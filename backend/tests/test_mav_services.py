"""Lệnh, nạp kế hoạch và đọc tham số — phía GCS của A4, A5, A6, A7, A16 (giao ước 3, 4, 9.4)."""
import asyncio

import pytest

from gcs_backend.link_mav import commands as cmd
from gcs_backend.link_mav.commands import CommandClient, CommandTimeout
from gcs_backend.link_mav.dialect import drone_gcs as mav
from gcs_backend.link_mav.mission_client import (CONTRACT_VER, MissionCancelled, MissionClient,
                                                 Waypoint, to_ascii)
from gcs_backend.link_mav.params import PARAM_MAP, ParamClient
from tests.test_mav_link import FakePi, _settle, _start

WPS = [Waypoint(expected_marker_id=1, alt_m=1.5, acceptance_radius_m=0.3, max_vel_mps=1.0),
       Waypoint(expected_marker_id=0, alt_m=1.0, acceptance_radius_m=0.3, max_vel_mps=1.0,
                action=mav.DRONE_ACTION_PICKUP)]


async def _up():
    """Liên kết đã biết đường về — mọi phép kiểm dưới đây bắt đầu từ đó."""
    link, bus, task, pi = await _start()
    pi.heartbeat()
    await _settle()
    pi.recv_all()
    return link, bus, task, pi


def _of(msgs, kind):
    return [m for m in msgs if m.get_type() == kind]


# ── lệnh (mục 4) ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lenh_thuong_duoc_ack_ngay():
    link, _, task, pi = await _up()
    client = CommandClient(link)
    fut = asyncio.create_task(client.mission_start())
    await _settle()
    sent = _of(pi.recv_all(), "COMMAND_LONG")
    assert len(sent) == 1 and sent[0].command == cmd.MISSION_START and sent[0].confirmation == 0
    pi.send(pi.mav.command_ack_encode(cmd.MISSION_START, mav.MAV_RESULT_ACCEPTED))
    res = await asyncio.wait_for(fut, 1.0)
    assert res.ok and res.attempts == 1
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_a7_phat_lai_tang_confirmation_0_1_2():
    """Giao ước 4.2: bản phát lại chuẩn TĂNG confirmation — nên không được chống trùng theo nó."""
    link, _, task, pi = await _up()
    client = CommandClient(link)
    fut = asyncio.create_task(client.mission_start())
    for _ in range(3):
        await asyncio.sleep(cmd.NORMAL_PERIOD_S + 0.05)
    with pytest.raises(CommandTimeout):
        await asyncio.wait_for(fut, 2.0)
    assert [m.confirmation for m in _of(pi.recv_all(), "COMMAND_LONG")] == [0, 1, 2]
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_lenh_khan_phat_lai_vo_han_va_bao_dong_sau_lan_3():
    """Một lệnh LAND dừng thử sau 3 giây là bỏ drone lại giữa không trung (giao ước 4.2)."""
    link, bus, task, pi = await _up()
    alerts = []
    bus.subscribe("command.unacked", lambda t, d: alerts.append(d))
    client = CommandClient(link)
    fut = asyncio.create_task(client.land())
    await asyncio.sleep(cmd.EMERGENCY_PERIOD_S * 5 + 0.1)
    assert not fut.done(), "lệnh khẩn không bao giờ tự bỏ cuộc"
    sent = _of(pi.recv_all(), "COMMAND_LONG")
    assert len(sent) > cmd.NORMAL_TRIES and [m.command for m in sent] == [cmd.LAND] * len(sent)
    assert alerts == [{"command": cmd.LAND, "attempts": 3}], "báo người vận hành đúng một lần"
    pi.send(pi.mav.command_ack_encode(cmd.LAND, mav.MAV_RESULT_ACCEPTED))
    assert (await asyncio.wait_for(fut, 1.0)).ok
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_disarm_mang_param2_21196_va_khong_co_duong_nao_arm():
    link, _, task, pi = await _up()
    client = CommandClient(link)
    fut = asyncio.create_task(client.disarm())
    await _settle()
    sent = _of(pi.recv_all(), "COMMAND_LONG")[0]
    assert (sent.command, sent.param1, sent.param2) == (cmd.ARM_DISARM, 0.0, cmd.DISARM_FORCE)
    assert not hasattr(client, "arm"), "GCS không được arm (giao ước 9.3)"
    pi.send(pi.mav.command_ack_encode(cmd.ARM_DISARM, mav.MAV_RESULT_ACCEPTED))
    await asyncio.wait_for(fut, 1.0)
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_a6_lenh_bi_tu_choi_van_la_mot_ket_qua_khong_phai_loi():
    link, _, task, pi = await _up()
    client = CommandClient(link)
    fut = asyncio.create_task(client.pause_continue(True))
    await _settle()
    pi.send(pi.mav.command_ack_encode(cmd.PAUSE_CONTINUE, mav.MAV_RESULT_UNSUPPORTED))
    res = await asyncio.wait_for(fut, 1.0)
    assert not res.ok and res.as_dict()["result_name"] == "MAV_RESULT_UNSUPPORTED"
    task.cancel()
    pi.close()


