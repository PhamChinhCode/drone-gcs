"""COBS, CRC, khung, bản tin — nghiệm thu A1 và kiểm thử quy đổi đơn vị (mục 3.1)."""
import os
import random

import pytest

from gcs_backend.link import cobs, crc
from gcs_backend.link import frame as fr
from gcs_backend.link import messages as m
from gcs_backend.link.auth import ReplayWindow

KEY = bytes(range(16))


def test_crc16_ccitt_false_check_value():
    assert crc.crc16_ccitt_false(b"123456789") == 0x29B1


def test_crc32_check_value():
    assert crc.crc32(b"123456789") == 0xCBF43926


@pytest.mark.parametrize("data", [b"", b"\x00", b"\x00\x00", b"\x11\x22\x00\x33", bytes(range(1, 255)),
                                  bytes(range(1, 256)), bytes(300), bytes([1] * 254 + [0] + [2] * 10)])
def test_cobs_roundtrip_edge_cases(data):
    enc = cobs.encode(data)
    assert 0 not in enc
    assert cobs.decode(enc) == data


def test_cobs_splitter_resync_after_garbage():
    sp = cobs.FrameSplitter()
    good = cobs.encode(b"\x01hello") + b"\x00"
    frames = sp.feed(b"\x05\x07garbage" + b"\x00" + good)
    assert cobs.decode(frames[-1]) == b"\x01hello"


def test_fuzz_a1_no_undetected_corruption():
    """A1: khung ngẫu nhiên bị hỏng bit → không có sai sót nào lọt qua mà không bị phát hiện.

    Mặc định 20 000 vòng cho CI; đặt GCS_FUZZ_N=1000000 để chạy đủ tiêu chí nghiệm thu.
    """
    n = int(os.environ.get("GCS_FUZZ_N", "20000"))
    rng = random.Random(1234)
    undetected = 0
    for i in range(n):
        payload = rng.randbytes(rng.randint(0, 120))
        unit = fr.build(rng.choice([0x41, 0x11, 0x17]), payload, i & 0xFFFF, priority=rng.randint(0, 3),
                        need_ack=bool(rng.getrandbits(1)), src=4, dst=1, auth_key=KEY if i % 2 else None)
        wire = bytearray(cobs.encode(b"\x01" + unit))
        for _ in range(rng.randint(1, 3)):
            pos = rng.randrange(len(wire))
            wire[pos] ^= 1 << rng.randrange(8)
        try:
            body = cobs.decode(bytes(wire).replace(b"\x00", b"\x01"))
            got = fr.parse(body[1:], KEY)
        except (cobs.CobsError, fr.FrameError):
            continue
        # chan_id không thuộc phạm vi CRC (mục 5.2) — chỉ so đơn vị lớp 3
        if body[1:] != unit and got.auth_ok is not False:
            undetected += 1
    assert undetected == 0


def test_all_message_sizes_match_spec():
    spec = {0x10: 12, 0x11: 6, 0x12: 22, 0x13: 12, 0x14: 28, 0x15: 8, 0x16: 6, 0x17: 8, 0x18: 20, 0x19: 8,
            0x1A: 22, 0x1B: 8, 0x1C: 4, 0x40: 12, 0x41: 32, 0x42: 24, 0x43: 16, 0x44: 16, 0x46: 20, 0x47: 12,
            0x7F: 5}
    for mid, size in spec.items():
        assert m.REGISTRY[mid].SIZE == size, hex(mid)
    assert all(not (0x1D <= mid <= 0x1F) for mid in m.REGISTRY), "0x1D–0x1F giữ chỗ cho geofence"


def test_telem_fast_frame_is_42_bytes():
    t = m.TelemFast.from_si(1, [1, 2, -3], [0, 0, 0], [0, 0, 0], 0, 0, 0, 50)
    assert len(fr.build(0x41, t.pack(), 1, priority=0, need_ack=False, src=4, dst=1)) == 42


def test_units_roundtrip_both_directions():
    t = m.TelemFast.from_si(123, [12.345, -6.789, -5.0], [1.23, -0.5, 0.25], [-20.5, 15.25, 359.99], 2, 0x11, 3, 77)
    si = m.TelemFast.unpack(t.pack()).to_si()
    assert si["pos"] == [12.345, -6.789, -5.0]
    assert si["vel"] == [1.23, -0.5, 0.25]
    assert si["att"] == [-20.5, 15.25, 359.99]
    assert si["armed"] and si["carrying"] and not si["tag_lock"]


def test_deg_wrap():
    assert m.deg_to_cdeg_signed(190) == -17000
    assert m.deg_to_cdeg_signed(-180) == -18000
    assert m.deg_to_cdeg_unsigned(-90) == 27000
    assert m.deg_to_cdeg_unsigned(360) == 0


def test_mission_wp_si_roundtrip():
    wp = {"pos_n_m": 10.0, "pos_e_m": -4.5, "pos_d_m": -5.0, "yaw_deg": 90, "tag_id": 7, "accept_radius_m": 0.3,
          "max_vel_mps": 2.5, "loiter_s": 2, "action": "pickup", "require_tag_lock": True, "precision_land": True}
    back = m.MissionWp.unpack(m.MissionWp.from_si(1, 2, 9, wp).pack()).to_si()
    for k, v in wp.items():
        assert back[k] == pytest.approx(v), k


def test_map_crc_order_independent_and_sensitive():
    a = {"tag_id": 1, "pos_n_m": 0, "pos_e_m": 0, "tag_size_m": 0.3, "kind": "home"}
    b = {"tag_id": 5, "pos_n_m": 10, "pos_e_m": 3, "tag_size_m": 0.3, "kind": "pickup"}
    assert m.compute_map_crc([a, b]) == m.compute_map_crc([b, a])
    moved = dict(b, pos_n_m=10.001)
    assert m.compute_map_crc([a, moved]) != m.compute_map_crc([a, b])
    assert m.compute_map_crc([a, dict(b, enabled=False)]) == m.compute_map_crc([a])


def test_auth_tag_detects_tamper():
    unit = bytearray(fr.build(0x17, bytes(8), 9, priority=3, need_ack=True, src=1, dst=4, auth_key=KEY))
    assert fr.parse(bytes(unit), KEY).auth_ok is True
    assert fr.parse(bytes(unit), b"x" * 16).auth_ok is False


def test_replay_window():
    w = ReplayWindow()
    assert w.accept(100)
    assert not w.accept(100)
    assert w.accept(98)          # đảo thứ tự nhẹ
    assert not w.accept(98)
    assert w.accept(200)
    assert not w.accept(100)     # ngoài cửa sổ 64
    for s in (0x7000, 0xE000, 0xFFF0, 0x0010):  # telemetry dời đỉnh qua vòng quay 16 bit
        w.observe(s)
    assert w.accept(0x0011)
