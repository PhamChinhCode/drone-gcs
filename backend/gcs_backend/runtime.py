"""Bộ khung chạy nền của backend: nối link ↔ shadow ↔ kiểm chứng lệnh ↔ cảnh báo ↔ CSDL ↔ WebSocket.

API REST gọi vào các phương thức nghiệp vụ ở đây; không module nào khác chạm LinkSession trực tiếp.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

from .api.ws import WsHub
from .bus import EventBus
from .config import Settings
from .data.repo import Database
from .link import messages as m
from .link.auth import emergency_confirm_code
from .link.session import LinkSession, LinkTimeout, SendCancelled
from .mission import planner
from .mission.shadow import CommandTracker, DroneShadow, TrackedCommand, make_verifier
from .mission.uploader import UploadError, sync_tagmap, upload_mission
from .safety.config_check import ConfigChecker
from .safety.monitor import SafetyMonitor
from .safety.thresholds import Thresholds
from .sitedesign.validator import has_errors, has_warnings, validate_design

log = logging.getLogger(__name__)
MS = m.MissionState
RENDER_DELAY_MS = 150  # hằng số hiển thị ở frontend — trả qua API để màn chẩn đoán hiện được (9.4)


class ApiError(Exception):
    def __init__(self, status: int, message: str, detail=None):
        super().__init__(message)
        self.status, self.message, self.detail = status, message, detail


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.db_url)
        self.bus = EventBus()
        self.link = LinkSession(settings.link_url, self.bus, settings.session_key_bytes)
        self.shadow = DroneShadow(settings.drone_id)
        self.hub = WsHub(settings.ws_batch_ms)
        self.tracker = CommandTracker(self.shadow, lambda c: self.hub.push("command", c.as_dict()))
        self.monitor = SafetyMonitor(self._on_alert)
        self.config_checker = ConfigChecker(self.db, self.link, self._on_drift)
        self.site_id = settings.site_id
        self.tags: list[dict] = []
        self.areas: list[dict] = []
        self.gcs_map_crc = 0
        self.th = Thresholds()
        self.active_mission_id: int | None = None
        self._interrupted: str | None = None
        self._last_wp_index = -1
        self._telem_buf: list[dict] = []
        self._last_store = 0.0
        self._upload_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self.link.map_crc_provider = lambda: self.gcs_map_crc
        self.bus.subscribe("rx", self._on_rx)
        self.bus.subscribe("link.", self._on_link)

    # ── vòng đời ─────────────────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.db.init(self.settings)
        self.reload_site()
        self.reload_thresholds()
        self._tasks = [asyncio.create_task(t) for t in (self.link.run(), self.hub.run(), self._periodic(),
                                                         self._flush_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await asyncio.to_thread(self.db.insert_samples, self._telem_buf)

    def reload_site(self) -> None:
        self.tags = self.db.list_tags(self.site_id)
        self.areas = self.db.list_areas(self.site_id)
        self.gcs_map_crc = m.compute_map_crc(self.tags)
        self._check_map()

    def reload_thresholds(self) -> None:
        self.th = Thresholds.from_map(self.db.config_map())

    # ── trạng thái tổng hợp ─────────────────────────────────────────────────────────────────

    def map_status(self) -> dict:
        drone = self.link.state.drone_map_crc
        return {"gcs_crc": self.gcs_map_crc, "drone_crc": drone, "in_sync": drone is not None and drone == self.gcs_map_crc}

    def snapshot(self) -> dict:
        return {
            "shadow": self.shadow.snapshot(),
            "link": self.link.status(),
            "map": self.map_status(),
            "alerts": self.monitor.snapshot()["active"],
            "active_mission": self.db.get_mission(self.active_mission_id) if self.active_mission_id else None,
            "commands": self.tracker.recent(20),
            "thresholds": self.th.__dict__,
            "render_delay_ms": RENDER_DELAY_MS,
            "site_id": self.site_id,
        }

    def _check_map(self) -> None:
        st = self.map_status()
        if st["drone_crc"] is None:
            return
        self.monitor.set_map_mismatch(not st["in_sync"], st["gcs_crc"], st["drone_crc"])
        self.hub.push("map", st)

    def _require_link(self) -> None:
        if self.link.link_state() != "ok":
            raise ApiError(503, f"liên kết không sẵn sàng ({self.link.link_state()})")

    def _require_map_sync(self) -> None:
        if not self.map_status()["in_sync"]:
            raise ApiError(409, "bản đồ tag GCS và drone lệch nhau — chặn lệnh nhiệm vụ, hãy đồng bộ lại (3.3)",
                           self.map_status())

    # ── nhận từ bus ─────────────────────────────────────────────────────────────────────────

    def _on_alert(self, alert: dict, action: str) -> None:
        self.hub.push("alert", {"action": action, "alert": alert})
        if action == "raise":
            self.db.add_event(mission_id=self.active_mission_id, severity=alert["severity"],
                              category=alert["category"], message=alert["message"], payload={"code": alert["code"]})

    def _on_drift(self, drift: list[dict]) -> None:
        self.monitor.set_drift(drift)
        self.hub.push("config_drift", {"drift": drift})

    def _on_link(self, topic: str, data: dict) -> None:
        if topic == "link.status":
            st = data["status"]
            self.hub.push("link", st)
            self.monitor.evaluate_link(st["state"])
            if st["state"] != "ok" and self.shadow.stale_since is None:
                self.shadow.stale_since = time.monotonic()
            elif st["state"] == "ok":
                self.shadow.stale_since = None
        elif topic == "link.state_changed" and data["new"] == "ok":
            # nối lại: đối chiếu cấu hình + xin trạng thái nhiệm vụ để dựng lại shadow (10.1, 6.2)
            self.config_checker.request()
            asyncio.get_running_loop().create_task(self._request_state())
        elif topic == "link.state_changed":
            self.db.add_event(mission_id=self.active_mission_id, severity=1, category="link",
                              message=f"liên kết {data['old'] or '—'} → {data['new']}")
        elif topic == "link.drone_map_crc":
            self._check_map()
        elif topic == "link.emergency_unacked":
            self.monitor.raise_("EMERGENCY_UNACKED", 3, "command",
                                "Lệnh khẩn cấp chưa có ACK sau 8 lần — đang phát lại mỗi 500 ms")
        elif topic == "link.retry":
            self.hub.push("link_retry", data)

    async def _request_state(self) -> None:
        try:
            await self.link.send(m.Request(m.RequestWhat.MISSION_STATE))
        except LinkTimeout:
            pass

    def _on_rx(self, topic: str, data: dict) -> None:
        msg, now = data["msg"], data["t"]
        t_gcs = time.time() * 1000
        if isinstance(msg, m.TelemFast):
            si = msg.to_si()
            si["t_gcs"] = t_gcs
            self.shadow.apply_fast(si, now)
            self.hub.push("telem_fast", si)
            self.monitor.evaluate_fast(si, self.th, self.areas)
            self._evaluate_commands()
            self._record(si)
        elif isinstance(msg, m.TelemSlow):
            si = msg.to_si()
            si["t_gcs"] = t_gcs
            self.shadow.slow, self.shadow.slow_rx = si, now
            self.hub.push("telem_slow", si)
        elif isinstance(msg, m.MissionStateMsg):
            si = msg.to_si()
            changed = self.shadow.apply_mission_state(si, now)
            self._mission_lifecycle(si)
            snap = self.shadow.snapshot()
            self.hub.push("mission_state", {k: v for k, v in snap.items() if k not in ("fast", "slow", "tag_detect")}
                          | {"changed": changed})
            self._evaluate_commands()
        elif isinstance(msg, m.TagDetect):
            si = msg.to_si()
            si["t_gcs"] = t_gcs
            self.shadow.tag_detect, self.shadow.tag_detect_rx = si, now
            self.hub.push("tag_detect", si)
        elif isinstance(msg, m.Event):
            si = msg.to_si()
            ev = self.db.add_event(mission_id=self.active_mission_id, severity=si["severity"],
                                   category=si["category"], message=si["message"], code=si["code"],
                                   t_drone_ms=si["t_ms"])
            self.hub.push("event", ev)
            if si["severity"] >= 2:
                self.monitor.raise_(f"DRONE_EVENT_{si['category'].upper()}_{si['code']}", si["severity"],
                                    si["category"], f"Drone: {si['message']}", transient_s=60)
        elif isinstance(msg, m.ParamValue):
            self.config_checker.on_param(msg)

    def _evaluate_commands(self) -> None:
        for c in self.tracker.evaluate():
            self.monitor.raise_(f"CMD_NOT_EFFECTIVE_{c.id}", 3, "command",
                                f"Lệnh {c.kind}/{c.action} đã nhận (ACK) nhưng không có hiệu lực sau "
                                f"{c.verify_window_s:g} s", transient_s=120)

    def _record(self, si: dict) -> None:
        hz = self.settings.telemetry_store_hz
        now = time.time()
        if hz < 10 and now - self._last_store < 1 / hz * 0.95:
            return
        self._last_store = now
        mission = self.active_mission_id
        self._telem_buf.append({
            "mission_id": mission, "drone_id": self.settings.drone_id, "t_utc": si["t_gcs"] / 1000,
            "t_drone_ms": si["t_ms"], "pos_n": si["pos"][0], "pos_e": si["pos"][1], "pos_d": si["pos"][2],
            "vel_n": si["vel"][0], "vel_e": si["vel"][1], "vel_d": si["vel"][2], "roll": si["att"][0],
            "pitch": si["att"][1], "yaw": si["att"][2], "fsm_state": si["fsm_state"], "flags": si["flags"],
            "wp_index": si["wp_index"], "battery_pct": si["battery_pct"]})

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            buf, self._telem_buf = self._telem_buf, []
            if buf:
                try:
                    await asyncio.to_thread(self.db.insert_samples, buf)
                except Exception:
                    log.exception("ghi telemetry lỗi")

    async def _periodic(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(1.0)
            n += 1
            self._evaluate_commands()
            self.monitor.tick()
            if n % 10 == 0:
                st = self.link.status()
                d = st["dongle"]
                if d:
                    self.db.add_link_stat(self.settings.drone_id, window_s=10, tx_count=d.get("tx_count"),
                                          rx_count=d.get("rx_count"), lost_count=d.get("tx_fail"),
                                          rtt_ms_avg=st["rtt_ms_avg"], rssi_dbm_avg=d.get("last_rssi_dbm"))

    # ── vòng đời nhiệm vụ theo shadow ─────────────────────────────────────────────────────

    def _mission_lifecycle(self, si: dict) -> None:
        wire_id, fsm = si["mission_id"], si["fsm_state"]
        if self.active_mission_id is None:
            # khôi phục sau khi GCS khởi động lại (N5): shadow dựng lại từ MISSION_STATE, CSDL chỉ để đối chiếu
            for cand in self.db.list_missions(state="ready,running", limit=20):
                if wire_id and cand["id"] & 0xFFFF == wire_id:
                    self.active_mission_id = cand["id"]
                    self.db.add_event(mission_id=cand["id"], severity=0, category="mission",
                                      message="khôi phục theo dõi nhiệm vụ từ MISSION_STATE sau khi GCS khởi động lại")
                elif cand["state"] == "running":
                    self._finish(cand["id"], "failed", "failed",
                                 f"sau khi nối lại, drone báo mission_id {wire_id} — không còn chạy nhiệm vụ này")
        mid = self.active_mission_id
        if mid is None:
            return
        mission = self.db.get_mission(mid)
        if mission is None:
            return
        if wire_id != mid & 0xFFFF:
            # Chỉ xét khi đã running: lúc vừa tải lên, MISSION_STATE cũ có thể đến sau ACK của END
            # (hàng đợi ưu tiên phía drone đảo thứ tự).
            if mission["state"] == "running":
                # shadow thắng kế hoạch cục bộ (6.2 quy tắc 2): drone không còn giữ nhiệm vụ này
                # (ví dụ Pi 4 khởi động lại giữa chừng) → nhiệm vụ coi như thất bại.
                self._finish(mid, "failed", "failed",
                             f"shadow báo drone đang giữ mission_id {wire_id}, không phải {mid & 0xFFFF}")
            return
        state = mission["state"]
        if state == "ready" and fsm not in (MS.IDLE, MS.MISSION_COMPLETE):
            self.db.update_mission(mid, state="running", started_at=_now_utc())
            self._interrupted, self._last_wp_index = None, -1
            state = "running"
            self.hub.push("mission_update", {"id": mid, "state": "running"})
        if state != "running":
            return
        if si["wp_index"] > self._last_wp_index:
            for seq in range(max(0, self._last_wp_index), si["wp_index"]):
                self.db.mark_waypoint_reached(mid, seq)
            self._last_wp_index = si["wp_index"]
        if fsm == MS.MISSION_COMPLETE:
            for seq in range(max(0, self._last_wp_index), si["wp_total"]):
                self.db.mark_waypoint_reached(mid, seq)
            self._finish(mid, "done", "ok", None)
        elif fsm in (MS.RTH, MS.EMERGENCY_LAND, MS.FAILSAFE) and self._interrupted is None:
            fs = m.FailsafeType(si["failsafe_type"]).name if si["failsafe_type"] else None
            self._interrupted = f"failsafe {fs}" if fs else f"chuyển {MS(fsm).name}"
        elif fsm == MS.IDLE and self._interrupted:
            aborted = self._interrupted.startswith("operator")
            self._finish(mid, "aborted" if aborted else "failed", "aborted" if aborted else "failed", self._interrupted)

    def _finish(self, mid: int, state: str, result: str, reason: str | None) -> None:
        self.db.update_mission(mid, state=state, finished_at=_now_utc(), result=result, fail_reason=reason)
        self.db.add_event(mission_id=mid, severity=0 if state == "done" else 2, category="mission",
                          message=f"nhiệm vụ {mid} kết thúc: {state}" + (f" ({reason})" if reason else ""))
        self.hub.push("mission_update", {"id": mid, "state": state})
        self.active_mission_id, self._interrupted = None, None

    # ── lệnh (F2, F5) ───────────────────────────────────────────────────────────────────────

    async def cmd_simple(self, action: str, param: float | None, user: dict) -> dict:
        try:
            act = m.SimpleAction[action]
        except KeyError:
            raise ApiError(400, f"action không hợp lệ: {action}")
        wire_param = 0
        if act == m.SimpleAction.TAKEOFF:
            wire_param = round((param or self.th.takeoff_alt_m) * 100)
        elif act == m.SimpleAction.PRECISION_LAND:
            if param is None or not any(t["tag_id"] == int(param) for t in self.tags):
                raise ApiError(400, "PRECISION_LAND cần tag_id có trong bản đồ")
            wire_param = int(param)
        c = self.tracker.new("simple", action, user["id"])
        self.db.audit(user["id"], "command.simple", action, {"param": param, "cmd_id": c.id})
        verifier = make_verifier("simple", action, self.shadow)
        await self._send_tracked(c, m.CmdSimple(c.id & 0xFFFF, act, 0, wire_param), verifier)
        return c.as_dict()

    def resolve_goto_target(self, ref_frame: str, ref_tag_id: int | None, x: float, y: float, z: float) -> list[float]:
        if ref_frame == "MAP_NED":
            return [x, y, z]
        if ref_frame == "TAG_RELATIVE":
            t = next((t for t in self.tags if t["tag_id"] == ref_tag_id), None)
            if t is None:
                raise ApiError(400, f"không có tag {ref_tag_id}")
            return [t["pos_n_m"] + x, t["pos_e_m"] + y, t.get("pos_d_m", 0.0) + z]
        if ref_frame == "BODY_RELATIVE":
            if not self.shadow.fast:
                raise ApiError(409, "chưa có telemetry để quy đổi hệ thân")
            p, yaw = self.shadow.fast["pos"], math.radians(self.shadow.fast["att"][2])
            return [p[0] + math.cos(yaw) * x - math.sin(yaw) * y, p[1] + math.sin(yaw) * x + math.cos(yaw) * y,
                    p[2] + z]
        raise ApiError(400, f"ref_frame không hợp lệ: {ref_frame}")

    async def cmd_goto(self, body: dict, user: dict) -> dict:
        frame = body["ref_frame"]
        target = self.resolve_goto_target(frame, body.get("ref_tag_id"), body["x"], body["y"], body["z"])
        warnings = planner.plan_warnings([{"seq": 0, "pos_n_m": target[0], "pos_e_m": target[1]}], self.areas)
        c = self.tracker.new("goto", frame, user["id"])
        self.db.audit(user["id"], "command.goto", frame, {**body, "target_ned": target, "cmd_id": c.id})
        msg = m.CmdGoto.from_si(c.id & 0xFFFF, int(m.RefFrame[frame]), body.get("ref_tag_id"), body["x"], body["y"],
                                body["z"], body.get("yaw"), body.get("max_vel") or 2.0, bool(body.get("climb_first")))
        verifier = make_verifier("goto", frame, self.shadow, target)
        await self._send_tracked(c, msg, verifier)
        return c.as_dict() | {"target_ned": target, "warnings": warnings}

    async def _send_tracked(self, c: TrackedCommand, msg: m.Message, verifier) -> None:
        try:
            ack = await self.link.send(msg, cancel=c.cancel)
        except LinkTimeout as e:
            self.tracker.set_stage(c, "failed", str(e))
            self.db.add_event(mission_id=self.active_mission_id, severity=2, category="command",
                              message=f"lệnh {c.kind}/{c.action} không có ACK: {e}")
            return
        except SendCancelled:
            self.tracker.set_stage(c, "cancelled", "người vận hành hủy")
            return
        self.tracker.acked(c, ack.as_dict(), verifier)
        if c.stage == "rejected":
            self.db.add_event(mission_id=self.active_mission_id, severity=2, category="command",
                              message=f"lệnh {c.kind}/{c.action} bị từ chối: {c.detail}")

    async def cmd_emergency(self, action: str, confirm_token: str | None, user: dict) -> dict:
        try:
            act = m.EmergencyAction[action]
        except KeyError:
            raise ApiError(400, f"action không hợp lệ: {action}")
        if act == m.EmergencyAction.KILL:
            if user["role"] != "admin":
                raise ApiError(403, "KILL chỉ dành cho admin")
            if confirm_token != "KILL":
                raise ApiError(400, "KILL cần gõ từ khóa xác nhận")
        c = self.tracker.new("emergency", action, user["id"])
        seq = c.id & 0xFFFF
        code = emergency_confirm_code(act, seq, self.settings.session_key_bytes) if act == m.EmergencyAction.KILL else 0
        self.db.audit(user["id"], "command.emergency", action, {"cmd_id": c.id})
        if act == m.EmergencyAction.ABORT_MISSION and self.active_mission_id:
            self._interrupted = "operator abort"
        verifier = make_verifier("emergency", action, self.shadow)

        async def run() -> None:
            await self._send_tracked(c, m.Emergency(seq, act, 0, code), verifier)
            self.monitor.clear("EMERGENCY_UNACKED")
        asyncio.get_running_loop().create_task(run())  # không chặn REST — tiến trình đẩy qua WS
        return c.as_dict()

    def cancel_command(self, cmd_id: int, user: dict) -> bool:
        c = self.tracker.commands.get(cmd_id)
        if c is None:
            return False
        c.cancel.set()
        self.db.audit(user["id"], "command.cancel", str(cmd_id))
        return True

    # ── nhiệm vụ (F3) ───────────────────────────────────────────────────────────────────────

    def create_mission(self, body: dict, user: dict) -> dict:
        cruise = body.get("cruise_alt_m") or self.th.takeoff_alt_m
        vel = body.get("max_vel_mps") or 3.0
        try:
            if body.get("stops"):
                wps = planner.plan_custom(self.tags, body["stops"], cruise_alt_m=cruise, max_vel_mps=vel,
                                          accept_radius_m=self.th.acceptance_radius_m)
                name = body.get("plan_name") or "Tuyến tùy biến"
            else:
                wps = planner.plan_pickup_dropoff(self.tags, int(body["pickup_tag"]), int(body["dropoff_tag"]),
                                                  cruise_alt_m=cruise, max_vel_mps=vel,
                                                  accept_radius_m=self.th.acceptance_radius_m)
                name = body.get("plan_name") or f"Lấy {body['pickup_tag']} → giao {body['dropoff_tag']}"
        except (planner.PlanError, KeyError, TypeError) as e:
            raise ApiError(400, f"không lập được kế hoạch: {e}")
        mission = self.db.create_mission(drone_id=self.settings.drone_id, user_id=user["id"], plan_name=name,
                                         pickup_tag=body.get("pickup_tag"), dropoff_tag=body.get("dropoff_tag"),
                                         map_crc=self.gcs_map_crc, cruise_alt_m=cruise, max_vel_mps=vel,
                                         priority=int(body.get("priority") or 100), waypoints=wps)
        self.db.audit(user["id"], "mission.create", str(mission["id"]), body)
        self.hub.push("mission_update", {"id": mission["id"], "state": mission["state"]})
        return mission | {"warnings": planner.plan_warnings(wps, self.areas)}

    async def upload(self, mission_id: int, user: dict) -> dict:
        mission = self.db.get_mission(mission_id)
        if mission is None:
            raise ApiError(404, "không có nhiệm vụ")
        if mission["state"] not in ("queued", "ready"):
            raise ApiError(409, f"không tải lên được nhiệm vụ ở trạng thái {mission['state']}")
        if self.active_mission_id and self.active_mission_id != mission_id:
            active = self.db.get_mission(self.active_mission_id)
            if active and active["state"] == "running":
                raise ApiError(409, f"nhiệm vụ {self.active_mission_id} đang chạy")
            if active and active["state"] == "ready":
                self.db.update_mission(active["id"], state="queued")
        if mission["map_crc"] != self.gcs_map_crc:
            raise ApiError(409, "bản đồ tag đã đổi từ lúc lập kế hoạch — hãy lập lại nhiệm vụ")
        self._require_link()
        self._require_map_sync()
        async with self._upload_lock:
            self.db.update_mission(mission_id, state="uploading", fail_reason=None)
            self.hub.push("mission_update", {"id": mission_id, "state": "uploading"})
            try:
                res = await upload_mission(self.link, mission, self.gcs_map_crc,
                                           progress=lambda i, n: self.hub.push("upload_progress",
                                                                               {"id": mission_id, "done": i, "total": n}))
            except UploadError as e:
                self.db.update_mission(mission_id, state="queued", fail_reason=f"tải lên lỗi: {e}")
                self.hub.push("mission_update", {"id": mission_id, "state": "queued"})
                self.db.add_event(mission_id=mission_id, severity=2, category="mission", message=f"tải lên lỗi: {e}")
                raise ApiError(502, f"tải lên lỗi ở {e.stage}: {e.reason}")
        self.db.update_mission(mission_id, state="ready")
        self.active_mission_id = mission_id
        self.db.audit(user["id"], "mission.upload", str(mission_id), res)
        self.db.add_event(mission_id=mission_id, severity=0, category="mission",
                          message=f"đã tải lên {res['total']} waypoint, wp_crc {res['wp_crc']:08X}")
        self.hub.push("mission_update", {"id": mission_id, "state": "ready"})
        return self.db.get_mission(mission_id) | {"upload": res}  # type: ignore[operator]

    async def mission_ctrl(self, mission_id: int, action: str, user: dict) -> dict:
        mission = self.db.get_mission(mission_id)
        if mission is None:
            raise ApiError(404, "không có nhiệm vụ")
        act = m.MissionCtrlAction[action.upper()]
        allowed = {m.MissionCtrlAction.START: ("ready",), m.MissionCtrlAction.PAUSE: ("running",),
                   m.MissionCtrlAction.RESUME: ("running",), m.MissionCtrlAction.ABORT: ("ready", "running")}
        if mission["state"] not in allowed[act]:
            if act == m.MissionCtrlAction.ABORT and mission["state"] == "queued":
                self.db.update_mission(mission_id, state="aborted", finished_at=_now_utc(), result="aborted",
                                       fail_reason="hủy trước khi tải lên")
                self.hub.push("mission_update", {"id": mission_id, "state": "aborted"})
                return {"id": mission_id, "state": "aborted"}
            raise ApiError(409, f"{act.name} không hợp lệ ở trạng thái {mission['state']}")
        if act == m.MissionCtrlAction.START:
            self._require_map_sync()
        self._require_link()
        if act == m.MissionCtrlAction.ABORT:
            self._interrupted = "operator abort"
        c = self.tracker.new("mission", act.name, user["id"])
        self.db.audit(user["id"], f"mission.{act.name.lower()}", str(mission_id), {"cmd_id": c.id})
        verifier = make_verifier("mission", act.name, self.shadow)
        await self._send_tracked(c, m.MissionCtrl(mission_id & 0xFFFF, act, 0, c.id & 0xFFFF), verifier)
        if act == m.MissionCtrlAction.ABORT and mission["state"] == "ready" and c.stage in ("acked", "effective"):
            self._finish(mission_id, "aborted", "aborted", "operator abort trước khi cất cánh")
        return c.as_dict()

    # ── bản đồ tag / thiết kế (F1, F8) ─────────────────────────────────────────────────────

    def validate(self, design: dict | None = None) -> list[dict]:
        return validate_design(design or self.db.get_design(self.site_id))  # type: ignore[arg-type]

    def save_design(self, site_id: int, design: dict, ack_warnings: bool, user: dict) -> dict:
        issues = validate_design(design)
        if has_errors(issues):
            raise ApiError(422, "thiết kế còn Lỗi — không lưu được", issues)
        if has_warnings(issues) and not ack_warnings:
            raise ApiError(409, "thiết kế còn Cảnh báo — cần xác nhận 'tôi đã hiểu'", issues)
        saved = self.db.save_design(site_id, design)
        self.db.audit(user["id"], "site.save_design", str(site_id),
                      {"warnings_acknowledged": [i["code"] for i in issues if i["level"] == "warning"],
                       "tags": len(design["tags"]), "areas": len(design["areas"])})
        if site_id == self.site_id:
            self.reload_site()
        self.hub.push("site_changed", {"site_id": site_id, "map": self.map_status()})
        return saved | {"issues": issues, "map": self.map_status()}

    def tags_changed(self) -> None:
        self.reload_site()
        self.hub.push("site_changed", {"site_id": self.site_id, "map": self.map_status()})

    async def sync_tags(self, user: dict) -> dict:
        issues = self.validate()
        if has_errors(issues):
            raise ApiError(422, "thiết kế còn Lỗi — chặn đồng bộ bản đồ xuống drone", issues)
        self._require_link()
        try:
            crc = await sync_tagmap(self.link, self.site_id, self.tags)
        except UploadError as e:
            raise ApiError(502, f"đồng bộ bản đồ lỗi ở {e.stage}: {e.reason}")
        # ACK OK của TAGMAP_END = drone đã kiểm và giữ đúng crc này; không chờ heartbeat kế tiếp
        self.link.state.drone_map_crc = crc
        self._check_map()
        self.db.audit(user["id"], "tags.sync", str(self.site_id), {"map_crc": crc})
        self.db.add_event(mission_id=None, severity=0, category="marker",
                          message=f"đồng bộ bản đồ {len(self.tags)} tag xuống drone, crc {crc:08X}")
        return {"map_crc": crc, "map": self.map_status()}

    def teach_tag(self, tag_pk: int, user: dict) -> dict:
        self._require_link()
        if not self.shadow.fast or (self.shadow.age() or 99) > 1.0:
            raise ApiError(409, "chưa có telemetry tươi để ghi vị trí")
        n, e, d = self.shadow.fast["pos"]
        fields = {"pos_n_m": round(n, 3), "pos_e_m": round(e, 3)}
        if abs(d) < 0.3:  # drone đang đỗ trên tag → lấy luôn cao độ bãi
            fields["pos_d_m"] = round(d, 3)
        tag = self.db.update_tag(tag_pk, fields)
        if tag is None:
            raise ApiError(404, "không có tag")
        self.db.audit(user["id"], "tags.teach", str(tag_pk), fields)
        self.tags_changed()
        return tag

    async def push_config(self, key: str, value: float) -> None:
        self._require_link()
        try:
            await self.config_checker.push(key, value)
        except LinkTimeout:
            raise ApiError(504, "PARAM_SET không có ACK")
