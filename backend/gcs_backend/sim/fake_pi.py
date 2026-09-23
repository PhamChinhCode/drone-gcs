"""Pi giả lập nói đúng giao ước GCS ↔ Pi 0.4 — đủ để chạy 10.A mà không cần ROS.

Thay cho `sim/fake_drone.py` của kênh ESP-NOW cũ. Hành vi bám bốn chỗ dễ sai nhất của hợp đồng:

- **Pi gọi ra trước** (2.1): phát `HEARTBEAT` 1 Hz tới GCS ngay khi khởi động, kể cả khi chưa nhận gì.
- **`LOCAL_POSITION_NED` + `ATTITUDE` chỉ phát khi `POS_VALID`** (5.1 ý 2), và `POS_VALID` chỉ bật
  sau khi odom đã "neo" — mô phỏng bằng việc neo sau khi leo được ~0,8 m (5.2b: đo trong Gazebo là
  1,6 s ở tốc độ leo 0,5 m/s). Nghĩa là **đậu trên đất thì GCS không có vị trí**, đúng nhánh thường
  gặp mà 5.2b cảnh báo.
- **Bất biến theo trạng thái** (4.2): lệnh phát lại nhiều lần chỉ đổi trạng thái một lần, và
  `confirmation` tăng dần không ảnh hưởng gì.
- **`DRONE_MISSION_ACK` chỉ gửi sau khi đã phán quyết** (3.2), kèm chuỗi lý do.
- **`MISSION_COMPLETE` giữ 1,5 s** rồi mới về `IDLE`, và nó là đích chung của **mọi** cách kết thúc —
  thứ nói chuyến bay thành công hay không là `flight_result` (8.5b), chốt lại tới khi cất cánh lần sau.

Không mô phỏng: gripper thật, camera, FC. `BATTERY_VALID` và `GLOBAL_POS_VALID` **luôn 0** vì drone
thật cũng vậy (5.2) — đừng "cải tiến" chỗ này, nó tồn tại để GCS không vẽ đồng hồ pin.
"""
from __future__ import annotations

import asyncio
import logging
import math
import pathlib
import time

from ..link_mav.codec import GCS, PI
from ..link_mav.tagmap import tagmap_crc
from ..link_mav.dialect import drone_gcs as mav

log = logging.getLogger(__name__)

CONTRACT_VER = 500
COMPLETE_HOLD_S = 1.5   # giữ MISSION_COMPLETE 3 chu kỳ telemetry (giao ước 11.5)
TICK = 0.05
CLIMB_MPS = 0.5
ANCHOR_ALT_M = 0.8      # leo tới đây thì camera thấy tag khác và odom neo -> POS_VALID bật (5.2b)
TAKEOFF_ALT_M = 1.5
ITEM_TIMEOUT_S = 1.0    # Pi chờ một ITEM rồi hỏi lại cùng seq
ITEM_RETRIES = 5

S = mav  # cho ngắn: S.DRONE_STATE_*


