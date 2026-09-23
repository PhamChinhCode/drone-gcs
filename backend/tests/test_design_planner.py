"""Bộ kiểm tra thiết kế: đủ 10 mã kích hoạt đúng. Planner: một mục mỗi điểm dừng (giao ước 3.2b)."""
import pytest

from gcs_backend.mission import planner
from gcs_backend.sitedesign.validator import has_errors, validate_design


def tag(tag_id, n, e, kind="pickup", size=0.3, label=None):
    return {"tag_id": tag_id, "label": label or str(tag_id), "pos_n_m": n, "pos_e_m": e, "pos_d_m": 0.0,
            "yaw_deg": 0, "tag_size_m": size, "kind": kind, "landing_tol_m": 0.3, "enabled": True}


def area(kind, verts, name="v"):
    return {"kind": kind, "name": name, "vertices": verts, "enabled": True}


SQUARE = [[-50, -50], [-50, 50], [50, 50], [50, -50]]


def good_design():
    return {"site": {"link_radius_meas_m": 200, "gcs_pos_n_m": 0, "gcs_pos_e_m": 0},
            "tags": [tag(0, 0, 0, "home"), tag(1, 10, 10), tag(2, -10, 20, "dropoff")],
            "areas": [area("operating", SQUARE)]}


def codes(d):
    return {i["code"] for i in validate_design(d)}


def test_good_design_has_no_issues():
    assert codes(good_design()) == set()


@pytest.mark.parametrize("mutate,code", [
    (lambda d: d["tags"].append(tag(1, 30, 30)), "E_DUP_TAG"),
    (lambda d: d["tags"].__setitem__(0, tag(0, 0, 0, "waypoint")), "E_NO_HOME"),
    (lambda d: d["areas"].append(area("no_fly", [[0, 0], [10, 10], [0, 10], [10, 0]])), "E_SELF_INTERSECT"),
    (lambda d: d["tags"].append(tag(9, 80, 0)), "E_TAG_OUTSIDE"),
    (lambda d: d["areas"].append(area("no_fly", [[5, 5], [5, 15], [15, 15], [15, 5]])), "E_TAG_IN_NOFLY"),
    (lambda d: d["site"].__setitem__("link_radius_meas_m", 20), "W_BEYOND_LINK"),
    (lambda d: d["site"].__setitem__("link_radius_meas_m", None), "W_NO_MEASURE"),
    (lambda d: d["tags"].append(tag(7, 10.5, 10)), "W_TAGS_CLOSE"),
    (lambda d: d["tags"].append(tag(8, 30, -30, size=0.6)), "W_TAG_SIZE"),
    (lambda d: d["tags"].append(tag(6, 47, 0)), "W_AREA_SMALL"),
])
def test_a16_each_rule_triggers(mutate, code):
    d = good_design()
    mutate(d)
    assert code in codes(d)


def test_errors_block():
    d = good_design()
    d["tags"].append(tag(1, 30, 30))
    assert has_errors(validate_design(d))


def test_moi_diem_dung_mot_muc_va_tu_them_home_cuoi():
    """Giao ước 3.2b: một mục = bay tới -> tìm marker -> hạ -> làm action -> cất cánh lại."""
    tags = good_design()["tags"]
    wps = planner.plan_pickup_dropoff(tags, 1, 2, cruise_alt_m=5, max_vel_mps=1.5, accept_radius_m=1.5)
    assert [w["tag_id"] for w in wps] == [1, 2, 0], "hai điểm dừng + home; không có mục đi ngang rời"
    assert [w["action"] for w in wps] == ["pickup", "dropoff", "none"]
    assert wps[-1]["tag_id"] == 0, "kế hoạch KHÔNG tự về home — mục home cuối là do GCS thêm"
    assert wps[0]["pos_d_m"] == -5, "cao hơn tag 5 m"


def test_khong_them_home_neu_diem_cuoi_da_la_home():
    tags = good_design()["tags"]
    wps = planner.plan_stops(tags, [{"tag_id": 1}, {"tag_id": 0}], cruise_alt_m=2, max_vel_mps=1,
                             accept_radius_m=1)
    assert [w["tag_id"] for w in wps] == [1, 0]


def test_diem_dung_phai_co_tag_va_khong_qua_16_muc():
    tags = good_design()["tags"]
    with pytest.raises(planner.PlanError, match="tag_id"):
        planner.plan_stops(tags, [{"pos_n_m": 1, "pos_e_m": 2}], cruise_alt_m=2, max_vel_mps=1,
                           accept_radius_m=1)
    with pytest.raises(planner.PlanError, match="16"):
        planner.plan_stops(tags, [{"tag_id": 1}] * 16, cruise_alt_m=2, max_vel_mps=1, accept_radius_m=1)


def test_plan_rejects_bad_input():
    tags = good_design()["tags"]
    with pytest.raises(planner.PlanError):
        planner.plan_pickup_dropoff(tags, 1, 1, cruise_alt_m=5, max_vel_mps=3, accept_radius_m=1)
    with pytest.raises(planner.PlanError):
        planner.plan_pickup_dropoff(tags, 1, 99, cruise_alt_m=5, max_vel_mps=3, accept_radius_m=1)


def test_plan_warns_crossing_no_fly():
    """Chặng tag 1 (10, 10) -> home (0, 0) đi xuyên ô vuông 2..8. Cảnh báo, KHÔNG cưỡng chế (9.3)."""
    tags = good_design()["tags"]
    wps = planner.plan_stops(tags, [{"tag_id": 1}], cruise_alt_m=5, max_vel_mps=1.5, accept_radius_m=1.5)
    warn = planner.plan_warnings(wps, [area("operating", SQUARE),
                                       area("no_fly", [[2, 2], [2, 8], [8, 8], [8, 2]], "kho")])
    assert any("kho" in w for w in warn)
