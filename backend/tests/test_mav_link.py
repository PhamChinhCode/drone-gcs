"""Tầng liên kết MAVLink: đường về theo gói hợp lệ gần nhất, chữ ký, đếm mất gói, TIMESYNC, STATUSTEXT.

Cặp socket UDP loopback thay cho Pi — phép kiểm C4, C5, A17 của giao ước ở mức không cần lên dây.
"""
import asyncio
import socket

import pytest

from gcs_backend.bus import EventBus
from gcs_backend.link_mav.codec import PI
from gcs_backend.link_mav.dialect import drone_gcs as mav
from gcs_backend.link_mav.link import STATUSTEXT_CHUNK, MavLink

KEY = bytes(range(32))


class FakePi:
    """Chỉ đủ để dựng byte đúng giao ước và bắn vào cổng của GCS."""

    def __init__(self, gcs_addr, signing_key=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.setblocking(False)
        self.gcs_addr = gcs_addr
        self.mav = mav.MAVLink(file=None, srcSystem=PI[0], srcComponent=PI[1])
        if signing_key is not None:
            self.mav.signing.secret_key = signing_key
            self.mav.signing.sign_outgoing = True
            self.mav.signing.link_id = 1

    @property
    def addr(self):
        return self.sock.getsockname()

    def send(self, msg):
        buf = msg.pack(self.mav)
        self.mav.seq = (self.mav.seq + 1) % 256  # pack() không tự tăng — xem Codec.encode
        self.sock.sendto(buf, self.gcs_addr)

    def heartbeat(self):
        self.send(self.mav.heartbeat_encode(2, 8, 0, 0, 4))

    def recv_all(self):
        out = []
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
            except BlockingIOError:
                return out
            out.extend(self.mav.parse_buffer(data) or [])

    def close(self):
        self.sock.close()


async def _start(signing_key=None):
    bus = EventBus()
    link = MavLink(bus, "127.0.0.1", 0, signing_key=signing_key)
    task = asyncio.create_task(link.run())
    for _ in range(100):
        if link.transport.local_addr is not None:
            break
        await asyncio.sleep(0.01)
    return link, bus, task, FakePi(link.transport.local_addr, signing_key)


async def _settle(n=3):
    for _ in range(n):
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_duong_ve_hoc_tu_goi_hop_le_dau_tien():
    link, _, task, pi = await _start()
    assert link.transport.peer is None, "chưa nhận gì thì chưa biết gửi đi đâu"
    pi.heartbeat()
    await _settle()
    assert link.transport.peer == pi.addr
    assert link.state.up
    link.send(link.codec.mav.heartbeat_encode(6, 8, 0, 0, 4))
    await _settle()
    assert [m.get_type() for m in pi.recv_all()] == ["HEARTBEAT"]
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_c5_doi_cong_nguon_giua_chung_thi_duong_ve_di_theo():
    """Modem 4G nối lại -> cổng nguồn mới. Không được gửi về cổng cũ (giao ước 2.1)."""
    link, _, task, pi = await _start()
    pi.heartbeat()
    await _settle()
    pi2 = FakePi(link.transport.local_addr)
    pi2.heartbeat()
    await _settle()
    assert link.transport.peer == pi2.addr
    link.send(link.codec.mav.heartbeat_encode(6, 8, 0, 0, 4))
    await _settle()
    assert pi.recv_all() == []
    assert [m.get_type() for m in pi2.recv_all()] == ["HEARTBEAT"]
    task.cancel()
    pi.close()
    pi2.close()


@pytest.mark.asyncio
async def test_c4_goi_sai_chu_ky_bi_bo_va_khong_cuop_duoc_duong_ve():
    link, _, task, pi = await _start(signing_key=KEY)
    pi.heartbeat()
    await _settle()
    assert link.transport.peer == pi.addr

    ke = FakePi(link.transport.local_addr, signing_key=bytes(range(1, 33)))  # khoá khác
    ke.heartbeat()
    await _settle()
    assert link.codec.counters.rx_bad_sig == 1
    assert link.transport.peer == pi.addr, "gói sai chữ ký không được đổi đường về"

    khong_ky = FakePi(link.transport.local_addr)  # không ký gì cả
    khong_ky.heartbeat()
    await _settle()
    assert link.codec.counters.rx_bad_sig == 2
    assert link.transport.peer == pi.addr

    pi.heartbeat()
    await _settle()
    assert link.codec.counters.rx_ok == 2, "gói ký đúng sau đó vẫn nhận bình thường"
    task.cancel()
    for s in (pi, ke, khong_ky):
        s.close()


@pytest.mark.asyncio
async def test_rx_drop_dem_theo_khoang_trong_seq():
    link, _, task, pi = await _start()
    pi.heartbeat()
    await _settle()
    pi.mav.seq = (pi.mav.seq + 3) & 0xFF  # gói sau mang seq cách 4: ba seq ở giữa không tới
    pi.heartbeat()
    await _settle()
    assert link.codec.counters.rx_drop == 3
    assert link.codec.counters.rx_ok == 2
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_timesync_pi_hoi_thi_gcs_tra_loi_ngay():
    link, _, task, pi = await _start()
    pi.heartbeat()
    await _settle()
    pi.recv_all()
    pi.send(pi.mav.timesync_encode(0, 12345))
    await _settle()
    reply = [m for m in pi.recv_all() if m.get_type() == "TIMESYNC"]
    assert len(reply) == 1
    assert reply[0].ts1 == 12345 and reply[0].tc1 != 0
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_a17_statustext_88_byte_duoc_ghep_du():
    """Giao ước 7.5: cắt ngắn sẽ mất đúng phần cuối câu — phần chứa lý do."""
    link, bus, task, pi = await _start()
    got = []
    bus.subscribe("mav.statustext", lambda topic, d: got.append(d["text"]))
    pi.heartbeat()
    await _settle()
    full = ("tu choi ke hoach 903: dang cho cat canh theo ke hoach cu "
            "- khong doi ke hoach luc nay")  # chuỗi thật của mission_manager_node, giao ước 7.5
    assert len(full) > STATUSTEXT_CHUNK, "ngắn hơn 50 byte thì không chia đoạn, test vô nghĩa"
    for i, start in enumerate(range(0, len(full), 50)):
        pi.send(pi.mav.statustext_encode(4, full[start:start + 50].encode(), 7, i))
    await _settle()
    assert got == [full]
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_seq_tang_tren_day_moi_goi_phat():
    """Lỗi Pi tìm ra ở 10.A: pack() không tăng seq -> mọi gói seq = 0 -> bên kia báo mất 255 gói/gói."""
    link, _, task, pi = await _start()
    pi.heartbeat()
    await _settle()
    pi.recv_all()
    for _ in range(3):
        link.send(link.codec.mav.heartbeat_encode(6, 8, 0, 0, 4))
    await _settle()
    seqs = [m.get_seq() for m in pi.recv_all()]
    assert len(seqs) == 3 and seqs[1] == (seqs[0] + 1) % 256 and seqs[2] == (seqs[1] + 1) % 256
    task.cancel()
    pi.close()
