"""Bộ kiểm tra thiết kế khu vực — đúng 10 quy tắc mục 8.4.4.

Còn Lỗi → không lưu được, không đồng bộ bản đồ. Còn Cảnh báo → lưu được nhưng phải xác nhận "tôi đã hiểu".
"""
from __future__ import annotations

from statistics import median

from .geometry import dist, dist_to_boundary, is_simple_polygon, point_in_polygon

LINK_SAFETY_FACTOR = 0.7   # biên an toàn 30 % (11.2)
AREA_MARGIN_M = 5.0
CLOSE_FACTOR = 3.0
SIZE_DEV = 0.20


def _issue(code: str, level: str, message: str, refs: list[dict], at: list[float] | None = None) -> dict:
    return {"code": code, "level": level, "message": message, "refs": refs, "at": at}


def validate_design(design: dict) -> list[dict]:
    site = design.get("site") or {}
    tags = [t for t in design.get("tags", []) if t.get("enabled", True)]
    all_tags = design.get("tags", [])
    areas = [a for a in design.get("areas", []) if a.get("enabled", True)]
    issues: list[dict] = []

    # E_DUP_TAG
    seen: dict[int, dict] = {}
    for t in all_tags:
        tid = int(t["tag_id"])
        if tid in seen:
            issues.append(_issue("E_DUP_TAG", "error", f"Trùng tag_id {tid} ({seen[tid].get('label')} / "
                                 f"{t.get('label')}) — drone xác thực tag theo ID, trùng ID là hạ nhầm bãi",
                                 [{"type": "tag", "tag_id": tid}], [t["pos_n_m"], t["pos_e_m"]]))
        else:
            seen[tid] = t

    # E_NO_HOME
    homes = [t for t in tags if t.get("kind") == "home"]
    if not homes:
        issues.append(_issue("E_NO_HOME", "error", "Không có tag Home — RTH không có đích", []))

    # E_SELF_INTERSECT
    valid_areas = []
    for idx, a in enumerate(areas):
        poly = [tuple(v) for v in a["vertices"]]
        if not is_simple_polygon(poly):
            issues.append(_issue("E_SELF_INTERSECT", "error",
                                 f"Đa giác '{a.get('name')}' tự cắt hoặc có ít hơn 3 đỉnh",
                                 [{"type": "area", "index": idx}], list(poly[0]) if poly else None))
        else:
            valid_areas.append((idx, a, poly))

    operating = [(i, a, p) for i, a, p in valid_areas if a["kind"] == "operating"]
    no_fly = [(i, a, p) for i, a, p in valid_areas if a["kind"] == "no_fly"]

    for t in tags:
        p = (t["pos_n_m"], t["pos_e_m"])
        ref = [{"type": "tag", "tag_id": t["tag_id"]}]
        # E_TAG_OUTSIDE
        if operating and not any(point_in_polygon(p, poly) for _, _, poly in operating):
            issues.append(_issue("E_TAG_OUTSIDE", "error",
                                 f"Tag {t['tag_id']} ({t.get('label')}) nằm ngoài vùng bay", ref, list(p)))
        # E_TAG_IN_NOFLY
        for i, a, poly in no_fly:
            if point_in_polygon(p, poly):
                issues.append(_issue("E_TAG_IN_NOFLY", "error",
                                     f"Tag {t['tag_id']} ({t.get('label')}) nằm trong vùng cấm '{a.get('name')}'",
                                     ref + [{"type": "area", "index": i}], list(p)))

    # W_NO_MEASURE / W_BEYOND_LINK
    meas = site.get("link_radius_meas_m")
    gcs = (site.get("gcs_pos_n_m") or 0.0, site.get("gcs_pos_e_m") or 0.0)
    if not meas:
        issues.append(_issue("W_NO_MEASURE", "warning",
                             "Chưa có bán kính liên kết đo được (chưa chạy T2) — chưa biết tầm sóng thật", []))
    else:
        limit = meas * LINK_SAFETY_FACTOR
        for t in tags:
            d = dist(gcs, (t["pos_n_m"], t["pos_e_m"]))
            if d > limit:
                issues.append(_issue("W_BEYOND_LINK", "warning",
                                     f"Tag {t['tag_id']} cách trạm {d:.1f} m > {limit:.1f} m (70 % bán kính đo)",
                                     [{"type": "tag", "tag_id": t["tag_id"]}], [t["pos_n_m"], t["pos_e_m"]]))

    # W_TAGS_CLOSE
    for i in range(len(tags)):
        for j in range(i + 1, len(tags)):
            a, b = tags[i], tags[j]
            d = dist((a["pos_n_m"], a["pos_e_m"]), (b["pos_n_m"], b["pos_e_m"]))
            lim = CLOSE_FACTOR * max(a["tag_size_m"], b["tag_size_m"])
            if d < lim:
                issues.append(_issue("W_TAGS_CLOSE", "warning",
                                     f"Tag {a['tag_id']} và {b['tag_id']} cách {d:.2f} m < {lim:.2f} m — "
                                     "camera có thể bắt nhầm tag lân cận",
                                     [{"type": "tag", "tag_id": a["tag_id"]}, {"type": "tag", "tag_id": b["tag_id"]}],
                                     [a["pos_n_m"], a["pos_e_m"]]))

    # W_TAG_SIZE
    if len(tags) >= 2:
        for t in tags:
            others = [o["tag_size_m"] for o in tags if o is not t]
            ref_size = median(others)
            if ref_size > 0 and abs(t["tag_size_m"] - ref_size) / ref_size > SIZE_DEV:
                issues.append(_issue("W_TAG_SIZE", "warning",
                                     f"Tag {t['tag_id']} kích thước {t['tag_size_m']} m lệch > 20 % so với "
                                     f"các tag khác ({ref_size} m) — thường là gõ nhầm",
                                     [{"type": "tag", "tag_id": t["tag_id"]}], [t["pos_n_m"], t["pos_e_m"]]))

    # W_AREA_SMALL
    for i, a, poly in operating:
        bad = [t for t in tags if not point_in_polygon((t["pos_n_m"], t["pos_e_m"]), poly)
               or dist_to_boundary((t["pos_n_m"], t["pos_e_m"]), poly) < AREA_MARGIN_M]
        if bad:
            issues.append(_issue("W_AREA_SMALL", "warning",
                                 f"Vùng bay '{a.get('name')}' không bao được "
                                 f"{', '.join(str(t['tag_id']) for t in bad)} kèm biên {AREA_MARGIN_M:.0f} m",
                                 [{"type": "area", "index": i}] + [{"type": "tag", "tag_id": t["tag_id"]} for t in bad],
                                 [bad[0]["pos_n_m"], bad[0]["pos_e_m"]]))
    return issues


def has_errors(issues: list[dict]) -> bool:
    return any(i["level"] == "error" for i in issues)


def has_warnings(issues: list[dict]) -> bool:
    return any(i["level"] == "warning" for i in issues)
