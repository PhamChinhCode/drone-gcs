"""Bộ kiểm tra thiết kế — A16: đủ 10 mã kích hoạt đúng. Planner — quy tắc tách pha ngang/hạ độ cao (6.1)."""
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


def test_plan_pickup_dropoff_separates_horizontal_and_vertical():
    tags = good_design()["tags"]
    wps = planner.plan_pickup_dropoff(tags, 1, 2, cruise_alt_m=5, max_vel_mps=3, accept_radius_m=1.5)
    assert len(wps) == 9
    for a, b in zip(wps, wps[1:]):
        moved_h = (a["pos_n_m"], a["pos_e_m"]) != (b["pos_n_m"], b["pos_e_m"])
        moved_v = a["pos_d_m"] != b["pos_d_m"]
        assert not (moved_h and moved_v), f"chặng {a['seq']}→{b['seq']} đi chéo"
    assert wps[2]["action"] == "pickup" and wps[2]["precision_land"] and wps[2]["tag_id"] == 1
    assert wps[5]["action"] == "dropoff" and wps[5]["tag_id"] == 2
    assert wps[1]["require_tag_lock"] and wps[1]["pos_d_m"] == -5
    assert wps[8]["precision_land"] and wps[8]["tag_id"] == 0


def test_plan_rejects_bad_input():
    tags = good_design()["tags"]
    with pytest.raises(planner.PlanError):
        planner.plan_pickup_dropoff(tags, 1, 1, cruise_alt_m=5, max_vel_mps=3, accept_radius_m=1)
    with pytest.raises(planner.PlanError):
        planner.plan_pickup_dropoff(tags, 1, 99, cruise_alt_m=5, max_vel_mps=3, accept_radius_m=1)


def test_plan_warns_crossing_no_fly():
    tags = good_design()["tags"]
    wps = planner.plan_pickup_dropoff(tags, 1, 2, cruise_alt_m=5, max_vel_mps=3, accept_radius_m=1.5)
    warn = planner.plan_warnings(wps, [area("operating", SQUARE), area("no_fly", [[2, 2], [2, 8], [8, 8], [8, 2]], "kho")])
    assert any("kho" in w for w in warn)
