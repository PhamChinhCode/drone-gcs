"""Lập kế hoạch nhiệm vụ (giao ước 3.2b, 8.3).

**Một mục kế hoạch = một điểm dừng theo marker**: bay tới → tìm marker → hạ chính xác → làm `action`
→ cất cánh lại. Pi làm cả chuỗi đó từ một mục, nên GCS **không** tách pha tiếp cận ngang và pha hạ
độ cao thành nhiều waypoint như bản ESP-NOW cũ — làm vậy là mô tả lại một việc mà FSM phía Pi đã
làm, và mỗi mục thừa lại thành một lần hạ cánh thật.

Hai hệ quả của 3.2b mà bộ lập kế hoạch phải tự lo, vì hợp đồng không thêm hành vi ngầm:

- **Kế hoạch không tự về home.** Xong mục cuối, drone hạ cánh **tại chỗ đó** rồi DISARM. Muốn về nhà
  thì mục cuối phải là tag home với `action = NONE` — thêm ở đây.
- **Vị trí suy từ `expected_marker_id`**, nên mọi điểm dừng phải là một tag có thật. Điểm theo toạ
  độ tự do không diễn đạt được trên kênh này nữa.

`pos_*` lưu trong CSDL chỉ để vẽ và để kiểm vùng cấm; thứ đi lên dây là `tag_id` và `alt_m`.
Quy ước độ cao: `pos_d_m = tag.pos_d_m − alt_m` (cao hơn tag `alt_m` mét).
"""
from __future__ import annotations

from ..sitedesign.geometry import point_in_polygon

MAX_ITEMS = 16  # giao ước 3.2 — Pi trả ERR_COUNT nếu quá; GCS chặn sớm vì đây là lỗi soạn thảo
# Cao hơn mức này camera (OV9281, tag 25 cm) không đọc được tag: bay thật 10-10 ở 5 m drone tìm
# 20 s không thấy, hết lượt thử lại. Ở 2 m tag ~70 px, 5 m ~15 px (dưới ngưỡng apriltag).
MAX_TAG_VISIBLE_ALT_M = 2.5


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


def _wp(tag: dict, alt_m: float, action: str, *, radius: float, vel: float, loiter_s: float = 0.0) -> dict:
    return {"tag_id": tag["tag_id"], "pos_n_m": tag["pos_n_m"], "pos_e_m": tag["pos_e_m"],
            "pos_d_m": round(tag.get("pos_d_m", 0.0) - alt_m, 3), "yaw_deg": None, "action": action,
            "accept_radius_m": radius, "max_vel_mps": vel, "loiter_s": loiter_s,
            "require_tag_lock": False, "precision_land": True}


def plan_stops(tags: list[dict], stops: list[dict], *, cruise_alt_m: float, max_vel_mps: float,
               accept_radius_m: float) -> list[dict]:
    """stops = [{tag_id, action?, alt_m?, loiter_s?}]. Tự thêm mục cuối là home với action NONE."""
    if not stops:
        raise PlanError("kế hoạch phải có ít nhất một điểm dừng")
    if cruise_alt_m <= 0:
        raise PlanError("độ cao phải lớn hơn 0")
    home = _home(tags)
    wps = []
    for s in stops:
        if s.get("tag_id") is None:
            raise PlanError("mỗi điểm dừng phải có tag_id — vị trí suy từ marker, không từ toạ độ (3.1)")
        t = _find(tags, int(s["tag_id"]))
        action = (s.get("action") or "none").lower()
        if action not in ("none", "pickup", "dropoff"):
            raise PlanError(f"action không hợp lệ: {action}")
        wps.append(_wp(t, float(s.get("alt_m") or cruise_alt_m), action,
                       radius=t.get("landing_tol_m") or accept_radius_m, vel=max_vel_mps,
                       loiter_s=float(s.get("loiter_s") or 0.0)))
    if wps[-1]["tag_id"] != home["tag_id"]:
        wps.append(_wp(home, cruise_alt_m, "none", radius=home.get("landing_tol_m") or accept_radius_m,
                       vel=max_vel_mps))
    if len(wps) > MAX_ITEMS:
        raise PlanError(f"kế hoạch {len(wps)} mục, quá giới hạn {MAX_ITEMS} của giao ước")
    for i, w in enumerate(wps):
        w["seq"] = i
    return wps


def plan_pickup_dropoff(tags: list[dict], pickup_tag: int, dropoff_tag: int, *, cruise_alt_m: float,
                        max_vel_mps: float, accept_radius_m: float) -> list[dict]:
    if pickup_tag == dropoff_tag:
        raise PlanError("tag lấy và tag giao phải khác nhau")
    return plan_stops(tags, [{"tag_id": pickup_tag, "action": "pickup"},
                             {"tag_id": dropoff_tag, "action": "dropoff"}],
                      cruise_alt_m=cruise_alt_m, max_vel_mps=max_vel_mps, accept_radius_m=accept_radius_m)


def plan_warnings(wps: list[dict], areas: list[dict], tags: list[dict] | None = None) -> list[str]:
    """Cảnh báo phía GCS (KHÔNG cưỡng chế — drone không biết vùng bay, giao ước 9.3).

    tags: để tính độ cao điểm so với mặt tag (cảnh báo camera không đọc được tag khi quá cao).
    """
    out = []
    tag_d = {t["tag_id"]: t.get("pos_d_m", 0.0) for t in tags or []}
    for w in wps:
        if w.get("tag_id") in tag_d:
            alt = tag_d[w["tag_id"]] - w["pos_d_m"]
            if alt > MAX_TAG_VISIBLE_ALT_M:
                out.append(f"waypoint {w['seq']} (tag {w['tag_id']}) cao {alt:.1f} m > {MAX_TAG_VISIBLE_ALT_M} m — "
                           "camera sẽ không đọc được tag, drone tìm không thấy rồi hết lượt thử lại")
    operating = [a["vertices"] for a in areas if a["kind"] == "operating" and a.get("enabled", True)]
    no_fly = [(a["name"], a["vertices"]) for a in areas if a["kind"] == "no_fly" and a.get("enabled", True)]
    for w in wps:
        p = (w["pos_n_m"], w["pos_e_m"])
        if operating and not any(point_in_polygon(p, poly) for poly in operating):
            out.append(f"waypoint {w['seq']} nằm ngoài vùng bay")
    for i in range(len(wps) - 1):
        a, b = wps[i], wps[i + 1]
        for k in range(11):  # lấy mẫu dọc chặng
            p = (a["pos_n_m"] + (b["pos_n_m"] - a["pos_n_m"]) * k / 10,
                 a["pos_e_m"] + (b["pos_e_m"] - a["pos_e_m"]) * k / 10)
            hit = next((name for name, poly in no_fly if point_in_polygon(p, poly)), None)
            if hit:
                out.append(f"chặng {i}→{i + 1} cắt qua vùng cấm '{hit}'")
                break
    return out