class FakePi(asyncio.DatagramProtocol):
    def __init__(self, gcs_addr: tuple, signing_key: bytes | None = None,
                 params: dict[str, float] | None = None) -> None:
        self.gcs_addr = gcs_addr
        self.mav = mav.MAVLink(file=None, srcSystem=PI[0], srcComponent=PI[1])
        if signing_key is not None:
            self.mav.signing.secret_key = signing_key
            self.mav.signing.sign_outgoing = True
            self.mav.signing.link_id = 1
        self.params = params or {"LOW_BATT_PCT": 25.0, "CRIT_BATT_PCT": 15.0, "LINK_LOST_S": 10.0,
                                 "MARKER_SRCH_S": 20.0, "GRIP_CONF_S": 3.0, "MAX_RETRIES": 3.0,
                                 "TAKEOFF_ALT_M": 5.0, "ACCEPT_RAD_M": 1.5}
        self.tags: dict[int, tuple[float, float, float]] = {}  # tag_id -> (n, e, d)
        self.tagmap_crc = 0
        self.state = S.DRONE_STATE_IDLE
        self.pos = [0.0, 0.0, 0.0]   # N, E, D
        self.yaw = 0.0
        self.anchored = False
        self.armed = False
        self.home: list[float] | None = None
        self.plan: list = []
        self.mission_id = 0
        self.wp_index = 0
        self.failsafe = 0
        self.gripper = S.DRONE_GRIP_OPEN
        self.flight_result = mav.DRONE_RESULT_UNKNOWN  # chốt lại tới khi cất cánh chuyến mới (8.5b)
        self.reject_reason: str | None = None     # đặt để bắt Pi từ chối kế hoạch kế tiếp
        self.contract_ver = CONTRACT_VER
        self._tr: asyncio.DatagramTransport | None = None
        self._loading: dict | None = None
        self._flight: asyncio.Task | None = None
        self._tasks: list[asyncio.Task] = []

    # ── vòng đời ─────────────────────────────────────────────────────────────────────────────

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> None:
        loop = asyncio.get_running_loop()
        self._tr, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, port))
        self._tasks = [asyncio.create_task(t()) for t in (self._loop_1hz, self._loop_2hz, self._loop_5hz,
                                                          self._loop_physics)]

    def stop(self) -> None:
        for t in self._tasks + ([self._flight] if self._flight else []):
            t.cancel()
        if self._tr:
            self._tr.close()

    @property
    def port(self) -> int:
        return self._tr.get_extra_info("sockname")[1]

    def send(self, msg) -> None:
        if self._tr is None:
            return
        buf = msg.pack(self.mav)
        self.mav.seq = (self.mav.seq + 1) % 256  # pack() KHÔNG tự tăng — lỗi tìm ra ở 10.A
        self._tr.sendto(buf, self.gcs_addr)

    def statustext(self, severity: int, text: str) -> None:
        """Chia đoạn 50 byte (7.5): cắt ngắn sẽ mất đúng phần cuối câu, phần chứa lý do."""
        raw = text.encode("ascii", "replace")
        if len(raw) <= 50:
            self.send(self.mav.statustext_encode(severity, raw, 0, 0))
            return
        sid = (int(time.time() * 10) % 255) + 1
        for i in range(0, len(raw), 50):
            self.send(self.mav.statustext_encode(severity, raw[i:i + 50], sid, i // 50))

    # ── chiều nhận ───────────────────────────────────────────────────────────────────────────

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self.gcs_addr = addr  # GCS có thể đổi cổng; Pi cũng chỉ giữ một đường về
        for msg in self.mav.parse_buffer(data) or []:
            if isinstance(msg, mav.MAVLink_bad_data):
                continue
            if (msg.get_srcSystem(), msg.get_srcComponent()) != GCS:
                continue  # bỏ im lặng, không log (2.2)
            self._on_msg(msg)

    def _on_msg(self, msg) -> None:
        kind = msg.get_type()
        if kind == "COMMAND_LONG":
            self._on_command(msg)
        elif kind == "DRONE_MISSION_COUNT":
            self._on_count(msg)
        elif kind == "DRONE_MISSION_ITEM":
            self._on_item(msg)
        elif kind == "PARAM_REQUEST_LIST":
            for i, (name, val) in enumerate(self.params.items()):
                self.send(self.mav.param_value_encode(name.encode(), val, mav.MAV_PARAM_TYPE_REAL32,
                                                      len(self.params), i))
        elif kind == "PARAM_SET":
            # ngưỡng chỉ sửa ở safety.yaml, nơi có người chịu trách nhiệm và có lịch sử git (9.4)
            self.statustext(4, "PARAM_SET bi bo qua: nguong chi sua o safety.yaml")
        elif kind == "TIMESYNC" and msg.tc1 == 0:
            self.send(self.mav.timesync_encode(time.time_ns(), msg.ts1))

    def _ack(self, command: int, result: int) -> None:
        self.send(self.mav.command_ack_encode(command, result))

    def _on_command(self, msg) -> None:
        c, R = msg.command, mav
        if c == mav.MAV_CMD_COMPONENT_ARM_DISARM:
            if msg.param1 != 0:
                # lệnh 400 CÓ được hỗ trợ; chỉ hành động arm bị từ chối vĩnh viễn (4.1, 9.3)
                self._ack(c, R.MAV_RESULT_DENIED)
                return
            self.armed = False
            self._abort_flight()
            self._set_state(S.DRONE_STATE_IDLE)
            self._ack(c, R.MAV_RESULT_ACCEPTED)
        elif c == mav.MAV_CMD_NAV_RETURN_TO_LAUNCH:
            self._ack(c, R.MAV_RESULT_ACCEPTED)
            if self.state != S.DRONE_STATE_RTH:
                self._set_result(mav.DRONE_RESULT_RTH)
                self._begin(self._do_rth())
        elif c == mav.MAV_CMD_NAV_LAND:
            self._ack(c, R.MAV_RESULT_ACCEPTED)
            if self.state != S.DRONE_STATE_EMERGENCY_LAND:
                self._set_result(mav.DRONE_RESULT_LANDED_CMD)
                self._begin(self._do_land())
        elif c == mav.MAV_CMD_DRONE_ABORT_MISSION:
            self._ack(c, R.MAV_RESULT_ACCEPTED)
            self._set_result(mav.DRONE_RESULT_ABORTED)
            self.plan, self._loading = [], None
            self._begin(self._do_land())
        elif c == mav.MAV_CMD_MISSION_START:
            if not self.plan:
                self._ack(c, R.MAV_RESULT_TEMPORARILY_REJECTED)
                self.statustext(4, "chua nap ke hoach nao")
                return
            self._ack(c, R.MAV_RESULT_ACCEPTED)
            if self.state in (S.DRONE_STATE_IDLE, S.DRONE_STATE_MISSION_COMPLETE):
                self._begin(self._run_mission())
        elif c == mav.MAV_CMD_DO_PAUSE_CONTINUE:
            self._ack(c, R.MAV_RESULT_UNSUPPORTED)  # đã cấp số, FSM chưa hiện thực (11.P7)
        else:
            self._ack(c, R.MAV_RESULT_UNSUPPORTED)  # lệnh lạ KHÔNG BAO GIỜ im lặng (4.1)

    # ── nạp kế hoạch (3.2) ───────────────────────────────────────────────────────────────────

    def _on_count(self, msg) -> None:
        if self._loading:
            self.statustext(4, f"COUNT moi {msg.mission_id}: huy luot dang nap")
        if msg.contract_ver and msg.contract_ver // 10000 != CONTRACT_VER // 10000:
            self._mission_ack(msg.mission_id, mav.DRONE_MISSION_ERR_CONTRACT,
                              f"lech MAJOR: Pi {CONTRACT_VER}, GCS {msg.contract_ver}")
            return
        if msg.count == 0 or msg.count > 16:
            self._mission_ack(msg.mission_id, mav.DRONE_MISSION_ERR_COUNT, f"count {msg.count} ngoai dai 1-16")
            return
        if self.state != S.DRONE_STATE_IDLE:
            self._mission_ack(msg.mission_id, mav.DRONE_MISSION_ERR_BUSY, "Pi khong o IDLE")
            return
        self._loading = {"id": msg.mission_id, "n": msg.count, "items": {}, "seq": 0, "tries": 0}
        self._request(0)

    def _request(self, seq: int) -> None:
        if self._loading is None:
            return
        self._loading["seq"] = seq
        self.send(self.mav.drone_mission_request_encode(self._loading["id"], seq))
        loop = asyncio.get_running_loop()
        loop.call_later(ITEM_TIMEOUT_S, self._item_timeout, self._loading["id"], seq)

    def _item_timeout(self, mission_id: int, seq: int) -> None:
        ld = self._loading
        if ld is None or ld["id"] != mission_id or ld["seq"] != seq or seq in ld["items"]:
            return
        ld["tries"] += 1
        if ld["tries"] >= ITEM_RETRIES:
            self._loading = None
            self._mission_ack(mission_id, mav.DRONE_MISSION_ERR_TIMEOUT,
                              f"thieu diem {seq} sau {ITEM_RETRIES} lan hoi")
            return
        self._request(seq)

    def _on_item(self, msg) -> None:
        ld = self._loading
        if ld is None or msg.mission_id != ld["id"] or msg.seq != ld["seq"]:
            return  # gói trùng do phát lại, hoặc gói của lượt cũ đến trễ — bỏ, không tăng bộ đếm
        ld["items"][msg.seq] = msg
        ld["tries"] = 0
        if len(ld["items"]) < ld["n"]:
            self._request(msg.seq + 1)
            return
        items = [ld["items"][i] for i in range(ld["n"])]
        self._loading = None
        self._judge(ld["id"], items)

    def _judge(self, mission_id: int, items: list) -> None:
        """Vai trò của mission_manager_node: bên DUY NHẤT được từ chối kế hoạch (1.1)."""
        if self.reject_reason:
            self._mission_ack(mission_id, mav.DRONE_MISSION_ERR_PARAM, self.reject_reason)
            return
        unknown = next((i.expected_marker_id for i in items if i.expected_marker_id not in self.tags), None)
        if unknown is not None:
            self._mission_ack(mission_id, mav.DRONE_MISSION_ERR_UNKNOWN_TAG,
                              f"tag {unknown} khong co trong tags.yaml")
            return
        bad = next((i for i in items if not 0 < i.max_vel_mps <= 1.9), None)
        if bad is not None:
            self._mission_ack(mission_id, mav.DRONE_MISSION_ERR_PARAM,
                              f"max_vel_mps {bad.max_vel_mps:.2f} ngoai dai (0; 1,9]")
            return
        self.plan, self.mission_id, self.wp_index = items, mission_id, 0
        self._mission_ack(mission_id, mav.DRONE_MISSION_ACCEPTED, "")

    def _mission_ack(self, mission_id: int, result: int, reason: str) -> None:
        self.send(self.mav.drone_mission_ack_encode(mission_id, result, reason.encode("ascii", "replace")[:50]))

    # ── bay ──────────────────────────────────────────────────────────────────────────────────

    def _set_state(self, s: int) -> None:
        if s != self.state:
            self.state = s

    def _set_result(self, r: int) -> None:
        """Lý do ĐẦU TIÊN thắng: hạ vì failsafe mà GCS bấm huỷ thì vẫn là FAILSAFE (8.5b ý 2)."""
        if self.flight_result == mav.DRONE_RESULT_UNKNOWN:
            self.flight_result = r

    def _begin(self, coro) -> None:
        self._abort_flight()
        self._flight = asyncio.create_task(self._guard(coro))

    def _abort_flight(self) -> None:
        if self._flight and not self._flight.done():
            self._flight.cancel()

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("lỗi trong pha bay giả lập")

    async def _fly_to(self, n: float, e: float, d: float, vel: float) -> None:
        while math.dist(self.pos, [n, e, d]) > 0.15:
            step = max(vel, 0.1) * TICK
            for i, tgt in enumerate((n, e, d)):
                delta = tgt - self.pos[i]
                self.pos[i] += math.copysign(min(abs(delta), step), delta)
            await asyncio.sleep(TICK)

    async def _takeoff(self) -> None:
        self.flight_result = mav.DRONE_RESULT_UNKNOWN  # xoá đúng lúc cất cánh, không phải lúc nạp (8.5b)
        self.armed = True
        self._set_state(S.DRONE_STATE_TAKEOFF)
        await self._fly_to(self.pos[0], self.pos[1], -TAKEOFF_ALT_M, CLIMB_MPS)
        if self.home is None and self.anchored:
            # home chốt ở tick đầu tiên có POS_VALID trong lúc TAKEOFF, không phải tick đầu tiên
            # có bất kỳ vị trí nào — nếu không, toạ độ nhà thuộc khung chưa neo (5.2b ý 4)
            self.home = [self.pos[0], self.pos[1]]

    async def _run_mission(self) -> None:
        await self._takeoff()
        for i, item in enumerate(self.plan):
            self.wp_index = i
            n, e, d = self.tags[item.expected_marker_id]
            self._set_state(S.DRONE_STATE_ENROUTE)
            await self._fly_to(n, e, d - item.alt_m, item.max_vel_mps)
            self._set_state(S.DRONE_STATE_MARKER_SEARCH)
            await asyncio.sleep(0.2)
            self._set_state(S.DRONE_STATE_PRECISION_LAND)
            await self._fly_to(n, e, d, CLIMB_MPS)
            if item.action != mav.DRONE_ACTION_NONE:
                self._set_state(S.DRONE_STATE_ACTUATE_GRIPPER)
                await asyncio.sleep(0.3)
                self.gripper = (S.DRONE_GRIP_CLOSED if item.action == mav.DRONE_ACTION_PICKUP
                                else S.DRONE_GRIP_OPEN)
            if i < len(self.plan) - 1:
                self._set_state(S.DRONE_STATE_TAKEOFF)
                await self._fly_to(n, e, d - TAKEOFF_ALT_M, CLIMB_MPS)
        self.armed = False
        self._set_result(mav.DRONE_RESULT_COMPLETED)
        # MISSION_COMPLETE là đích chung của CẢ huỷ lệnh, RTH, NAV_LAND, hết lượt thử và hết kế hoạch —
        # nên nó KHÔNG có nghĩa là thành công; flight_result mới nói điều đó (8.5b). Giữ 1,5 s = ba chu
        # kỳ telemetry: bản 0.4 chỉ giữ một tick 200 ms nên GCS trượt mất 60% số lần (11.5 P30).
        self._set_state(S.DRONE_STATE_MISSION_COMPLETE)
        await asyncio.sleep(COMPLETE_HOLD_S)
        self._set_state(S.DRONE_STATE_IDLE)

    async def _do_rth(self) -> None:
        self._set_state(S.DRONE_STATE_RTH)
        if self.home is None:
            # chưa neo mà đã cất cánh -> không biết nhà -> hạ tại chỗ. An toàn và trung thực (5.2b)
            await self._do_land()
            return
        await self._fly_to(self.home[0], self.home[1], -TAKEOFF_ALT_M, 1.0)
        await self._do_land()

    async def _do_land(self) -> None:
        self._set_state(S.DRONE_STATE_EMERGENCY_LAND)  # hạ cánh CHUNG; failsafe_type mới nói khẩn (8.5)
        await self._fly_to(self.pos[0], self.pos[1], 0.0, CLIMB_MPS)
        self.armed = False
        self._set_state(S.DRONE_STATE_MISSION_COMPLETE)
        await asyncio.sleep(COMPLETE_HOLD_S)
        self._set_state(S.DRONE_STATE_IDLE)

    # ── phát định kỳ (5.1) ───────────────────────────────────────────────────────────────────

    @property
    def pos_valid(self) -> bool:
        return self.anchored

    def valid_flags(self) -> int:
        f = mav.DRONE_FC_LINK_VALID | mav.DRONE_EKF_HEALTHY | mav.DRONE_GRIPPER_VALID
        if self.pos_valid:
            f |= mav.DRONE_POS_VALID
        if self.home is not None:
            f |= mav.DRONE_HOME_VALID
        return f  # GLOBAL_POS và BATTERY: luôn hạ, đúng như drone thật (5.2)

    async def _loop_1hz(self) -> None:
        while True:
            self.send(self.mav.heartbeat_encode(mav.MAV_TYPE_QUADROTOR, mav.MAV_AUTOPILOT_GENERIC,
                                                0, 0, mav.MAV_STATE_ACTIVE))
            await asyncio.sleep(1.0)

    async def _loop_2hz(self) -> None:
        while True:
            st = (mav.DRONE_ARMED if self.armed else 0) | mav.DRONE_PI_HAS_AUTHORITY
            if self.gripper == S.DRONE_GRIP_CLOSED:
                st |= mav.DRONE_CARRYING
            home = self.home or [0.0, 0.0]
            self.send(self.mav.drone_telemetry_encode(
                stamp_us=time.time_ns() // 1000, mission_id=self.mission_id, contract_ver=self.contract_ver,
                lat=0, lon=0, marker_id_tracking=-1, alt_m=-self.pos[2],
                vel_ned=[0.0, 0.0, 0.0], battery_pct=0.0, battery_v=0.0,
                valid_flags=self.valid_flags(), status_flags=st, gcs_rssi_dbm=-70,
                mission_state=self.state, current_wp_index=self.wp_index, wp_total=len(self.plan),
                retry_count=0, gripper_state=self.gripper, failsafe_type=self.failsafe,
                expected_marker_id=-1, tagmap_crc=self.tagmap_crc, flight_result=self.flight_result,
                # GPS thô (0.7): giả lập có fix 3D ngoài trời, 14 vệ tinh, sai số 1,2 m.
                gps_fix_type=3, gps_sats=14, gps_hacc_cm=120,
                home_n_mm=round(home[0] * 1000), home_e_mm=round(home[1] * 1000)))
            await asyncio.sleep(0.5)

    async def _loop_5hz(self) -> None:
        while True:
            if self.pos_valid:  # không phát = không biết; đây là cách duy nhất giữ R3 với bản tin chuẩn
                t = int(time.monotonic() * 1000) & 0xFFFFFFFF
                self.send(self.mav.local_position_ned_encode(t, *self.pos, 0.0, 0.0, 0.0))
                self.send(self.mav.attitude_encode(t, 0.0, 0.0, self.yaw, 0.0, 0.0, 0.0))
            await asyncio.sleep(0.2)

    async def _loop_physics(self) -> None:
        while True:
            if not self.anchored and -self.pos[2] >= ANCHOR_ALT_M:
                self.anchored = True  # cờ latching: đã neo thì không hạ xuống nữa (5.2b)
                if self.home is None and self.state == S.DRONE_STATE_TAKEOFF:
                    self.home = [self.pos[0], self.pos[1]]
            await asyncio.sleep(TICK)


async def start_fake_pi(gcs_addr: tuple, tags: dict[int, tuple[float, float, float]],
                        tagmap_crc: int = 0, signing_key: bytes | None = None) -> FakePi:
    pi = FakePi(gcs_addr, signing_key)
    pi.tags, pi.tagmap_crc = tags, tagmap_crc
    await pi.start()
    return pi


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Pi giả lập nói giao ước GCS <-> Pi 0.4")
    ap.add_argument("--gcs", default="127.0.0.1:14550", help="địa chỉ GCS để gọi ra trước (2.1)")
    ap.add_argument("--port", type=int, default=14551, help="cổng Pi lắng nghe")
    ap.add_argument("--key", help="file khoá chữ ký 32 byte; bỏ qua = chạy không ký (chỉ mạng kín)")
    args = ap.parse_args()
    host, port = args.gcs.rsplit(":", 1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        key = pathlib.Path(args.key).read_bytes() if args.key else None
        pi = FakePi((host, int(port)), key)
        # khu vực mẫu trùng bản đồ tag mà GCS tạo lần chạy đầu
        pi.tags = {0: (0.0, 0.0, 0.0), 1: (10.0, 0.0, 0.0), 2: (10.0, 10.0, 0.0),
                   3: (-15.0, 25.0, 0.0), 4: (-18.0, -20.0, 0.0)}
        # CRC tính bằng ĐÚNG hàm của GCS: nếu hai bên lệch công thức thì lệch ở đây chứ không phải
        # lúc bay (giao ước 8.6, phép kiểm A15)
        pi.tagmap_crc = tagmap_crc([{"tag_id": t, "pos_n_m": n, "pos_e_m": e, "pos_d_m": d}
                                    for t, (n, e, d) in pi.tags.items()])
        await pi.start("0.0.0.0", int(args.port))
        log.info("Pi giả lập nghe :%s, phát heartbeat tới %s, tagmap_crc %08X",
                 args.port, args.gcs, pi.tagmap_crc)
        await asyncio.Event().wait()

    asyncio.run(run())


if __name__ == "__main__":
    _main()
