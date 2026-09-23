"""build_items() phải luôn đồng bộ với tagmap_crc() — cùng thứ tự, cùng phép làm tròn (8.6, 8.7)."""
from gcs_backend.link_mav.tagmap import tagmap_crc
from gcs_backend.link_mav.tagmap_client import build_items

TAGS = [
    {"tag_id": 1, "pos_n_m": 0.0, "pos_e_m": 10.0, "pos_d_m": 0.0},
    {"tag_id": 0, "pos_n_m": 0.0, "pos_e_m": 0.0, "pos_d_m": 0.0},
    {"tag_id": 2, "pos_n_m": 0.0, "pos_e_m": -0.002, "pos_d_m": -0.055, "enabled": False},
]


def test_sap_theo_tag_id_tang_dan_va_bo_qua_tag_tat():
    items = build_items(TAGS)
    assert [it[1] for it in items] == [0, 1]           # tag 2 bi bo (enabled=False)
    assert [it[0] for it in items] == [0, 1]            # seq lien tuc tu 0


def test_toa_do_doi_dung_mm_va_lam_tron():
    items = build_items([{"tag_id": 5, "pos_n_m": 0.1229, "pos_e_m": -1.5, "pos_d_m": 0.0015}])
    seq, tag_id, n_mm, e_mm, d_mm = items[0]
    # round() cua Python lam tron NUA VE SO CHAN: 0,1229 -> 123 mm (khong phai 122 nhu int()
    # se cat), va 1,5 -> 2 (chan gan nhat khi dung giua).
    assert (tag_id, n_mm, e_mm, d_mm) == (5, 123, -1500, 2)


def test_dong_bo_voi_tagmap_crc():
    """Tag_id va toa do trong items phai khop dung ban ghi ma tagmap_crc() dung de tinh CRC."""
    items = build_items(TAGS)
    assert len(items) == len(set(it[1] for it in items)) == 2
    # tagmap_crc() tu loc enabled va sap xep giong build_items() - hai ham doc chung mot nguon tags
    assert tagmap_crc(TAGS) != 0
