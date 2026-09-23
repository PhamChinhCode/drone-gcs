"""Telemetry MAVLink -> dict SI: cờ hiệu lực, hai gốc của alt_m, bước nhảy khi odom neo (5.1, 5.2, 8.4)."""
import math

from gcs_backend.link_mav.dialect import drone_gcs as mav
from gcs_backend.link_mav.telemetry import TelemetryDecoder, item_alt_to_map


def _telem(valid=0, status=0, **kw):
    f = dict(stamp_us=1_758_000_000_000_000, mission_id=903, contract_ver=400, lat=0, lon=0,
             marker_id_tracking=-1, alt_m=1.5, vel_ned=[0.5, -0.25, -0.1], battery_pct=0.0,
             battery_v=0.0, valid_flags=valid, status_flags=status, gcs_rssi_dbm=-71,
             mission_state=mav.DRONE_STATE_ENROUTE, current_wp_index=1, wp_total=3, retry_count=0,
             gripper_state=mav.DRONE_GRIP_CLOSED, failsafe_type=0, expected_marker_id=-1,
             tagmap_crc=0x6BDEA0A6, home_n_mm=0, home_e_mm=0)
    f.update(kw)
    return mav.MAVLink_drone_telemetry_message(**f)


def test_co_ha_thi_tra_none_chu_khong_tra_0():
    """R3: '0 là một lời nói dối khác'. Pin và GPS hiện LUÔN rơi vào nhánh này (5.2)."""
    _, si = TelemetryDecoder().feed(_telem(valid=mav.DRONE_POS_VALID, battery_pct=47.0, lat=210280000))
    assert si["battery_pct"] is None and si["battery_v"] is None
    assert si["lat"] is None and si["lon"] is None
    assert si["marker_id_tracking"] is None, "MARKER_VALID = 0 thì bỏ qua trường, không đọc -1"
    assert si["home_ne"] is None, "HOME_VALID = 0 thì không vẽ gì, kể cả pad_home"
    assert si["armed"] is None, "không có FC_LINK thì ARMED không có nghĩa"
    assert si["alt_m"] == 1.5 and si["vel"] == [0.5, -0.25, -0.1]


def test_co_bat_thi_doc_gia_tri():
    valid = (mav.DRONE_POS_VALID | mav.DRONE_BATTERY_VALID | mav.DRONE_FC_LINK_VALID
             | mav.DRONE_MARKER_VALID | mav.DRONE_GRIPPER_VALID | mav.DRONE_HOME_VALID)
    status = mav.DRONE_ARMED | mav.DRONE_PI_HAS_AUTHORITY | mav.DRONE_CARRYING
    _, si = TelemetryDecoder().feed(_telem(valid=valid, status=status, battery_pct=47.0,
                                           marker_id_tracking=1, expected_marker_id=2,
                                           home_n_mm=3040, home_e_mm=-10))
    assert si["battery_pct"] == 47.0
    assert (si["marker_id_tracking"], si["expected_tag"]) == (1, 2), "tag đang bám khác tag đang tìm"
    assert si["home_ne"] == [3.04, -0.01]
    assert si["armed"] is True and si["pi_has_authority"] is True and si["carrying"] is True
    assert si["valid"]["pos"] and not si["valid"]["global_pos"]


def test_expected_tag_am_mot_la_khong_tim():
    _, si = TelemetryDecoder().feed(_telem(expected_marker_id=-1))
    assert si["expected_tag"] is None
    _, si = TelemetryDecoder().feed(_telem(expected_marker_id=0))
    assert si["expected_tag"] == 0, "tag 0 là tag thật, không phải 'không biết'"


def test_ghep_attitude_vao_mau_vi_tri():
    d = TelemetryDecoder()
    assert d.feed(mav.MAVLink_attitude_message(1000, 0.0, 0.0, math.pi / 2, 0.0, 0.0, 0.0)) is None
    kind, si = d.feed(mav.MAVLink_local_position_ned_message(1000, 1.0, 2.0, -3.0, 0.1, 0.0, 0.0))
    assert kind == "fast"
    assert si["pos"] == [1.0, 2.0, -3.0] and si["alt_m"] == 3.0
    assert round(si["att"][2]) == 90, "yaw so với trục N của bản đồ tag, không phải la bàn"


def test_buoc_nhay_khi_odom_neo_duoc_danh_dau():
    """ekf_health_node ép EKF về pose marker -> nhảy tức thì, frontend không nội suy qua (5.2b)."""
    d = TelemetryDecoder()
    _, a = d.feed(mav.MAVLink_local_position_ned_message(0, 0.0, 0.0, -1.0, 0, 0, 0))
    _, b = d.feed(mav.MAVLink_local_position_ned_message(200, 0.3, 0.0, -1.0, 0, 0, 0))
    _, c = d.feed(mav.MAVLink_local_position_ned_message(400, 3.3, 0.0, -1.0, 0, 0, 0))
    assert (a["jump"], b["jump"], c["jump"]) == (False, False, True)


def test_alt_m_hai_goc_quy_doi_o_mot_cho():
    """Mục kế hoạch: so với tag đích. Telemetry: so với gốc bản đồ. Trùng tên, khác gốc (P22)."""
    assert item_alt_to_map(1.5, tag_d_m=0.0) == 1.5
    assert item_alt_to_map(1.5, tag_d_m=-2.0) == 3.5, "tag trên cao 2 m thì điểm bay ở 3,5 m"
