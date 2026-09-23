"""Truy cập CSDL. Mọi giá trị ở đây là SI (m, m/s, độ) — quy đổi sang đơn vị dây chỉ ở link/messages.py."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (AppUser, AuditLog, Base, Drone, LinkStatRow, Mission, MissionEvent, MissionWaypoint, Site,
                     SiteArea, SiteAreaVertex, SystemConfig, TagPoint, TelemetrySample, utcnow)

# Bảng ngưỡng phải khớp safety.yaml phía Pi 4 (mục 10.1)
DEFAULT_CONFIG = {
    "low_battery_pct": "25.0",
    "critical_battery_pct": "15.0",
    "link_lost_timeout_s": "10.0",
    "marker_search_timeout_s": "20.0",
    "grip_confirm_timeout_s": "3.0",
    "max_retries": "3",
    "takeoff_alt_m": "5.0",
    "acceptance_radius_m": "1.5",
}

TAG_FIELDS = ("tag_id", "label", "pos_n_m", "pos_e_m", "pos_d_m", "yaw_deg", "tag_size_m", "kind", "landing_tol_m",
              "enabled", "notes")
SITE_FIELDS = ("name", "origin_lat", "origin_lon", "origin_alt_m", "yaw_offset_deg", "design_radius_m",
               "link_radius_meas_m", "cruise_alt_default_m", "gcs_pos_n_m", "gcs_pos_e_m", "bg_image_path",
               "bg_anchor_json")
WP_FIELDS = ("seq", "tag_id", "pos_n_m", "pos_e_m", "pos_d_m", "yaw_deg", "action", "accept_radius_m", "max_vel_mps",
             "loiter_s", "require_tag_lock", "precision_land")


def hash_password(pw: str, salt: bytes | None = None, iters: int = 200_000) -> str:
    salt = salt or os.urandom(16)
    return f"pbkdf2${iters}${salt.hex()}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, iters).hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, h = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters)).hex()
    return hmac.compare_digest(calc, h)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def tag_dict(t: TagPoint) -> dict:
    d = {k: getattr(t, k) for k in TAG_FIELDS}
    d["id"], d["site_id"], d["enabled"] = t.id, t.site_id, bool(t.enabled)
    return d


def area_dict(a: SiteArea) -> dict:
    return {"id": a.id, "kind": a.kind, "name": a.name, "min_alt_m": a.min_alt_m, "max_alt_m": a.max_alt_m,
            "enabled": bool(a.enabled), "notes": a.notes,
            "vertices": [[v.pos_n_m, v.pos_e_m] for v in a.vertices]}


def site_dict(s: Site) -> dict:
    d = {k: getattr(s, k) for k in SITE_FIELDS}
    d["id"] = s.id
    d["bg_anchors"] = json.loads(s.bg_anchor_json) if s.bg_anchor_json else None
    d.pop("bg_anchor_json")
    return d


def mission_dict(mi: Mission, with_wps: bool = True) -> dict:
    d = {"id": mi.id, "drone_id": mi.drone_id, "requested_by": mi.requested_by, "plan_name": mi.plan_name,
         "state": mi.state, "pickup_tag": mi.pickup_tag, "dropoff_tag": mi.dropoff_tag, "priority": mi.priority,
         "map_crc": mi.map_crc, "cruise_alt_m": mi.cruise_alt_m, "max_vel_mps": mi.max_vel_mps,
         "created_at": _iso(mi.created_at), "started_at": _iso(mi.started_at), "finished_at": _iso(mi.finished_at),
         "result": mi.result, "fail_reason": mi.fail_reason}
    if with_wps:
        d["waypoints"] = [{**{k: getattr(w, k) for k in WP_FIELDS},
                           "require_tag_lock": bool(w.require_tag_lock), "precision_land": bool(w.precision_land),
                           "reached_at": _iso(w.reached_at)} for w in mi.waypoints]
    return d


class Database:
    def __init__(self, url: str) -> None:
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragma(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def s(self) -> Session:
        return self.Session()

    # ── khởi tạo ─────────────────────────────────────────────────────────────────────────────

    def init(self, settings) -> None:
        Base.metadata.create_all(self.engine)
        with self.s() as s, s.begin():
            if s.scalar(select(AppUser).limit(1)) is None:
                s.add(AppUser(username=settings.admin_user, password_hash=hash_password(settings.admin_password),
                              role="admin"))
                s.add(AppUser(username=settings.operator_user,
                              password_hash=hash_password(settings.operator_password), role="operator"))
            if s.get(Drone, settings.drone_id) is None:
                # esp_peer_mac: cột của kênh ESP-NOW cũ, kênh MAVLink không dùng địa chỉ MAC.
                # Giữ vì bỏ cột cần migration; điền giá trị trung tính cho ràng buộc unique.
                s.add(Drone(id=settings.drone_id, name="Drone 1",
                            esp_peer_mac=f"mavlink:{settings.drone_id}"))
            for k, v in DEFAULT_CONFIG.items():
                if s.get(SystemConfig, k) is None:
                    s.add(SystemConfig(key=k, value=v))
            if s.get(Site, settings.site_id) is None:
                self._seed_demo_site(s, settings.site_id)

    @staticmethod
    def _seed_demo_site(s: Session, site_id: int) -> None:
        """Khu vực mẫu để chạy thử T0 ngay: Home + 2 điểm lấy + 2 điểm giao + vùng bay + 1 vùng cấm."""
        s.add(Site(id=site_id, name="Bãi thử T0", cruise_alt_default_m=5.0, design_radius_m=60.0))
        s.flush()
        for tag_id, label, n, e, kind in [(0, "Home", 0, 0, "home"), (1, "A", 18, 12, "pickup"),
                                          (2, "B", -10, 25, "dropoff"), (3, "C", 25, -15, "pickup"),
                                          (4, "D", -20, -18, "dropoff")]:
            s.add(TagPoint(site_id=site_id, tag_id=tag_id, label=label, pos_n_m=n, pos_e_m=e, tag_size_m=0.30,
                           kind=kind, landing_tol_m=0.3))
        op = SiteArea(site_id=site_id, kind="operating", name="Vùng bay chính")
        op.vertices = [SiteAreaVertex(seq=i, pos_n_m=n, pos_e_m=e)
                       for i, (n, e) in enumerate([(35, -30), (35, 35), (-30, 35), (-30, -30)])]
        nf = SiteArea(site_id=site_id, kind="no_fly", name="Nhà kho")
        nf.vertices = [SiteAreaVertex(seq=i, pos_n_m=n, pos_e_m=e)
                       for i, (n, e) in enumerate([(5, -12), (5, -4), (-4, -4), (-4, -12)])]
        s.add_all([op, nf])

    # ── người dùng / kiểm toán ───────────────────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> dict | None:
        with self.s() as s, s.begin():
            u = s.scalar(select(AppUser).where(AppUser.username == username))
            if u is None or not verify_password(password, u.password_hash):
                return None
            u.last_login = utcnow()
            return {"id": u.id, "username": u.username, "role": u.role}

    def get_user(self, user_id: int) -> dict | None:
        with self.s() as s:
            u = s.get(AppUser, user_id)
            return None if u is None else {"id": u.id, "username": u.username, "role": u.role}

    def list_users(self) -> list[dict]:
        with self.s() as s:
            return [{"id": u.id, "username": u.username, "role": u.role, "created_at": _iso(u.created_at),
                     "last_login": _iso(u.last_login)} for u in s.scalars(select(AppUser).order_by(AppUser.id))]

    def create_user(self, username: str, password: str, role: str) -> dict:
        with self.s() as s, s.begin():
            u = AppUser(username=username, password_hash=hash_password(password), role=role)
            s.add(u)
            s.flush()
            return {"id": u.id, "username": u.username, "role": u.role}

    def delete_user(self, user_id: int) -> bool:
        with self.s() as s, s.begin():
            u = s.get(AppUser, user_id)
            if u is None:
                return False
            s.delete(u)
            return True

    def audit(self, user_id: int | None, action: str, target: str | None = None, detail: Any = None) -> None:
        with self.s() as s, s.begin():
            s.add(AuditLog(user_id=user_id, action=action, target=target,
                           detail_json=None if detail is None else json.dumps(detail, ensure_ascii=False)))

    def list_audit(self, limit: int = 200) -> list[dict]:
        with self.s() as s:
            rows = s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
            return [{"id": r.id, "user_id": r.user_id, "t_utc": _iso(r.t_utc), "action": r.action,
                     "target": r.target, "detail": json.loads(r.detail_json) if r.detail_json else None}
                    for r in rows]

    # ── khu vực / tag / vùng ─────────────────────────────────────────────────────────────────

    def get_site(self, site_id: int) -> dict | None:
        with self.s() as s:
            st = s.get(Site, site_id)
            return None if st is None else site_dict(st)

    def list_sites(self) -> list[dict]:
        with self.s() as s:
            return [site_dict(x) for x in s.scalars(select(Site).order_by(Site.id))]

    def list_tags(self, site_id: int) -> list[dict]:
        with self.s() as s:
            return [tag_dict(t) for t in s.scalars(select(TagPoint).where(TagPoint.site_id == site_id)
                                                  .order_by(TagPoint.tag_id))]

    def get_tag(self, pk: int) -> dict | None:
        with self.s() as s:
            t = s.get(TagPoint, pk)
            return None if t is None else tag_dict(t)

    def create_tag(self, site_id: int, data: dict) -> dict:
        with self.s() as s, s.begin():
            if data.get("kind") == "home":
                self._demote_home(s, site_id, None)
            t = TagPoint(site_id=site_id, **{k: data[k] for k in TAG_FIELDS if k in data})
            s.add(t)
            s.flush()
            return tag_dict(t)

    def update_tag(self, pk: int, data: dict) -> dict | None:
        with self.s() as s, s.begin():
            t = s.get(TagPoint, pk)
            if t is None:
                return None
            if data.get("kind") == "home":
                self._demote_home(s, t.site_id, pk)
            for k in TAG_FIELDS:
                if k in data:
                    setattr(t, k, int(data[k]) if k == "enabled" else data[k])
            s.flush()
            return tag_dict(t)

    @staticmethod
    def _demote_home(s: Session, site_id: int, keep_pk: int | None) -> None:
        """Mỗi khu vực đúng MỘT Home; đặt Home mới thì Home cũ hạ thành điểm thường (8.4.2)."""
        for old in s.scalars(select(TagPoint).where(TagPoint.site_id == site_id, TagPoint.kind == "home")):
            if old.id != keep_pk:
                old.kind = "waypoint"

    def delete_tag(self, pk: int) -> bool:
        with self.s() as s, s.begin():
            t = s.get(TagPoint, pk)
            if t is None:
                return False
            s.delete(t)
            return True

    def list_areas(self, site_id: int) -> list[dict]:
        with self.s() as s:
            return [area_dict(a) for a in s.scalars(select(SiteArea).where(SiteArea.site_id == site_id)
                                                   .order_by(SiteArea.id))]

    @staticmethod
    def _replace_areas(s: Session, site_id: int, areas: list[dict]) -> None:
        s.execute(delete(SiteArea).where(SiteArea.site_id == site_id))
        for a in areas:
            row = SiteArea(site_id=site_id, kind=a["kind"], name=a.get("name") or a["kind"],
                           min_alt_m=a.get("min_alt_m", 0.0), max_alt_m=a.get("max_alt_m", 20.0),
                           enabled=int(a.get("enabled", True)), notes=a.get("notes"))
            row.vertices = [SiteAreaVertex(seq=i, pos_n_m=v[0], pos_e_m=v[1]) for i, v in enumerate(a["vertices"])]
            s.add(row)

    def replace_areas(self, site_id: int, areas: list[dict]) -> list[dict]:
        with self.s() as s, s.begin():
            self._replace_areas(s, site_id, areas)
        return self.list_areas(site_id)

    def get_design(self, site_id: int) -> dict | None:
        site = self.get_site(site_id)
        if site is None:
            return None
        return {"site": site, "tags": self.list_tags(site_id), "areas": self.list_areas(site_id)}

    def save_design(self, site_id: int, design: dict) -> dict:
        """Lưu cả gói thiết kế trong MỘT giao dịch (8.4.5) — không có trạng thái nửa vời."""
        with self.s() as s, s.begin():
            st = s.get(Site, site_id)
            if st is None:
                st = Site(id=site_id, name=design["site"].get("name") or f"Khu vực {site_id}")
                s.add(st)
            for k in SITE_FIELDS:
                if k in design["site"] and k not in ("bg_image_path", "bg_anchor_json"):
                    setattr(st, k, design["site"][k])
            if "bg_anchors" in design["site"]:
                anchors = design["site"]["bg_anchors"]
                st.bg_anchor_json = json.dumps(anchors) if anchors else None
            s.execute(delete(TagPoint).where(TagPoint.site_id == site_id))
            s.flush()
            for t in design["tags"]:
                s.add(TagPoint(site_id=site_id, **{k: (int(t[k]) if k == "enabled" else t[k])
                                                   for k in TAG_FIELDS if k in t}))
            self._replace_areas(s, site_id, design["areas"])
        return self.get_design(site_id)  # type: ignore[return-value]

    def set_site_fields(self, site_id: int, **fields: Any) -> None:
        with self.s() as s, s.begin():
            st = s.get(Site, site_id)
            for k, v in fields.items():
                setattr(st, k, v)

    # ── nhiệm vụ ─────────────────────────────────────────────────────────────────────────────

    def create_mission(self, *, drone_id: int, user_id: int | None, plan_name: str, pickup_tag: int | None,
                       dropoff_tag: int | None, map_crc: int, cruise_alt_m: float, max_vel_mps: float,
                       priority: int, waypoints: list[dict]) -> dict:
        with self.s() as s, s.begin():
            mi = Mission(drone_id=drone_id, requested_by=user_id, plan_name=plan_name, state="queued",
                         pickup_tag=pickup_tag, dropoff_tag=dropoff_tag, priority=priority, map_crc=map_crc,
                         cruise_alt_m=cruise_alt_m, max_vel_mps=max_vel_mps)
            mi.waypoints = [MissionWaypoint(**{k: (int(w[k]) if k in ("require_tag_lock", "precision_land") else w[k])
                                               for k in WP_FIELDS if k in w}) for w in waypoints]
            s.add(mi)
            s.flush()
            return mission_dict(mi)

    def get_mission(self, mission_id: int) -> dict | None:
        with self.s() as s:
            mi = s.get(Mission, mission_id)
            return None if mi is None else mission_dict(mi)

    def list_missions(self, *, state: str | None = None, tag: int | None = None, t_from: str | None = None,
                      t_to: str | None = None, limit: int = 200) -> list[dict]:
        with self.s() as s:
            q = select(Mission)
            if state:
                q = q.where(Mission.state.in_(state.split(",")))
            if tag is not None:
                q = q.where((Mission.pickup_tag == tag) | (Mission.dropoff_tag == tag))
            if t_from:
                q = q.where(Mission.created_at >= datetime.fromisoformat(t_from))
            if t_to:
                q = q.where(Mission.created_at <= datetime.fromisoformat(t_to))
            q = q.order_by(Mission.id.desc()).limit(limit)
            return [mission_dict(x, with_wps=False) for x in s.scalars(q)]

    def update_mission(self, mission_id: int, **fields: Any) -> dict | None:
        with self.s() as s, s.begin():
            mi = s.get(Mission, mission_id)
            if mi is None:
                return None
            for k, v in fields.items():
                setattr(mi, k, v)
            s.flush()
            return mission_dict(mi)

    def mark_waypoint_reached(self, mission_id: int, seq: int) -> None:
        with self.s() as s, s.begin():
            w = s.scalar(select(MissionWaypoint).where(MissionWaypoint.mission_id == mission_id,
                                                       MissionWaypoint.seq == seq))
            if w is not None and w.reached_at is None:
                w.reached_at = utcnow()

    def queue(self, drone_id: int) -> list[dict]:
        with self.s() as s:
            q = select(Mission).where(Mission.drone_id == drone_id,
                                      Mission.state.in_(("queued", "uploading", "ready", "running"))) \
                .order_by(Mission.priority, Mission.id)
            return [mission_dict(x, with_wps=False) for x in s.scalars(q)]

    # ── telemetry / sự kiện / thống kê liên kết ─────────────────────────────────────────────

    def insert_samples(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self.s() as s, s.begin():
            s.execute(TelemetrySample.__table__.insert(), rows)

    def get_telemetry(self, mission_id: int, rate_hz: float | None = None) -> list[dict]:
        with self.s() as s:
            rows = s.execute(select(TelemetrySample.__table__).where(TelemetrySample.mission_id == mission_id)
                             .order_by(TelemetrySample.t_utc)).mappings().all()
        out, last_t = [], None
        min_dt = 1.0 / rate_hz if rate_hz else 0.0
        for r in rows:
            if last_t is not None and r["t_utc"] - last_t < min_dt * 0.95:
                continue
            last_t = r["t_utc"]
            out.append({k: r[k] for k in ("t_utc", "t_drone_ms", "pos_n", "pos_e", "pos_d", "vel_n", "vel_e",
                                          "vel_d", "roll", "pitch", "yaw", "fsm_state", "flags", "wp_index",
                                          "battery_pct")})
        return out

    def add_event(self, *, mission_id: int | None, severity: int, category: str, message: str,
                  code: int | None = None, t_drone_ms: int | None = None, payload: Any = None) -> dict:
        with self.s() as s, s.begin():
            e = MissionEvent(mission_id=mission_id, t_utc=time.time(), t_drone_ms=t_drone_ms, severity=severity,
                             category=category, code=code, message=message,
                             payload_json=None if payload is None else json.dumps(payload, ensure_ascii=False))
            s.add(e)
            s.flush()
            return self._event_dict(e)

    @staticmethod
    def _event_dict(e: MissionEvent) -> dict:
        return {"id": e.id, "mission_id": e.mission_id, "t_utc": e.t_utc, "t_drone_ms": e.t_drone_ms,
                "severity": e.severity, "category": e.category, "code": e.code, "message": e.message,
                "payload": json.loads(e.payload_json) if e.payload_json else None}

    def list_events(self, mission_id: int | None = None, limit: int = 300) -> list[dict]:
        with self.s() as s:
            q = select(MissionEvent)
            if mission_id is not None:
                q = q.where(MissionEvent.mission_id == mission_id).order_by(MissionEvent.t_utc)
            else:
                q = q.order_by(MissionEvent.id.desc()).limit(limit)
            return [self._event_dict(e) for e in s.scalars(q)]

    def add_link_stat(self, drone_id: int, **fields: Any) -> None:
        with self.s() as s, s.begin():
            s.add(LinkStatRow(drone_id=drone_id, t_utc=time.time(), **fields))

    def link_stats(self, drone_id: int, since_s: float = 3600) -> list[dict]:
        with self.s() as s:
            q = select(LinkStatRow).where(LinkStatRow.drone_id == drone_id,
                                          LinkStatRow.t_utc >= time.time() - since_s).order_by(LinkStatRow.t_utc)
            return [{"t_utc": r.t_utc, "tx_count": r.tx_count, "rx_count": r.rx_count, "lost_count": r.lost_count,
                     "rtt_ms_avg": r.rtt_ms_avg, "rssi_dbm_avg": r.rssi_dbm_avg} for r in s.scalars(q)]

    # ── cấu hình hệ thống ────────────────────────────────────────────────────────────────────

    def get_config(self) -> list[dict]:
        with self.s() as s:
            return [{"key": c.key, "value": c.value, "drone_value": c.drone_value, "in_sync": c.in_sync,
                     "last_checked": _iso(c.last_checked), "updated_at": _iso(c.updated_at)}
                    for c in s.scalars(select(SystemConfig).order_by(SystemConfig.key))]

    def config_map(self) -> dict[str, float]:
        return {c["key"]: float(c["value"]) for c in self.get_config()}

    def set_config(self, key: str, value: str, user_id: int | None) -> bool:
        with self.s() as s, s.begin():
            c = s.get(SystemConfig, key)
            if c is None:
                return False
            c.value, c.updated_by, c.updated_at = value, user_id, utcnow()
            if c.drone_value is not None:
                c.in_sync = int(_same(c.value, c.drone_value))
            return True

    def set_drone_value(self, key: str, value: str) -> None:
        with self.s() as s, s.begin():
            c = s.get(SystemConfig, key)
            if c is None:
                return
            c.drone_value, c.last_checked, c.in_sync = value, utcnow(), int(_same(c.value, value))


def _same(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-3
    except ValueError:
        return a == b
