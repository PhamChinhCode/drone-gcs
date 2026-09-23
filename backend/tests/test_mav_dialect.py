"""Dialect drone_gcs.xml — phép kiểm A13, A14, A15(a) của giao ước GCS <-> Pi (không cần lên dây)."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import pymavlink
from gcs_backend.link_mav import tagmap
from gcs_backend.link_mav.dialect import drone_gcs as d

XML = Path(__file__).resolve().parent.parent / "gcs_backend" / "link_mav" / "drone_gcs.xml"


def _codec(mod):
    mav = mod.MAVLink(None, srcSystem=1, srcComponent=191)
    mav.robust_parsing = True
    return mav


def _roundtrip(mod_tx, mod_rx, msg):
    buf = msg.pack(_codec(mod_tx))
    return _codec(mod_rx).parse_buffer(bytes(buf))


def test_a15a_tagmap_crc_vector():
    """tags.yaml hiện tại: tag 0 tại gốc, tag 1 tại x (ENU) = 10 m → e = 10 m."""
    tags = [{"tag_id": 0, "pos_n_m": 0.0, "pos_e_m": 0.0, "pos_d_m": 0.0},
            {"tag_id": 1, "pos_n_m": 0.0, "pos_e_m": 10.0, "pos_d_m": 0.0}]
    assert tagmap.tagmap_crc(tags) == 0x6BDEA0A6
    assert tagmap.tagmap_crc(list(reversed(tags))) == 0x6BDEA0A6  # sắp theo tag_id, không theo thứ tự nhập
    assert tagmap.tagmap_crc(tags + [{"tag_id": 2, "pos_n_m": 1, "pos_e_m": 1, "enabled": False}]) == 0x6BDEA0A6
    assert tagmap.tagmap_crc([tags[0], {**tags[1], "pos_e_m": 10.001}]) != 0x6BDEA0A6  # A15(b)


def test_id_so_hieu_khop_so_dang_ky_8_1_8_2():
    assert (d.MAVLINK_MSG_ID_DRONE_MISSION_COUNT, d.MAVLINK_MSG_ID_DRONE_MISSION_REQUEST,
            d.MAVLINK_MSG_ID_DRONE_MISSION_ITEM, d.MAVLINK_MSG_ID_DRONE_MISSION_ACK,
            d.MAVLINK_MSG_ID_DRONE_TELEMETRY, d.MAVLINK_MSG_ID_DRONE_LINK_STATS) == (42001, 42002, 42003, 42004,
                                                                                    42010, 42011)
    assert d.MAV_CMD_DRONE_ABORT_MISSION == 42100
    assert d.DRONE_HOME_VALID == 256 and d.DRONE_STATE_FAILSAFE == 10 and d.DRONE_FS_FC_COMM_LOST == 6


def test_dong_mo_goi_telemetry_giu_nguyen_truong():
    msg = d.MAVLink_drone_telemetry_message(
        stamp_us=1_758_000_000_000_000, mission_id=903, contract_ver=300, lat=0, lon=0, marker_id_tracking=-1,
        alt_m=1.5, vel_ned=[0.5, -0.25, -0.1], battery_pct=0.0, battery_v=0.0,
        valid_flags=d.DRONE_POS_VALID | d.DRONE_HOME_VALID, status_flags=d.DRONE_ARMED, gcs_rssi_dbm=-71,
        mission_state=d.DRONE_STATE_ENROUTE, current_wp_index=1, wp_total=3, retry_count=0,
        gripper_state=d.DRONE_GRIP_CLOSED, failsafe_type=0, expected_marker_id=1, tagmap_crc=0x6BDEA0A6,
        home_n_mm=40, home_e_mm=-10)
    got = _roundtrip(d, d, msg)
    assert len(got) == 1 and got[0].get_type() == "DRONE_TELEMETRY"
    g = got[0]
    assert (g.mission_id, g.gcs_rssi_dbm, g.expected_marker_id, g.tagmap_crc, g.home_e_mm) == (903, -71, 1,
                                                                                            0x6BDEA0A6, -10)
    assert list(g.vel_ned) == [0.5, -0.25, -0.10000000149011612]


def test_plan_name_va_reason_cat_ngan_khong_loi():
    item = d.MAVLink_drone_mission_count_message(
        mission_id=7, issued_stamp_us=0, search_timeout_s=0.0, count=2, max_retries=0,
        plan_name="Lấy A giao B dài quá hai mươi byte".encode()[:20], contract_ver=300)
    assert _roundtrip(d, d, item)[0].count == 2


# ── A13 / A14: tương thích khi thêm trường (R2) ──────────────────────────────────────────────────


def _gen_variant(tmp: Path, name: str, xml_text: str):
    """Sinh module từ một biến thể XML trong tiến trình con (mavgen đọc XML bằng mã hoá mặc định của HĐH)."""
    src = tmp / name
    src.mkdir()
    defs = Path(pymavlink.__file__).parent / "message_definitions" / "v1.0"
    for f in ("common.xml", "standard.xml", "minimal.xml"):
        shutil.copy(defs / f, src)
    (src / "drone_gcs.xml").write_text(xml_text, encoding="utf-8")
    out = tmp / f"{name}.py"
    code = ("from pymavlink.generator import mavgen;"
            f"o=mavgen.Opts(r'{out}', wire_protocol='2.0', language='Python3', validate=False);"
            f"assert mavgen.mavgen(o, [r'{src / 'drone_gcs.xml'}'])")
    subprocess.run([sys.executable, "-X", "utf8", "-c", code], check=True, capture_output=True)
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _link_stats(mod, **extra):
    return mod.MAVLink_drone_link_stats_message(1, 2, 3, 4, 5, 6, 7, 8, **extra)


@pytest.fixture(scope="module")
def variants(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dialect")
    base = XML.read_text(encoding="utf-8")
    # chèn trường thử vào cuối DRONE_LINK_STATS; bám mốc </message> để chú thích đổi không làm hỏng test
    stats_end = re.search(r'<message id="42011".*?(?=</message>)', base, re.S).group(0)
    ext = base.replace(stats_end, stats_end + '  <extensions/> <field type="uint32_t" name="new_ext"/> ')
    plain = base.replace(stats_end, stats_end + '  <field type="uint32_t" name="new_plain"/> ')
    return _gen_variant(tmp, "v_n", base), _gen_variant(tmp, "v_ext", ext), _gen_variant(tmp, "v_plain", plain)


def test_a13_truong_sau_extensions_doc_duoc_ca_hai_chieu(variants):
    old, new, _ = variants
    assert old.MAVLink_drone_link_stats_message.crc_extra == new.MAVLink_drone_link_stats_message.crc_extra
    got = _roundtrip(new, old, _link_stats(new, new_ext=99))
    assert got and got[0].rtt_ms == 8                           # bên cũ bỏ qua trường mới
    got = _roundtrip(old, new, _link_stats(old))
    assert got and got[0].rtt_ms == 8 and got[0].new_ext == 0   # bên mới thấy 0


def test_a14_truong_truoc_extensions_lam_ben_cu_loai_goi(variants):
    old, _, plain = variants
    assert old.MAVLink_drone_link_stats_message.crc_extra != plain.MAVLink_drone_link_stats_message.crc_extra
    got = _roundtrip(plain, old, _link_stats(plain, new_plain=99))
    assert not any(m.get_type() == "DRONE_LINK_STATS" for m in got)  # PHẢI thất bại — lý do R2 tồn tại


def test_xml_khong_co_truong_nao_chen_truoc_extensions_cua_telemetry():
    """Trường ext phải nằm SAU <extensions/>, đúng thứ tự — thứ tự extension LÀ phần của hợp đồng (R2).

    flight_result thêm ở 0.5 phải ở CUỐI: chèn giữa là đổi nghĩa mọi trường sau nó. Ba trường GPS thêm
    ở 0.7 nối tiếp SAU flight_result, cũng theo đúng quy tắc đó."""
    body = re.search(r'name="DRONE_TELEMETRY">(.*?)</message>', XML.read_text(encoding="utf-8"), re.S).group(1)
    after = body.split("<extensions/>")[1]
    assert re.findall(r'name="(\w+)"', after) == ["expected_marker_id", "tagmap_crc", "home_n_mm",
                                                   "home_e_mm", "flight_result", "gps_fix_type",
                                                   "gps_sats", "gps_hacc_cm"]


def test_hai_truong_pi_them_so_voi_bang_8_3_8_4():
    """XML bản gốc của Pi có hai trường mà mục 8.3/8.4 chưa ghi — XML thắng tài liệu (mục 0.2).

    mission_id của ITEM nằm TRƯỚC <extensions/> nên nó đổi CRC_EXTRA: bên nào sinh mã từ XML cũ sẽ
    loại sạch mọi ITEM mà không báo gì (đúng kịch bản A14).
    """
    xml = XML.read_text(encoding="utf-8")
    item = re.search(r'name="DRONE_MISSION_ITEM">(.*?)</message>', xml, re.S).group(1)
    assert "<extensions/>" not in item, "toàn bộ ITEM nằm trước extensions"
    assert "mission_id" in item
    count = re.search(r'name="DRONE_MISSION_COUNT">(.*?)</message>', xml, re.S).group(1)
    assert re.findall(r'name="(\w+)"', count.split("<extensions/>")[1]) == ["contract_ver"]
