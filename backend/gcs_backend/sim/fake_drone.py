"""Drone giả lập nói ĐÚNG giao thức mục 5 (mục 7.5 — hạng mục bắt buộc, tầng kiểm thử T0).

Một tiến trình đóng ba vai: dongle ESP32-GCS (thống kê chan 0x02), kênh không khí ESP-NOW (rớt gói/trễ),
và Pi 4 (động học bậc nhất, FSM nhiệm vụ có thẩm quyền, pin, telemetry, ACK, failsafe).

Chạy:   python -m gcs_backend.sim.fake_drone --port 5760
GCS:    GCS_LINK_URL=tcp://127.0.0.1:5760

Cổng điều khiển lỗi (mặc định 5761), gửi từng dòng lệnh text, ví dụ bằng `ncat 127.0.0.1 5761`:
  drop 0.3            rớt 30 % gói không khí hai chiều
  delay 20 40         trễ 20 ms ± 40 ms jitter
  outage 8            mất liên kết không khí 8 s
  reject 0x11 3       từ chối msg 0x11 với mã result 3 (0 = bỏ)
  noeffect on|off     ACK lệnh nhưng không đổi trạng thái (nghiệm thu A11)
  hang 10             Pi 4 treo 10 s (dongle vẫn gửi thành công → "drone im lặng")
  dongle off|on       dongle ngừng trả lời serial ("mất dongle")
  param NAME VALUE    đặt lệch một tham số (nghiệm thu A10)
  battery 20          đặt % pin
  status              in trạng thái
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import logging
import math
import random
import time

from ..link import frame as fr
from ..link import messages as m
from ..link.auth import emergency_confirm_code
from ..link.serial_io import Transport
from ..link.session import LinkEndpoint, LinkTimeout

log = logging.getLogger("fake_drone")
MS = m.MissionState

DEFAULT_PARAMS = {
    "low_battery_pct": 25.0, "critical_battery_pct": 15.0, "link_lost_timeout_s": 10.0,
    "marker_search_timeout_s": 20.0, "max_retries": 3.0, "takeoff_alt_m": 5.0, "acceptance_radius_m": 1.5,
}


class _StreamTransport(Transport):
    def __init__(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        self._r, self._w = r, w

    async def open(self) -> None:
        pass

    async def read(self) -> bytes:
        return await self._r.read(4096)

    async def write(self, data: bytes) -> None:
        self._w.write(data)
        await self._w.drain()

    async def close(self) -> None:
        self._w.close()


class Faults:
    def __init__(self) -> None:
        self.drop = 0.0
        self.delay_ms = 0.0
        self.jitter_ms = 0.0
        self.outage_until = 0.0
        self.hang_until = 0.0
        self.dongle_on = True
        self.no_effect = False
        self.reject: dict[int, int] = {}

    def air_lost(self) -> bool:
        return time.monotonic() < self.outage_until or random.random() < self.drop


class DroneEndpoint(LinkEndpoint):
    """Điểm cuối phía drone + mô phỏng dongle và kênh không khí."""

    def __init__(self, auth_key: bytes, faults: Faults) -> None:
        super().__init__(src=fr.NODE_PI4, dst=fr.NODE_GCS, auth_key=auth_key)
        self.faults = faults
        self.dongle = {"tx_count": 0, "tx_ok": 0, "tx_fail": 0, "rx_count": 0}

    def on_frame(self, chan: int, raw: bytes) -> None:
        if chan != fr.CHAN_AIR:
            return
        if not self.faults.dongle_on:
            return
        self.dongle["tx_count"] += 1
        if self.faults.air_lost():
            self.dongle["tx_fail"] += 1
            return
        self.dongle["tx_ok"] += 1  # MAC ACK thành công kể cả khi Pi 4 treo
        if time.monotonic() < self.faults.hang_until:
            return
        delay = self._delay()
        if delay:
            asyncio.get_running_loop().call_later(delay, self.on_unit, raw)
        else:
            self.on_unit(raw)

    async def write_unit(self, chan: int, raw: bytes) -> None:
        if chan == fr.CHAN_AIR:
            if not self.faults.dongle_on or time.monotonic() < self.faults.hang_until or self.faults.air_lost():
                return
            self.dongle["rx_count"] += 1
            delay = self._delay()
            if delay:
                await asyncio.sleep(delay)
        elif chan == fr.CHAN_DONGLE and not self.faults.dongle_on:
            return
        await super().write_unit(chan, raw)

    def _delay(self) -> float:
        d = self.faults.delay_ms + random.uniform(0, self.faults.jitter_ms)
        return d / 1000.0 if d > 0 else 0.0


class FakeDrone:
    TICK = 0.02

    def __init__(self, auth_key: bytes, faults: Faults) -> None:
        self.key = auth_key
        self.faults = faults
        self.ep = DroneEndpoint(auth_key, faults)
        self.ep.handlers.append(self.on_message)
        self.t0 = time.monotonic()
        # động học
        self.pos = [0.0, 0.0, 0.0]
        self.vel = [0.0, 0.0, 0.0]
        self.yaw = 0.0
        self.roll = self.pitch = 0.0
        self.sp = [0.0, 0.0, 0.0]
        self.sp_vel = 2.0
        self.sp_yaw: float | None = None
        self.armed = False
        self.carrying = False
        self.gripper = m.GripperState.OPEN
        self.battery = 100.0
        self.params = dict(DEFAULT_PARAMS)
        # nhiệm vụ
        self.fsm = MS.IDLE
        self.prev_fsm = MS.IDLE
        self.mission_id = 0
        self.plan: list[m.MissionWp] = []
        self.plan_buf: dict[int, m.MissionWp] = {}
        self.plan_total = 0
        self.plan_ready = False
        self.wp_index = 0
        self.expected_tag = -1
        self.retry_count = 0
        self.failsafe = m.FailsafeType.NONE
        self.tag_lock = False
        self.paused = False
        self.activity: asyncio.Task | None = None
        self.tasks: list[asyncio.Task] = []
        self._dongle_seq = itertools.count(1)
        # bản đồ tag
        self.tags: dict[int, tuple] = {}
        self.map_buf: dict[int, tuple] = {}
        self.last_gcs_hb = time.monotonic()
        self.link_lost_fired = False
        self.low_batt_fired = False
        self.crit_batt_fired = False

    # ── tiện ích ─────────────────────────────────────────────────────────────────────────────

    def t_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000) & 0xFFFFFFFF

    @property
    def map_crc(self) -> int:
        return m.map_crc_from_wire(list(self.tags.values()))

    @property
    def airborne(self) -> bool:
        return self.pos[2] < -0.15

    def set_fsm(self, s: MS) -> None:
        if s != self.fsm:
            self.prev_fsm, self.fsm = self.fsm, s
            self.send_mission_state()

    def event(self, severity: int, category: str, code: int, text: str) -> None:
        ev = m.Event(self.t_ms(), severity, m.EVENT_CATEGORY_IDS[category], code, text)

        async def _send() -> None:
            try:
                await self.ep.send(ev)
            except LinkTimeout:
                pass
        asyncio.get_running_loop().create_task(_send())

    def send_mission_state(self) -> None:
        asyncio.get_running_loop().create_task(self.ep.send(m.MissionStateMsg(
            self.t_ms(), self.mission_id, int(self.fsm), int(self.prev_fsm), self.wp_index & 0xFF,
            len(self.plan) & 0xFF, self.expected_tag, self.retry_count, int(self.failsafe))))

    def start_activity(self, coro) -> None:
        if self.activity and not self.activity.done():
            self.activity.cancel()
        self.paused = False
        self.activity = asyncio.get_running_loop().create_task(self._guard(coro))

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("hoạt động lỗi")

    # ── xử lý bản tin ───────────────────────────────────────────────────────────────────────

    def on_message(self, msg: m.Message, hdr: fr.Header) -> int | None:
        rej = self.faults.reject.get(hdr.msg_id)
        if rej and hdr.need_ack:
            return rej
        if isinstance(msg, m.HeartbeatGcs):
            self.last_gcs_hb = time.monotonic()
            self.ep.note_peer_session(hdr.src, msg.session_id)
            if self.link_lost_fired:
                self.link_lost_fired = False
                self.event(0, "link", 0, "liên kết GCS đã khôi phục")
            return None
        effect = not self.faults.no_effect
        R = m.AckResult

        if isinstance(msg, m.CmdSimple):
            a = msg.action
            if a == m.SimpleAction.ARM:
                if self.airborne:
                    return R.REJECT_WRONG_STATE
                self.armed = self.armed or effect
            elif a == m.SimpleAction.DISARM:
                if self.airborne:
                    return R.REJECT_WRONG_STATE
                if effect:
                    self.armed = False
            elif a == m.SimpleAction.TAKEOFF:
                if not self.armed or self.airborne:
                    return R.REJECT_WRONG_STATE
                alt = msg.param / 100 if msg.param > 0 else self.params["takeoff_alt_m"]
                if effect:
                    self.start_activity(self.do_takeoff(alt))
            elif a == m.SimpleAction.LAND:
                if effect:
                    self.start_activity(self.do_land(MS.PRECISION_LAND, None))
            elif a == m.SimpleAction.HOLD:
                if effect:
                    self.start_activity(self.do_hold())
            elif a == m.SimpleAction.PRECISION_LAND:
                if msg.param not in self.tags:
                    return R.REJECT_INVALID_ARG
                if effect:
                    self.start_activity(self.do_precision_land_at(msg.param))
            elif a == m.SimpleAction.RTH:
                if effect:
                    self.start_activity(self.do_rth())
            else:
                return R.REJECT_UNSUPPORTED
            return R.OK

        if isinstance(msg, m.CmdGoto):
            if not self.armed or not self.airborne:
                return R.REJECT_WRONG_STATE
            x, y, z = msg.x_mm / 1000, msg.y_mm / 1000, msg.z_mm / 1000
            if msg.ref_frame == m.RefFrame.MAP_NED:
                tgt = [x, y, z]
            elif msg.ref_frame == m.RefFrame.TAG_RELATIVE:
                t = self.tags.get(msg.ref_tag_id)
                if t is None:
                    return R.REJECT_INVALID_ARG
                tgt = [t[1] / 1000 + x, t[2] / 1000 + y, t[3] / 1000 + z]
            elif msg.ref_frame == m.RefFrame.BODY_RELATIVE:
                c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
                tgt = [self.pos[0] + c * x - s * y, self.pos[1] + s * x + c * y, self.pos[2] + z]
            else:
                return R.REJECT_INVALID_ARG
            yaw = None if msg.flags & 0x01 else msg.yaw_cdeg / 100
            vel = msg.max_vel_cms / 100 or 2.0
            if effect:
                self.start_activity(self.do_goto(tgt, vel, yaw, climb_first=bool(msg.flags & 0x02)))
            return R.OK

        if isinstance(msg, m.MissionBegin):
            if self.fsm not in (MS.IDLE, MS.MISSION_COMPLETE):
                return R.REJECT_BUSY
            if msg.map_crc != self.map_crc:
                return R.REJECT_MAP_MISMATCH
            self.plan_buf, self.plan_total, self.plan_ready = {}, msg.total, False
            self._begin_mission_id = msg.mission_id
            return R.OK
        if isinstance(msg, m.MissionWp):
            if msg.mission_id != getattr(self, "_begin_mission_id", None) or msg.seq >= self.plan_total:
                return R.REJECT_INVALID_ARG
            self.plan_buf[msg.seq] = msg
            return R.OK
        if isinstance(msg, m.MissionEnd):
            if msg.mission_id != getattr(self, "_begin_mission_id", None):
                return R.REJECT_INVALID_ARG
            if len(self.plan_buf) != self.plan_total or m.compute_wp_crc(list(self.plan_buf.values())) != msg.wp_crc:
                return R.REJECT_CRC
            self.plan = [self.plan_buf[i] for i in range(self.plan_total)]
            self.mission_id, self.plan_ready, self.wp_index = msg.mission_id, True, 0
            self.send_mission_state()
            return R.OK
        if isinstance(msg, m.MissionCtrl):
            a = msg.action
            if a == m.MissionCtrlAction.START:
                if not self.plan_ready or msg.mission_id != self.mission_id:
                    return R.REJECT_INVALID_ARG
                if self.fsm not in (MS.IDLE, MS.MISSION_COMPLETE) or self.airborne:
                    return R.REJECT_WRONG_STATE
                if effect:
                    self.start_activity(self.run_mission())
            elif a == m.MissionCtrlAction.PAUSE:
                self.paused = self.paused or effect
            elif a == m.MissionCtrlAction.RESUME:
                if effect:
                    self.paused = False
            elif a == m.MissionCtrlAction.ABORT:
                if effect:
                    self.start_activity(self.do_rth())
            else:
                return R.REJECT_UNSUPPORTED
            return R.OK

        if isinstance(msg, m.Emergency):
            a = msg.action
            if a == m.EmergencyAction.KILL:
                if msg.confirm_code != emergency_confirm_code(a, msg.cmd_seq, self.key):
                    return R.REJECT_AUTH
                if effect:
                    if self.activity:
                        self.activity.cancel()
                    self.armed = False
                    self.set_fsm(MS.FAILSAFE)
                    self.event(3, "command", 5, "KILL — ngắt động cơ")
            elif a in (m.EmergencyAction.RTH, m.EmergencyAction.ABORT_MISSION):
                if effect and self.fsm != MS.RTH:
                    self.start_activity(self.do_rth())
            elif a == m.EmergencyAction.LAND_NOW:
                if effect and self.fsm != MS.EMERGENCY_LAND:
                    self.start_activity(self.do_land(MS.EMERGENCY_LAND, None))
            elif a == m.EmergencyAction.HOLD:
                if effect:
                    self.start_activity(self.do_hold())
            else:
                return R.REJECT_UNSUPPORTED
            return R.OK

        if isinstance(msg, m.ParamSet):
            name = m.PARAM_IDS.get(msg.param_id)
            if name not in self.params:
                return R.REJECT_INVALID_ARG
            self.params[name] = float(msg.value)
            self.ep.enqueue_raw(1, self.ep.build(m.ParamValue(msg.param_id, len(self.params), msg.value),
                                                 self.ep.next_seq()))
            return R.OK
        if isinstance(msg, m.Request):
            if msg.what == m.RequestWhat.PARAMS:
                for k, v in self.params.items():
                    self.ep.enqueue_raw(1, self.ep.build(m.ParamValue(m.PARAM_NAMES[k], len(self.params), v),
                                                         self.ep.next_seq()))
            elif msg.what == m.RequestWhat.MISSION_STATE:
                self.send_mission_state()
            return R.OK

        if isinstance(msg, m.TagmapBegin):
            self.map_buf = {}
            self._map_total = msg.total
            return R.OK
        if isinstance(msg, m.TagmapEntry):
            self.map_buf[msg.tag_id] = (msg.tag_id, msg.n_mm, msg.e_mm, msg.d_mm, msg.yaw_cdeg, msg.size_mm, msg.kind)
            return R.OK
        if isinstance(msg, m.TagmapEnd):
            if len(self.map_buf) != msg.total or m.map_crc_from_wire(list(self.map_buf.values())) != msg.map_crc:
                return R.REJECT_CRC
            self.tags = dict(self.map_buf)
            self.event(0, "marker", 0, f"đã nhận bản đồ {len(self.tags)} tag, crc {msg.map_crc:08X}")
            return R.OK
        return None

    # ── hoạt động bay ────────────────────────────────────────────────────────────────────────

    async def tick(self) -> None:
        await asyncio.sleep(self.TICK)
        while self.paused:
            self.sp = list(self.pos)
            await asyncio.sleep(self.TICK)

    def _dist(self, tgt, horiz=False) -> float:
        dn, de = tgt[0] - self.pos[0], tgt[1] - self.pos[1]
        dd = 0 if horiz else tgt[2] - self.pos[2]
        return math.sqrt(dn * dn + de * de + dd * dd)

    async def _fly(self, tgt, vel, yaw=None, radius=0.15) -> None:
        self.sp, self.sp_vel = list(tgt), vel
        if yaw is None and self._dist(tgt, horiz=True) > 1.0:
            yaw = math.degrees(math.atan2(tgt[1] - self.pos[1], tgt[0] - self.pos[0])) % 360
        self.sp_yaw = yaw
        while self._dist(tgt) > radius:
            await self.tick()

    async def do_takeoff(self, alt: float) -> None:
        self.armed = True
        self.set_fsm(MS.TAKEOFF)
        await self._fly([self.pos[0], self.pos[1], -alt], 1.5)
        self.set_fsm(MS.IDLE)

    async def do_goto(self, tgt, vel, yaw, climb_first=False) -> None:
        self.set_fsm(MS.ENROUTE)
        if climb_first:
            await self._fly([self.pos[0], self.pos[1], tgt[2]], vel)
        await self._fly(tgt, vel, yaw)
        self.set_fsm(MS.IDLE)

    async def do_hold(self) -> None:
        self.sp = list(self.pos)
        while True:
            await self.tick()

    async def do_land(self, state: MS, tag_id: int | None) -> None:
        self.set_fsm(state)
        await self._fly([self.pos[0], self.pos[1], 0.0], 0.6 if state == MS.PRECISION_LAND else 1.0)
        self.armed = False
        self.tag_lock = False
        self.set_fsm(MS.IDLE)

    async def do_precision_land_at(self, tag_id: int) -> None:
        t = self.tags[tag_id]
        n, e, d = t[1] / 1000, t[2] / 1000, t[3] / 1000
        self.expected_tag = tag_id
        await self._fly([n, e, min(self.pos[2], d - 3.0)], 2.0)
        await self._search_and_descend(tag_id, d)
        self.armed = False
        self.set_fsm(MS.IDLE)

    async def _search_and_descend(self, tag_id: int, ground_d: float, stop_alt: float = 0.0) -> None:
        self.set_fsm(MS.MARKER_SEARCH)
        await asyncio.sleep(random.uniform(0.8, 1.8))
        self.tag_lock = True
        self.set_fsm(MS.PRECISION_LAND)
        t = self.tags.get(tag_id)
        n, e = (t[1] / 1000, t[2] / 1000) if t else (self.sp[0], self.sp[1])
        await self._fly([n, e, ground_d - stop_alt], 0.5, radius=0.05)
        self.tag_lock = False

    async def run_mission(self) -> None:
        self.armed = True
        self.retry_count = 0
        self.failsafe = m.FailsafeType.NONE
        for i, wp in enumerate(self.plan):
            self.wp_index = i
            self.expected_tag = wp.tag_id
            self.send_mission_state()
            tgt = [wp.pos_n_mm / 1000, wp.pos_e_mm / 1000, wp.pos_d_mm / 1000]
            vel = wp.max_vel_cms / 100 or 2.0
            if i == 0 and not self.airborne:
                self.set_fsm(MS.TAKEOFF)
                await self._fly([self.pos[0], self.pos[1], tgt[2]], 1.5)
            if wp.flags & m.WPF_PRECISION_LAND:
                await self._search_and_descend(wp.tag_id, 0.0)
            else:
                self.set_fsm(MS.ENROUTE)
                if wp.flags & m.WPF_REQUIRE_TAG_LOCK:
                    await self._fly(tgt, vel, radius=max(0.2, wp.accept_radius_cm / 100))
                    self.set_fsm(MS.MARKER_SEARCH)
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                    self.tag_lock = True
                else:
                    await self._fly(tgt, vel, radius=max(0.2, wp.accept_radius_cm / 100))
                    self.tag_lock = False
            if wp.action in (m.WpAction.PICKUP, m.WpAction.DROPOFF):
                self.set_fsm(MS.ACTUATE_GRIPPER)
                self.gripper = m.GripperState.MOVING
                await asyncio.sleep(1.5)
                self.carrying = wp.action == m.WpAction.PICKUP
                self.gripper = m.GripperState.CLOSED if self.carrying else m.GripperState.OPEN
                self.event(0, "gripper", wp.action, "đã gắp hàng" if self.carrying else "đã thả hàng")
                self.tag_lock = False
            if wp.loiter_s:
                await asyncio.sleep(wp.loiter_s)
        if not self.airborne:
            self.armed = False
        self.tag_lock = False
        self.plan_ready = False
        self.set_fsm(MS.MISSION_COMPLETE)
        self.event(0, "mission", 0, f"nhiệm vụ {self.mission_id} hoàn thành")

    async def do_rth(self) -> None:
        self.set_fsm(MS.RTH)
        self.armed = self.armed or self.airborne
        home = next((t for t in self.tags.values() if t[6] == m.TagKind.HOME), None)
        hn, he = (home[1] / 1000, home[2] / 1000) if home else (0.0, 0.0)
        cruise = -abs(self.params["takeoff_alt_m"])
        if self.airborne:
            await self._fly([self.pos[0], self.pos[1], min(self.pos[2], cruise)], 1.5)
            await self._fly([hn, he, min(self.pos[2], cruise)], 3.0)
            await self._fly([hn, he, 0.0], 0.8, radius=0.05)
        self.armed = False
        self.set_fsm(MS.IDLE)

    # ── vòng mô phỏng ────────────────────────────────────────────────────────────────────────

    def physics(self, dt: float) -> None:
        if not self.armed and not self.airborne:
            self.vel = [0.0, 0.0, 0.0]
            self.roll = self.pitch = 0.0
            return
        if not self.armed:  # KILL trên không: rơi tự do (đơn giản)
            self.vel[2] = min(self.vel[2] + 9.81 * dt, 15)
            self.pos[2] = min(0.0, self.pos[2] + self.vel[2] * dt)
            return
        err = [self.sp[i] - self.pos[i] for i in range(3)]
        dist = math.sqrt(sum(e * e for e in err))
        speed = min(self.sp_vel, dist * 1.2)
        want = [e / dist * speed if dist > 1e-6 else 0.0 for e in err]
        prev = list(self.vel)
        k = min(1.0, dt / 0.35)  # bậc nhất τ = 0,35 s
        self.vel = [self.vel[i] + (want[i] - self.vel[i]) * k for i in range(3)]
        self.pos = [self.pos[i] + self.vel[i] * dt for i in range(3)]
        self.pos[2] = min(0.0, self.pos[2])
        if self.sp_yaw is not None:
            dy = (self.sp_yaw - self.yaw + 540) % 360 - 180
            self.yaw = (self.yaw + max(-90 * dt, min(90 * dt, dy))) % 360
        acc = [(self.vel[i] - prev[i]) / dt for i in range(2)]
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        fwd_v, right_v = c * self.vel[0] + s * self.vel[1], -s * self.vel[0] + c * self.vel[1]
        fwd_a, right_a = c * acc[0] + s * acc[1], -s * acc[0] + c * acc[1]
        tgt_pitch = max(-25, min(25, -(fwd_v * 3 + fwd_a * 4)))
        tgt_roll = max(-25, min(25, right_v * 3 + right_a * 4))
        self.pitch += (tgt_pitch - self.pitch) * min(1, dt * 8)
        self.roll += (tgt_roll - self.roll) * min(1, dt * 8)
        drain = 0.05 + 0.01 * math.sqrt(sum(v * v for v in self.vel)) + (0.02 if self.carrying else 0)
        self.battery = max(0.0, self.battery - drain * dt)

    def safety(self) -> None:
        now = time.monotonic()
        lost_for = now - self.last_gcs_hb
        if self.airborne and lost_for > self.params["link_lost_timeout_s"] and not self.link_lost_fired:
            self.link_lost_fired = True
            self.failsafe = m.FailsafeType.LINK_LOST
            self.event(3, "failsafe", int(m.FailsafeType.LINK_LOST), "mất liên kết GCS — tự kích RTH")
            if self.fsm != MS.RTH:
                self.start_activity(self.do_rth())
        if self.armed and self.battery < self.params["critical_battery_pct"] and not self.crit_batt_fired:
            self.crit_batt_fired = True
            self.failsafe = m.FailsafeType.LOW_BATTERY
            self.event(3, "failsafe", int(m.FailsafeType.LOW_BATTERY), "pin tới hạn — hạ cánh khẩn cấp")
            self.start_activity(self.do_land(MS.EMERGENCY_LAND, None))
        elif self.armed and self.battery < self.params["low_battery_pct"] and not self.low_batt_fired:
            self.low_batt_fired = True
            self.failsafe = m.FailsafeType.LOW_BATTERY
            self.event(2, "failsafe", int(m.FailsafeType.LOW_BATTERY), "pin yếu — quay về Home")
            if self.fsm not in (MS.RTH, MS.EMERGENCY_LAND):
                self.start_activity(self.do_rth())

    def telem_flags(self) -> int:
        f = m.TF_EKF_OK | m.TF_OFFBOARD
        f |= m.TF_ARMED if self.armed else 0
        f |= m.TF_TAG_LOCK if self.tag_lock else 0
        f |= m.TF_CARRYING if self.carrying else 0
        f |= m.TF_FAILSAFE if self.failsafe != m.FailsafeType.NONE and self.fsm in (MS.RTH, MS.EMERGENCY_LAND, MS.FAILSAFE) else 0
        return f

    async def run(self) -> None:
        """Lập lịch theo đồng hồ thật (không đếm tick): sleep trên Windows phân giải ~15 ms."""
        last = time.monotonic()
        due = {"fast": last, "detect": last, "state": last, "slow": last}
        period = {"fast": 0.1, "detect": 0.2, "state": 0.5, "slow": 1.0}
        while True:
            await asyncio.sleep(self.TICK)
            now = time.monotonic()
            self.physics(min(now - last, 0.1))
            last = now
            fire = {k for k, t in due.items() if now >= t}
            for k in fire:
                due[k] = max(due[k] + period[k], now)
            if "fast" in fire:  # 10 Hz
                await self.ep.send(m.TelemFast.from_si(
                    self.t_ms(), self.pos, self.vel, [self.roll, self.pitch, self.yaw],
                    int(self.fsm), self.telem_flags(), self.wp_index, self.battery))
            if "detect" in fire and self.fsm in (MS.MARKER_SEARCH, MS.PRECISION_LAND) \
                    and self.expected_tag in self.tags:  # 5 Hz khi đang bám
                await self.ep.send(self._tag_detect())
            if "state" in fire:  # 2 Hz
                self.send_mission_state()
                self.safety()
            if "slow" in fire:  # 1 Hz
                await self._slow()

    def _tag_detect(self) -> m.TagDetect:
        t = self.tags[self.expected_tag]
        dn, de, dd = t[1] / 1000 - self.pos[0], t[2] / 1000 - self.pos[1], t[3] / 1000 - self.pos[2]
        c, s = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        fx, fy = c * dn + s * de, -s * dn + c * de
        q = max(0, min(100, int(100 - abs(dd) * 6 - random.uniform(0, 10)))) if self.tag_lock else 30
        clamp = lambda v: max(-32768, min(32767, int(v * 1000)))
        return m.TagDetect(self.t_ms(), self.expected_tag, clamp(fx), clamp(fy), clamp(dd),
                           m.deg_to_cdeg_signed(t[4] / 100 - self.yaw), q, 1)

    async def _slow(self) -> None:
        ep, d = self.ep, self.ep.dongle
        rssi = -45 - random.randint(0, 8) - int(math.hypot(self.pos[0], self.pos[1]) * 0.2)
        loss = int(100 * d["tx_fail"] / d["tx_count"]) if d["tx_count"] else 0
        await ep.send(m.HeartbeatDrone(self.t_ms(), self.map_crc, int(self.fsm), self.telem_flags(), self.ep.session_id))
        await ep.send(m.TelemSlow(self.t_ms(), int(14800 * (0.85 + 0.15 * self.battery / 100)),
                                  int(1200 if self.armed else 80), int((100 - self.battery) * 50) & 0xFFFF,
                                  self.mission_id, int(time.monotonic() - self.t0) & 0xFFFF, int(self.battery),
                                  -rssi, loss, 95, 0, random.randint(20, 45), 52, int(self.gripper),
                                  int(self.failsafe), 0))
        await ep.send(m.LinkStat(d["rx_count"] & 0xFFFF, d["rx_count"] & 0xFFFF, 0, d["tx_ok"] & 0xFFFF,
                                 rssi, loss, 0))
        stat = m.DongleStat(d["tx_count"], d["tx_ok"], d["tx_fail"], d["rx_count"], rssi,
                            sum(len(q) for q in ep._txq), 0)
        # dongle thật có bộ đếm seq riêng — không được ăn vào seq của Pi 4 (làm sai PDR phía GCS)
        ep.enqueue_raw(0, fr.build(stat.MSG_ID, stat.pack(), next(self._dongle_seq) & 0xFFFF, priority=0, need_ack=False,
                                   src=fr.NODE_ESP_GCS, dst=fr.NODE_GCS), chan=fr.CHAN_DONGLE)


async def control_server(drone: FakeDrone, port: int) -> None:
    f = drone.faults

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        while line := (await r.readline()).decode(errors="replace").strip():
            p = line.split()
            try:
                cmd = p[0].lower()
                if cmd == "drop":
                    f.drop = float(p[1])
                elif cmd == "delay":
                    f.delay_ms, f.jitter_ms = float(p[1]), float(p[2]) if len(p) > 2 else 0.0
                elif cmd == "outage":
                    f.outage_until = time.monotonic() + float(p[1])
                elif cmd == "hang":
                    f.hang_until = time.monotonic() + float(p[1])
                elif cmd == "reject":
                    mid, res = int(p[1], 0), int(p[2])
                    f.reject.pop(mid, None) if res == 0 else f.reject.__setitem__(mid, res)
                elif cmd == "noeffect":
                    f.no_effect = p[1] == "on"
                elif cmd == "dongle":
                    f.dongle_on = p[1] == "on"
                elif cmd == "param":
                    drone.params[p[1]] = float(p[2])
                elif cmd == "battery":
                    drone.battery = float(p[1])
                elif cmd != "status":
                    raise ValueError("lệnh không rõ")
                w.write(f"ok fsm={drone.fsm.name} pos={[round(x, 2) for x in drone.pos]} batt={drone.battery:.1f} "
                        f"drop={f.drop} noeffect={f.no_effect} reject={f.reject} stats={drone.ep.stats}\n".encode())
            except Exception as e:
                w.write(f"lỗi: {e}\n".encode())
            await w.drain()
        w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


async def start_link_server(drone: FakeDrone, host: str, port: int) -> asyncio.Server:
    """Mở cổng TCP đóng vai dây serial GCS↔dongle; chạy kèm vòng TX và vòng mô phỏng."""
    ep = drone.ep

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if ep.transport is not None:
            log.warning("đã có GCS kết nối — từ chối kết nối thứ hai")
            w.close()
            return
        log.info("GCS kết nối từ %s", w.get_extra_info("peername"))
        t = _StreamTransport(r, w)
        ep.transport = t
        try:
            await ep.rx_loop(t)
        except (ConnectionError, OSError):
            pass
        finally:
            ep.transport = None
            ep.clear_tx()
            log.info("GCS ngắt kết nối")

    server = await asyncio.start_server(handle, host, port)
    drone.tasks = [asyncio.create_task(ep.tx_loop()), asyncio.create_task(drone.run())]
    return server


async def main(args) -> None:
    faults = Faults()
    faults.drop, faults.no_effect = args.drop, args.no_effect
    drone = FakeDrone(bytes.fromhex(args.session_key), faults)
    server = await start_link_server(drone, args.host, args.port)
    log.info("fake_drone lắng nghe tcp://%s:%d, cổng điều khiển lỗi %d", args.host, args.port, args.control_port)
    await asyncio.gather(server.serve_forever(), control_server(drone, args.control_port))


def cli() -> None:
    ap = argparse.ArgumentParser(description="Drone giả lập giao thức GCS ESP-NOW")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5760)
    ap.add_argument("--control-port", type=int, default=5761)
    ap.add_argument("--drop", type=float, default=0.0, help="tỉ lệ rớt gói không khí 0..1")
    ap.add_argument("--no-effect", action="store_true", help="ACK lệnh nhưng không thực thi (A11)")
    ap.add_argument("--session-key", default="00112233445566778899aabbccddeeff",
                    help="khóa phiên hex — phải trùng GCS_SESSION_KEY")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
