"""Lập kế hoạch nhiệm vụ (mục 6.1).

Quy tắc chốt: LUÔN tách pha tiếp cận ngang và pha hạ độ cao — bay tới tọa độ ngang của tag ở độ cao
hành trình, khóa tag rồi mới hạ. Không sinh waypoint đi chéo thẳng xuống bãi đáp.
Hệ bản đồ NED cục bộ: độ cao h mét trên mặt tag nghĩa là pos_d = tag.pos_d_m − h.
"""
from __future__ import annotations

from ..sitedesign.geometry import point_in_polygon


class PlanError(ValueError):
    pass


def _find(tags: list[dict], tag_id: int) -> dict:
    for t in tags:
        if t["tag_id"] == tag_id:
            if not t.get("enabled", True):
                raise PlanError(f"tag {tag_id} đang bị tắt")
            return t
    raise PlanError(f"không có tag {tag_id} trong bản đồ")


def _home(tags: list[dict]) -> dict:
    homes = [t for t in tags if t["kind"] == "home" and t.get("enabled", True)]
    if len(homes) != 1:
        raise PlanError("khu vực phải có đúng một tag Home")
    return homes[0]


def _wp(tag: dict, alt: float, *, tag_id: int | None = None, action: str = "none", lock: bool = False,
        land: bool = False, radius: float, vel: float) -> dict:
    return {"tag_id": tag_id, "pos_n_m": tag["pos_n_m"], "pos_e_m": tag["pos_e_m"],
            "pos_d_m": round(tag.get("pos_d_m", 0.0) - alt, 3), "yaw_deg": None, "action": action,
            "accept_radius_m": radius, "max_vel_mps": vel, "loiter_s": 0.0,
            "require_tag_lock": lock, "precision_land": land}


def plan_pickup_dropoff(tags: list[dict], pickup_tag: int, dropoff_tag: int, *, cruise_alt_m: float,
                        max_vel_mps: float, accept_radius_m: float, land_alt_m: float = 0.0) -> list[dict]:
    if pickup_tag == dropoff_tag:
        raise PlanError("tag lấy và tag giao phải khác nhau")
    if cruise_alt_m <= land_alt_m or cruise_alt_m <= 0:
        raise PlanError("độ cao hành trình phải lớn hơn độ cao hạ")
    home, a, b = _home(tags), _find(tags, pickup_tag), _find(tags, dropoff_tag)
    land_vel = min(0.5, max_vel_mps)
    wps = [
        _wp(home, cruise_alt_m, radius=accept_radius_m, vel=max_vel_mps),
        _wp(a, cruise_alt_m, tag_id=a["tag_id"], lock=True, radius=accept_radius_m, vel=max_vel_mps),
        _wp(a, land_alt_m, tag_id=a["tag_id"], land=True, action="pickup", radius=a["landing_tol_m"], vel=land_vel),
        _wp(a, cruise_alt_m, radius=accept_radius_m, vel=max_vel_mps),
        _wp(b, cruise_alt_m, tag_id=b["tag_id"], lock=True, radius=accept_radius_m, vel=max_vel_mps),
        _wp(b, land_alt_m, tag_id=b["tag_id"], land=True, action="dropoff", radius=b["landing_tol_m"], vel=land_vel),
        _wp(b, cruise_alt_m, radius=accept_radius_m, vel=max_vel_mps),
        _wp(home, cruise_alt_m, tag_id=home["tag_id"], radius=accept_radius_m, vel=max_vel_mps),
        _wp(home, land_alt_m, tag_id=home["tag_id"], land=True, radius=home["landing_tol_m"], vel=land_vel),
    ]
    for i, w in enumerate(wps):
        w["seq"] = i
    return wps


def plan_custom(tags: list[dict], stops: list[dict], *, cruise_alt_m: float, max_vel_mps: float,
                accept_radius_m: float) -> list[dict]:
    """Chuỗi điểm tùy biến: mỗi stop = {tag_id, land?, action?} hoặc {pos_n_m, pos_e_m}. Luôn kết thúc về Home."""
    if not stops:
        raise PlanError("chuỗi waypoint rỗng")
    home = _home(tags)
    land_vel = min(0.5, max_vel_mps)
    wps = [_wp(home, cruise_alt_m, radius=accept_radius_m, vel=max_vel_mps)]
    for s in stops:
        if s.get("tag_id") is not None:
            t = _find(tags, int(s["tag_id"]))
            land = bool(s.get("land"))
            wps.append(_wp(t, cruise_alt_m, tag_id=t["tag_id"], lock=land, radius=accept_radius_m, vel=max_vel_mps))
            if land:
                wps.append(_wp(t, 0.0, tag_id=t["tag_id"], land=True, action=s.get("action") or "none",
                               radius=t["landing_tol_m"], vel=land_vel))
                wps.append(_wp(t, cruise_alt_m, radius=accept_radius_m, vel=max_vel_mps))
        else:
            pt = {"pos_n_m": float(s["pos_n_m"]), "pos_e_m": float(s["pos_e_m"]), "pos_d_m": home.get("pos_d_m", 0.0)}
            wps.append(_wp(pt, cruise_alt_m, radius=accept_radius_m, vel=max_vel_mps))
    wps.append(_wp(home, cruise_alt_m, tag_id=home["tag_id"], radius=accept_radius_m, vel=max_vel_mps))
    wps.append(_wp(home, 0.0, tag_id=home["tag_id"], land=True, radius=home["landing_tol_m"], vel=land_vel))
    if len(wps) > 255:
        raise PlanError("quá 255 waypoint")
    for i, w in enumerate(wps):
        w["seq"] = i
    return wps


def plan_warnings(wps: list[dict], areas: list[dict]) -> list[str]:
    """Cảnh báo phía GCS (KHÔNG cưỡng chế — drone không biết vùng bay, mục 8.4.5)."""
    out = []
    operating = [a["vertices"] for a in areas if a["kind"] == "operating" and a.get("enabled", True)]
    no_fly = [(a["name"], a["vertices"]) for a in areas if a["kind"] == "no_fly" and a.get("enabled", True)]
    for w in wps:
        p = (w["pos_n_m"], w["pos_e_m"])
        if operating and not any(point_in_polygon(p, poly) for poly in operating):
            out.append(f"waypoint {w['seq']} nằm ngoài vùng bay")
    for i in range(len(wps) - 1):
        a, b = wps[i], wps[i + 1]
        for k in range(11):  # lấy mẫu dọc chặng
            p = (a["pos_n_m"] + (b["pos_n_m"] - a["pos_n_m"]) * k / 10, a["pos_e_m"] + (b["pos_e_m"] - a["pos_e_m"]) * k / 10)
            hit = next((name for name, poly in no_fly if point_in_polygon(p, poly)), None)
            if hit:
                out.append(f"chặng {i}→{i + 1} cắt qua vùng cấm '{hit}'")
                break
    return out
