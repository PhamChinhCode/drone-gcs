"""Hướng tag — giao ước GCS <-> Pi bản 0.8 (8.6, 8.7): CRC, gói ITEM, tags.yaml, kiểm thiết kế, CSDL."""
from sqlalchemy import inspect, text

from gcs_backend.config import Settings
from gcs_backend.data.repo import Database
from gcs_backend.link_mav.tagmap import site_origin, tagmap_crc, yaw_cdeg
from gcs_backend.link_mav.tagmap_client import build_items
from gcs_backend.mission.uploader import tags_yaml
from gcs_backend.sitedesign.validator import validate_design

# Vectơ 0.6 (tag 0 tại gốc, tag 1 tại e = 10 m) + hướng: tag 0 = 90°, tag 1 = -45°.
TAGS = [{"tag_id": 0, "kind": "home", "label": "Home", "pos_n_m": 0.0, "pos_e_m": 0.0, "pos_d_m": 0.0,
         "yaw_deg": 90.0, "yaw_valid": True, "tag_size_m": 0.25},
        {"tag_id": 1, "kind": "pickup", "label": "A", "pos_n_m": 0.0, "pos_e_m": 10.0, "pos_d_m": 0.0,
         "yaw_deg": -45.0, "yaw_valid": True, "tag_size_m": 0.25}]
SITE = {"origin_lat": 21.0285110, "origin_lon": 105.8048170, "origin_alt_m": 15.0, "yaw_offset_deg": -1.5}


def _chi(tags, **kw):
    return [{**t, **kw} for t in tags]


def test_vecto_kiem_trung_pi():
    """Pi tính độc lập ra đúng các số này (drone-ros2-jazzy test_tagmap.test_vecto_kiem_co_huong)."""
    assert tagmap_crc(TAGS) == 0xD3A58731
    assert tagmap_crc([TAGS[0], {**TAGS[1], "yaw_valid": False}]) == 0xAADD4B3C
    assert tagmap_crc(TAGS, site_origin(SITE)) == 0x6D6CED89


def test_yaw_chua_do_thi_crc_nhu_0_7():
    """yaw_deg mặc định 0 của bản đồ cũ KHÔNG được vào CRC — không thì mọi bản đồ cũ lệch Pi."""
    assert tagmap_crc(_chi(TAGS, yaw_valid=False)) == 0x6BDEA0A6
    assert tagmap_crc(_chi(TAGS, yaw_valid=False), site_origin(SITE)) == 0xA35B41F9


def test_yaw_dang_chuan_tac():
    assert yaw_cdeg(180.0) == yaw_cdeg(-180.0) == -18000
    assert yaw_cdeg(359.99) == -1 and yaw_cdeg(179.99) == 17999 and yaw_cdeg(-90) == -9000


def test_item_mang_huong_dung_tagmap_crc():
    items = build_items([TAGS[1], TAGS[0], {**TAGS[0], "tag_id": 2, "yaw_valid": False}])
    assert [(it[1], it[5], it[6]) for it in items] == [(0, 1, 9000), (1, 1, -4500), (2, 0, 0)]


def test_tags_yaml_mang_huong_da_do():
    y = tags_yaml(TAGS, None)
    assert "known_tags_heading: [0.0, 90.00, 1.0, -45.00]" in y
    assert "known_tags_heading: []" in tags_yaml(_chi(TAGS, yaw_valid=False), None)


def test_thiet_ke_tu_choi_dai_tag_nho():
    design = {"site": {}, "areas": [], "tags": [dict(TAGS[0]), {**TAGS[1], "tag_id": 11}]}
    codes = [i["code"] for i in validate_design(design)]
    assert "E_RESERVED_TAG" in codes
    design["tags"][1]["tag_id"] = 1
    assert "E_RESERVED_TAG" not in [i["code"] for i in validate_design(design)]


def test_csdl_cu_duoc_them_cot_yaw_valid(tmp_path):
    """CSDL tạo trước 0.8 không có cột yaw_valid: init phải thêm, tag cũ mặc định chưa đo hướng."""
    db = Database(f"sqlite:///{tmp_path / 'gcs.db'}")
    db.init(Settings())
    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE tag_point DROP COLUMN yaw_valid"))
    assert "yaw_valid" not in {c["name"] for c in inspect(db.engine).get_columns("tag_point")}
    db.init(Settings())
    assert "yaw_valid" in {c["name"] for c in inspect(db.engine).get_columns("tag_point")}
    with db.engine.begin() as conn:
        assert conn.execute(text("SELECT DISTINCT yaw_valid FROM tag_point")).scalars().all() == [0]
