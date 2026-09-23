"""GPS và gốc WGS84 của bản đồ tag — giao ước GCS <-> Pi bản 0.7 (8.6, 8.7, 8.4, 5.2)."""
from gcs_backend.link_mav.dialect import drone_gcs as mav
from gcs_backend.link_mav.tagmap import site_origin, tagmap_crc
from gcs_backend.link_mav.telemetry import TelemetryDecoder
from gcs_backend.mission.uploader import tags_yaml

# Cùng bản đồ với vectơ 0.6: tag 0 tại gốc, tag 1 tại x = 10 m (ENU) tức e = 10 (NED).
TAGS = [{"tag_id": 0, "kind": "home", "label": "Home", "pos_n_m": 0.0, "pos_e_m": 0.0, "pos_d_m": 0.0},
        {"tag_id": 1, "kind": "pickup", "label": "A", "pos_n_m": 0.0, "pos_e_m": 10.0, "pos_d_m": 0.0}]
SITE = {"origin_lat": 21.0285110, "origin_lon": 105.8048170, "origin_alt_m": 15.0,
        "yaw_offset_deg": -1.5}


def test_khong_goc_thi_crc_nhu_0_6():
    assert tagmap_crc(TAGS) == 0x6BDEA0A6
    assert tagmap_crc(TAGS, site_origin({"origin_lat": None, "origin_lon": None})) == 0x6BDEA0A6


def test_vecto_kiem_co_goc_trung_pi():
    """Pi tính độc lập ra đúng số này (drone-ros2-jazzy test_tagmap.test_vecto_kiem_co_goc)."""
    assert tagmap_crc(TAGS, site_origin(SITE)) == 0xA35B41F9


def test_site_thieu_do_cao_thi_lay_0():
    o = site_origin({"origin_lat": 21.0, "origin_lon": 105.0, "origin_alt_m": None,
                     "yaw_offset_deg": None})
    assert o == {"lat": 21.0, "lon": 105.0, "alt_m": 0.0, "north_yaw_deg": 0.0}


def test_tags_yaml_mang_goc():
    y = tags_yaml(TAGS, site_origin(SITE))
    assert "geo_origin_valid: true" in y and "geo_origin_lat: 21.0285110" in y
    assert "geo_north_yaw_deg: -1.50" in y
    assert "geo_origin_valid: false" in tags_yaml(TAGS, None)


def _telem(**kw):
    f = dict(stamp_us=0, mission_id=0, contract_ver=700, lat=0, lon=0, marker_id_tracking=-1,
             alt_m=0.0, vel_ned=[0.0, 0.0, 0.0], battery_pct=0.0, battery_v=0.0, valid_flags=0,
             status_flags=0, gcs_rssi_dbm=0, mission_state=0, current_wp_index=0, wp_total=0,
             retry_count=0, gripper_state=0, failsafe_type=0, expected_marker_id=-1,
             tagmap_crc=0, home_n_mm=0, home_e_mm=0)
    f.update(kw)
    return mav.MAVLink_drone_telemetry_message(**f)


def test_gps_tho_va_vi_tri_hop_nhat():
    valid = mav.DRONE_POS_VALID | mav.DRONE_GLOBAL_POS_VALID
    _, si = TelemetryDecoder().feed(_telem(valid_flags=valid, lat=210284000, lon=1058050000,
                                           gps_fix_type=3, gps_sats=14, gps_hacc_cm=120))
    assert (si["lat"], si["lon"]) == (21.0284, 105.805)
    assert si["gps"] == {"fix_type": 3, "sats": 14, "hacc_m": 1.2}


def test_gps_khong_biet_thi_none():
    """Pi cũ (< 0.7) không gửi trường GPS -> đọc ra 0 -> không vẽ gì (R3)."""
    _, si = TelemetryDecoder().feed(_telem())
    assert si["gps"] is None and si["lat"] is None
    _, si = TelemetryDecoder().feed(_telem(gps_fix_type=1, gps_sats=0, gps_hacc_cm=0xFFFF))
    assert si["gps"] == {"fix_type": 1, "sats": 0, "hacc_m": None}, "có GPS nhưng chưa fix"
