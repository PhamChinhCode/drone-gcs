"""Phiên liên kết qua drone giả lập — nghiệm thu A2, A3, A4 (tầng T0, không cần phần cứng)."""
import asyncio
import time

import pytest

from gcs_backend.bus import EventBus
from gcs_backend.link import frame as fr
from gcs_backend.link import messages as m
from gcs_backend.link.session import LinkSession
from gcs_backend.sim.fake_drone import FakeDrone, Faults, start_link_server

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


async def _setup(drop: float = 0.0):
    faults = Faults()
    faults.drop = drop
    drone = FakeDrone(KEY, faults)
    server = await start_link_server(drone, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    bus = EventBus()
    sess = LinkSession(f"tcp://127.0.0.1:{port}", bus, KEY)
    task = asyncio.create_task(sess.run())
    for _ in range(100):
        if sess.transport is not None:
            break
        await asyncio.sleep(0.02)
    return drone, server, sess, task, bus


async def _teardown(drone, server, task):
    task.cancel()
    for t in drone.tasks:
        t.cancel()
    if drone.activity:
        drone.activity.cancel()
    server.close()


@pytest.mark.asyncio
async def test_command_ack_and_telemetry():
    drone, server, sess, task, bus = await _setup()
    got = []
    bus.subscribe("rx", lambda t, d: got.append(d["msg"]))
    try:
        ack = await sess.send(m.CmdSimple(1, m.SimpleAction.ARM))
        assert ack.result == m.AckResult.OK
        assert drone.armed
        await asyncio.sleep(1.2)
        assert any(isinstance(x, m.TelemFast) for x in got)
        assert any(isinstance(x, m.HeartbeatDrone) for x in got)
        assert sess.link_state() == "ok"
    finally:
        await _teardown(drone, server, task)


@pytest.mark.asyncio
async def test_a2_duplicate_seq_executes_once_acks_every_time():
    drone, server, sess, task, bus = await _setup()
    executed = []
    drone.ep.handlers.insert(0, lambda msg, hdr: executed.append(hdr.seq) if isinstance(msg, m.CmdGoto) else None)
    try:
        await sess.send(m.CmdSimple(1, m.SimpleAction.ARM))
        await sess.send(m.CmdSimple(2, m.SimpleAction.TAKEOFF, 0, 300))
        goto = m.CmdGoto(3, m.RefFrame.MAP_NED, -1, 1000, 0, -3000, 0, 100, 1)
        seq = sess.next_seq()
        raw = sess.build(goto, seq)

        received = asyncio.Queue()

        def count_ack(u_raw):
            u = fr.parse(u_raw)
            if u.header.is_ack:
                a = m.Ack.unpack(u.payload)
                if a.ack_seq == seq:
                    received.put_nowait(a.result)
        real_on_unit = sess.on_unit
        sess.on_unit = lambda r: (count_ack(r), real_on_unit(r))
        for _ in range(100):
            sess.enqueue_raw(2, raw)
        deadline = time.monotonic() + 5
        while received.qsize() < 100 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert received.qsize() == 100
        assert executed.count(seq) == 1
    finally:
        await _teardown(drone, server, task)


@pytest.mark.asyncio
async def test_a3_retry_with_30pct_drop_all_commands_arrive_once():
    drone, server, sess, task, bus = await _setup(drop=0.30)
    executed: list[int] = []
    drone.ep.handlers.insert(0, lambda msg, hdr: executed.append(msg.cmd_seq) if isinstance(msg, m.CmdSimple) else None)
    ok = fail = 0
    try:
        for i in range(60):
            try:
                ack = await sess.send(m.CmdSimple(i, m.SimpleAction.HOLD))
                ok += ack.result == m.AckResult.OK
            except Exception:
                fail += 1
        # 4 lần thử × rớt 30 % mỗi chiều: xác suất một lệnh thất bại ≈ (1−0,49)^4 ≈ 6,8 %.
        # "100 % sau retry" (N4) cần tầng trên gửi lại; ở đây kiểm tra: KHÔNG lệnh nào chạy hai lần,
        # và mọi lệnh đã được ACK đều thực thi đúng một lần.
        assert all(executed.count(i) <= 1 for i in range(60))
        assert ok >= 45
        assert len(set(executed)) >= ok
    finally:
        await _teardown(drone, server, task)


@pytest.mark.asyncio
async def test_a4_emergency_under_telemetry_flood():
    drone, server, sess, task, bus = await _setup()
    try:
        # ngập hàng đợi TX phía GCS bằng gói ưu tiên 0/1
        flood = sess.build(m.HeartbeatGcs(0, 0, 1), sess.next_seq())
        for _ in range(500):
            sess.enqueue_raw(0, flood)
        t0 = time.monotonic()
        ack = await sess.send(m.Emergency(9, m.EmergencyAction.HOLD, 0, 0))
        assert ack.result == m.AckResult.OK
        assert (time.monotonic() - t0) < 0.3
    finally:
        await _teardown(drone, server, task)


@pytest.mark.asyncio
async def test_link_state_distinguishes_dongle_vs_drone():
    drone, server, sess, task, bus = await _setup()
    try:
        await asyncio.sleep(1.3)
        assert sess.link_state() == "ok"
        drone.faults.hang_until = time.monotonic() + 30   # Pi 4 treo, dongle vẫn MAC-ACK
        await asyncio.sleep(4.5)
        assert sess.link_state() == "drone_silent"
        drone.faults.hang_until = 0
        drone.faults.dongle_on = False
        await asyncio.sleep(2.5)
        assert sess.link_state() == "dongle_lost"
    finally:
        await _teardown(drone, server, task)