# ── nạp kế hoạch (mục 3) ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a2_nap_ke_hoach_hai_diem_theo_nhip_cua_pi():
    link, _, task, pi = await _up()
    client = MissionClient(link)
    fut = asyncio.create_task(client.upload(903, WPS, plan_name="Lấy hàng kho A"))
    await _settle()
    count = _of(pi.recv_all(), "DRONE_MISSION_COUNT")[0]
    assert (count.mission_id, count.count, count.contract_ver) == (903, 2, CONTRACT_VER)
    assert count.plan_name == "Lay hang kho A", "bỏ dấu trước khi gửi (P26)"

    for seq in (0, 1):
        pi.send(pi.mav.drone_mission_request_encode(903, seq))
        await _settle()
        item = _of(pi.recv_all(), "DRONE_MISSION_ITEM")[0]
        assert (item.mission_id, item.seq, item.expected_marker_id) == (903, seq,
                                                                        WPS[seq].expected_marker_id)
    pi.send(pi.mav.drone_mission_ack_encode(903, mav.DRONE_MISSION_ACCEPTED, b""))
    assert (await asyncio.wait_for(fut, 1.0)).ok
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_a4_pi_hoi_lai_cung_seq_thi_gcs_dap_lai():
    """Pi phát lại REQUEST khi mất ITEM (3.2). GCS không tự phát lại — cứ mỗi REQUEST là một ITEM."""
    link, _, task, pi = await _up()
    client = MissionClient(link)
    fut = asyncio.create_task(client.upload(903, WPS))
    await _settle()
    pi.recv_all()
    for _ in range(3):
        pi.send(pi.mav.drone_mission_request_encode(903, 0))
        await _settle()
    assert [i.seq for i in _of(pi.recv_all(), "DRONE_MISSION_ITEM")] == [0, 0, 0]
    fut.cancel()
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_a5_pi_bo_cuoc_thi_gcs_nhan_ly_do_nguyen_van():
    link, _, task, pi = await _up()
    client = MissionClient(link)
    fut = asyncio.create_task(client.upload(903, WPS))
    await _settle()
    reason = b"thieu diem 1 sau 5 lan hoi"
    pi.send(pi.mav.drone_mission_ack_encode(903, mav.DRONE_MISSION_ERR_TIMEOUT, reason))
    ack = await asyncio.wait_for(fut, 1.0)
    assert not ack.ok and ack.reason == reason.decode()
    assert ack.as_dict()["result_name"] == "DRONE_MISSION_ERR_TIMEOUT"
    task.cancel()
    pi.close()


@pytest.mark.asyncio
async def test_goi_cua_luot_cu_den_tre_khong_lan_vao_luot_moi():
    """P24 — trường mission_id mà Pi vừa thêm vào ITEM/REQUEST tồn tại đúng để chặn việc này."""
    link, _, task, pi = await _up()
    client = MissionClient(link)
    cu = asyncio.create_task(client.upload(903, WPS))
    await _settle()
    moi = asyncio.create_task(client.upload(904, WPS))
    await _settle()
    with pytest.raises(MissionCancelled):
        await asyncio.wait_for(cu, 1.0)
    pi.recv_all()
    pi.send(pi.mav.drone_mission_request_encode(903, 0))  # lượt cũ, đến trễ
    await _settle()
    assert _of(pi.recv_all(), "DRONE_MISSION_ITEM") == []
    pi.send(pi.mav.drone_mission_request_encode(904, 0))
    await _settle()
    assert _of(pi.recv_all(), "DRONE_MISSION_ITEM")[0].mission_id == 904
    moi.cancel()
    task.cancel()
    pi.close()


def test_to_ascii_cat_dung_gioi_han():
    assert to_ascii("Kho Đông lô 3", 20) == b"Kho Dong lo 3"
    assert len(to_ascii("x" * 40, 20)) == 20


# ── tham số (mục 9.4) ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a16_doc_du_tam_tham_so_va_doi_chieu():
    link, _, task, pi = await _up()
    client = ParamClient(link)
    fut = asyncio.create_task(client.request_all())
    await _settle()
    assert _of(pi.recv_all(), "PARAM_REQUEST_LIST"), "GCS phải hỏi trước"
    values = {"LOW_BATT_PCT": 25.0, "CRIT_BATT_PCT": 15.0, "LINK_LOST_S": 10.0,
              "MARKER_SRCH_S": 20.0, "GRIP_CONF_S": 3.0, "MAX_RETRIES": 3.0,
              "TAKEOFF_ALT_M": 5.0, "ACCEPT_RAD_M": 1.5}
    assert set(values) == set(PARAM_MAP)
    for i, (name, val) in enumerate(values.items()):
        pi.send(pi.mav.param_value_encode(name.encode(), val, mav.MAV_PARAM_TYPE_REAL32,
                                          len(values), i))
    assert await asyncio.wait_for(fut, 2.0) == values

    doi_chieu = client.compare({"low_battery_pct": 25.0, "link_lost_timeout_s": 15.0})
    assert doi_chieu["low_battery_pct"]["in_sync"] is True
    assert doi_chieu["link_lost_timeout_s"]["in_sync"] is False
    assert doi_chieu["max_retries"]["in_sync"] is None, "GCS chưa có giá trị thì không báo là khớp"
    task.cancel()
    pi.close()
