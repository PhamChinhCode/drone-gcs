"""Phiên liên kết: seq, bảng chờ ACK, retry, hàng đợi ưu tiên, chống trùng, watchdog (mục 5.5, 5.8, 7.3).

LinkEndpoint  — logic giao thức đối xứng, dùng cho cả GCS lẫn sim/fake_drone.py.
LinkSession   — phía GCS: nối lại transport, heartbeat 1 Hz, watchdog, thống kê liên kết.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

from ..bus import EventBus
from . import cobs
from . import frame as fr
from . import messages as m
from .auth import ReplayWindow, SeenSeq
from .serial_io import Transport, encode_uart, open_transport

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    timeout: float
    count: int


RETRY = {
    3: RetryPolicy(0.060, 8),   # khẩn cấp — hết lượt thì phát lại mỗi 500 ms tới khi ACK/hủy
    2: RetryPolicy(0.150, 4),   # lệnh
    1: RetryPolicy(0.400, 3),   # thường
    0: RetryPolicy(0.400, 3),
}
EMERGENCY_REPEAT_S = 0.5


class LinkTimeout(Exception):
    def __init__(self, msg_id: int, seq: int, attempts: int):
        super().__init__(f"hết lượt chờ ACK msg 0x{msg_id:02X} seq {seq} sau {attempts} lần")
        self.msg_id, self.seq, self.attempts = msg_id, seq, attempts


class SendCancelled(Exception):
    pass


@dataclass(slots=True)
class AckInfo:
    result: int
    detail: int
    seq: int
    attempts: int
    rtt_ms: float

    @property
    def ok(self) -> bool:
        return self.result in (m.AckResult.OK, m.AckResult.DEFERRED)

    def as_dict(self) -> dict:
        return {"result": m.AckResult(self.result).name, "detail": self.detail, "seq": self.seq,
                "attempts": self.attempts, "rtt_ms": round(self.rtt_ms, 1)}


@dataclass(slots=True)
class _Pending:
    fut: asyncio.Future
    msg_id: int
    first_tx: float
    attempts: int = 0


# handler(msg, header) → mã AckResult cho bản tin NEED_ACK (bỏ qua với bản tin không cần ACK)
RxHandler = Callable[[m.Message, fr.Header], "int | None"]


class LinkEndpoint:
    def __init__(self, *, src: int, dst: int, auth_key: bytes | None) -> None:
        self.src, self.dst, self.auth_key = src, dst, auth_key
        self._seq = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._txq: list[deque[tuple[int, bytes]]] = [deque() for _ in range(4)]
        self._tx_event = asyncio.Event()
        self._seen: dict[int, SeenSeq] = defaultdict(SeenSeq)
        self._replay: dict[int, ReplayWindow] = defaultdict(ReplayWindow)
        self._splitter = cobs.FrameSplitter()
        self.transport: Transport | None = None
        self.handlers: list[RxHandler] = []
        self.stats = {"tx_units": 0, "rx_units": 0, "crc_errors": 0, "auth_rejects": 0, "dup_rx": 0,
                      "retries": 0, "timeouts": 0}
        self.rtt_ms: deque[float] = deque(maxlen=50)
        self.on_retry: Callable[[int, int, int], None] | None = None
        self.on_emergency_unacked: Callable[[int, int], None] | None = None

    # ── gửi ──────────────────────────────────────────────────────────────────────────────────

    def next_seq(self) -> int:
        while True:
            s = next(self._seq) & 0xFFFF
            if s:
                return s

    def build(self, msg: m.Message, seq: int, *, need_ack: bool | None = None) -> bytes:
        prio = msg.PRIORITY
        return fr.build(msg.MSG_ID, msg.pack(), seq, priority=prio,
                        need_ack=msg.NEED_ACK if need_ack is None else need_ack,
                        src=self.src, dst=self.dst, auth_key=self.auth_key if prio >= 2 else None)

    def enqueue_raw(self, priority: int, raw: bytes, chan: int = fr.CHAN_AIR) -> None:
        self._txq[priority].append((chan, raw))
        self._tx_event.set()

    async def send(self, msg: m.Message, *, cancel: asyncio.Event | None = None,
                   raw_override: bytes | None = None) -> AckInfo | None:
        """Gửi bản tin; với NEED_ACK thì chờ ACK theo bảng RETRY. Thử lại gửi NGUYÊN VĂN khung cũ, giữ seq."""
        seq = self.next_seq()
        raw = raw_override or self.build(msg, seq)
        prio = msg.PRIORITY
        if not msg.NEED_ACK:
            self.enqueue_raw(prio, raw)
            return None
        if raw_override is not None:
            seq = fr.parse(raw_override, self.auth_key).header.seq
        loop = asyncio.get_running_loop()
        pend = _Pending(loop.create_future(), msg.MSG_ID, time.monotonic())
        self._pending[seq] = pend
        policy = RETRY[prio]
        try:
            for attempt in range(policy.count):
                pend.attempts = attempt + 1
                self.enqueue_raw(prio, raw)
                try:
                    return await asyncio.wait_for(asyncio.shield(pend.fut), policy.timeout)
                except asyncio.TimeoutError:
                    if attempt + 1 < policy.count:
                        self.stats["retries"] += 1
                        if self.on_retry:
                            self.on_retry(msg.MSG_ID, seq, attempt + 1)
            if prio == 3:
                if self.on_emergency_unacked:
                    self.on_emergency_unacked(msg.MSG_ID, seq)
                while True:
                    if cancel is not None and cancel.is_set():
                        raise SendCancelled()
                    pend.attempts += 1
                    self.enqueue_raw(prio, raw)
                    try:
                        return await asyncio.wait_for(asyncio.shield(pend.fut), EMERGENCY_REPEAT_S)
                    except asyncio.TimeoutError:
                        pass
            self.stats["timeouts"] += 1
            raise LinkTimeout(msg.MSG_ID, seq, policy.count)
        finally:
            self._pending.pop(seq, None)

    def _send_ack(self, hdr: fr.Header, result: int, detail: int = 0) -> None:
        ack = m.Ack(hdr.msg_id, hdr.seq, result, detail)
        raw = fr.build(m.Ack.MSG_ID, ack.pack(), self.next_seq(), priority=hdr.priority, need_ack=False,
                       src=self.src, dst=hdr.src, is_ack=True)
        self.enqueue_raw(max(hdr.priority, 2), raw)

    async def tx_loop(self) -> None:
        """Rút hàng đợi theo ưu tiên tuyệt đối: 3 trước 2 trước 1 trước 0."""
        while True:
            item = None
            for q in reversed(self._txq):
                if q:
                    item = q.popleft()
                    break
            if item is None:
                self._tx_event.clear()
                await self._tx_event.wait()
                continue
            chan, raw = item
            await self.write_unit(chan, raw)

    async def write_unit(self, chan: int, raw: bytes) -> None:
        if self.transport is None:
            return  # chưa có transport → gói rơi; lệnh sẽ timeout và báo lỗi
        try:
            await self.transport.write(encode_uart(chan, raw))
            self.stats["tx_units"] += 1
        except Exception as e:
            log.warning("ghi transport lỗi: %s", e)

    def clear_tx(self) -> None:
        for q in self._txq:
            q.clear()

    # ── nhận ─────────────────────────────────────────────────────────────────────────────────

    async def rx_loop(self, transport: Transport) -> None:
        while True:
            chunk = await transport.read()
            if not chunk:
                return
            for f in self._splitter.feed(chunk):
                try:
                    body = cobs.decode(f)
                except cobs.CobsError:
                    self.stats["crc_errors"] += 1
                    continue
                if body:
                    self.on_frame(body[0], body[1:])

    def on_frame(self, chan: int, raw: bytes) -> None:
        if chan == fr.CHAN_AIR:
            self.on_unit(raw)
        elif chan == fr.CHAN_DONGLE:
            self.on_dongle_unit(raw)
        elif chan == fr.CHAN_DEBUG:
            log.debug("esp32: %s", raw.decode("utf-8", errors="replace"))

    def on_dongle_unit(self, raw: bytes) -> None:
        pass

    def on_unit(self, raw: bytes) -> None:
        try:
            unit = fr.parse(raw, self.auth_key)
        except fr.FrameError:
            self.stats["crc_errors"] += 1
            return
        hdr = unit.header
        self.stats["rx_units"] += 1
        if not hdr.has_auth:
            self._replay[hdr.src].observe(hdr.seq)
        self.on_rx_header(hdr)

        if hdr.is_ack:
            try:
                ack = m.Ack.unpack(unit.payload)
            except ValueError:
                return
            pend = self._pending.get(ack.ack_seq)
            if pend and pend.msg_id == ack.ack_msg_id and not pend.fut.done():
                rtt = (time.monotonic() - pend.first_tx) * 1000
                self.rtt_ms.append(rtt)
                pend.fut.set_result(AckInfo(ack.result, ack.detail, ack.ack_seq, pend.attempts, rtt))
            return

        if hdr.priority >= 2 and hdr.msg_id != m.Ack.MSG_ID:
            if not unit.auth_ok:
                self.stats["auth_rejects"] += 1
                if hdr.need_ack:
                    self._send_ack(hdr, m.AckResult.REJECT_AUTH)
                return
            seen = self._seen[hdr.src]
            if seen.seen(hdr.seq):  # trùng: ACK lại, KHÔNG thực thi lần hai
                self.stats["dup_rx"] += 1
                if hdr.need_ack:
                    self._send_ack(hdr, m.AckResult.OK)
                return
            if not self._replay[hdr.src].accept(hdr.seq):
                self.stats["auth_rejects"] += 1
                if hdr.need_ack:
                    self._send_ack(hdr, m.AckResult.REJECT_AUTH, 1)
                return
            seen.add(hdr.seq)
        elif hdr.need_ack:
            seen = self._seen[hdr.src]
            if seen.seen(hdr.seq):
                self.stats["dup_rx"] += 1
                self._send_ack(hdr, m.AckResult.OK)
                return
            seen.add(hdr.seq)

        try:
            msg = m.decode(hdr.msg_id, unit.payload)
        except ValueError:
            if hdr.need_ack:
                self._send_ack(hdr, m.AckResult.REJECT_UNSUPPORTED)
            return
        result: int | None = None
        for h in self.handlers:
            r = h(msg, hdr)
            if r is not None and result is None:
                result = r
        if hdr.need_ack:
            self._send_ack(hdr, m.AckResult.OK if result is None else result)

    def on_rx_header(self, hdr: fr.Header) -> None:
        pass


# ───────────────────────────────── phía GCS ───────────────────────────────────────────────────

DRONE_LOST_S = 3.0
DRONE_LOST_LONG_S = 10.0
DONGLE_LOST_S = 2.0


@dataclass
class _Window:
    expected: int = 0
    received: int = 0


@dataclass
class LinkState:
    transport_up: bool = False
    last_dongle_rx: float | None = None
    last_drone_hb: float | None = None
    last_drone_rx: float | None = None
    dongle: dict = field(default_factory=dict)
    dongle_tx_ok_prev: int | None = None
    dongle_tx_ok_rising: bool = False
    air: dict = field(default_factory=dict)
    drone_map_crc: int | None = None
    pdr_pct: float | None = None


class LinkSession(LinkEndpoint):
    def __init__(self, url: str, bus: EventBus, auth_key: bytes, session_id: int = 1) -> None:
        super().__init__(src=fr.NODE_GCS, dst=fr.NODE_PI4, auth_key=auth_key)
        self.url, self.bus, self.session_id = url, bus, session_id
        self.state = LinkState()
        self.map_crc_provider: Callable[[], int] = lambda: 0
        self._last_drone_seq: int | None = None
        self._win = _Window()
        self._t0 = time.monotonic()
        self._prev_link_state = ""
        self.on_retry = lambda mid, seq, n: bus.emit("link.retry", msg_id=mid, seq=seq, attempt=n)
        self.on_emergency_unacked = lambda mid, seq: bus.emit("link.emergency_unacked", msg_id=mid, seq=seq)
        self.handlers.append(self._dispatch)

    async def run(self) -> None:
        tasks = [asyncio.create_task(self.tx_loop()), asyncio.create_task(self._periodic())]
        try:
            while True:
                t = open_transport(self.url)
                try:
                    await t.open()
                except Exception as e:
                    log.debug("mở liên kết %s thất bại: %s", self.url, e)
                    await asyncio.sleep(1.0)
                    continue
                log.info("liên kết mở: %s", self.url)
                self.transport, self.state.transport_up = t, True
                self._splitter = cobs.FrameSplitter()
                try:
                    await self.rx_loop(t)
                except Exception as e:
                    log.warning("liên kết đọc lỗi: %s", e)
                finally:
                    self.transport, self.state.transport_up = None, False
                    self.clear_tx()
                    await t.close()
                    log.info("liên kết đóng: %s", self.url)
                await asyncio.sleep(1.0)
        finally:
            for task in tasks:
                task.cancel()

    def _dispatch(self, msg: m.Message, hdr: fr.Header) -> int | None:
        now = time.monotonic()
        self.state.last_drone_rx = now
        if isinstance(msg, m.HeartbeatDrone):
            self.state.last_drone_hb = now
            if self.state.drone_map_crc != msg.map_crc:
                self.state.drone_map_crc = msg.map_crc
                self.bus.emit("link.drone_map_crc", map_crc=msg.map_crc)
        elif isinstance(msg, m.LinkStat):
            self.state.air = msg.to_si()
        self.bus.emit("rx", msg=msg, header=hdr, t=now)
        return None

    def on_rx_header(self, hdr: fr.Header) -> None:
        if hdr.src != fr.NODE_PI4:
            return
        self.state.last_dongle_rx = time.monotonic()  # dữ liệu không khí tới được nghĩa là dongle sống
        if self._last_drone_seq is not None:
            diff = (hdr.seq - self._last_drone_seq) & 0xFFFF
            if 0 < diff < 1000:
                self._win.expected += diff
                self._win.received += 1
        self._last_drone_seq = hdr.seq

    def on_dongle_unit(self, raw: bytes) -> None:
        try:
            unit = fr.parse(raw)
        except fr.FrameError:
            self.stats["crc_errors"] += 1
            return
        self.state.last_dongle_rx = time.monotonic()
        if unit.header.msg_id == m.DongleStat.MSG_ID:
            stat = m.DongleStat.unpack(unit.payload)
            prev = self.state.dongle_tx_ok_prev
            self.state.dongle_tx_ok_rising = prev is not None and stat.tx_ok > prev
            self.state.dongle_tx_ok_prev = stat.tx_ok
            self.state.dongle = stat.to_si()

    async def send_dongle(self, msg: m.Message) -> None:
        raw = fr.build(msg.MSG_ID, msg.pack(), self.next_seq(), priority=1, need_ack=False,
                       src=fr.NODE_GCS, dst=fr.NODE_ESP_GCS)
        self.enqueue_raw(1, raw, chan=fr.CHAN_DONGLE)

    def link_state(self) -> str:
        """dongle_lost | drone_lost_long | drone_silent | drone_lost | ok — ba nguyên nhân gốc khác nhau (5.8)."""
        now = time.monotonic()
        s = self.state
        if not s.transport_up or s.last_dongle_rx is None or now - s.last_dongle_rx > DONGLE_LOST_S:
            return "dongle_lost"
        hb_age = None if s.last_drone_hb is None else now - s.last_drone_hb
        if hb_age is None or hb_age > DRONE_LOST_S:
            if s.dongle_tx_ok_rising:  # MAC vẫn ACK nhưng ứng dụng Pi 4 không trả heartbeat → treo phần mềm
                return "drone_silent"
            if hb_age is None or hb_age > DRONE_LOST_LONG_S:
                return "drone_lost_long"
            return "drone_lost"
        return "ok"

    def status(self) -> dict:
        now = time.monotonic()
        s = self.state
        hb_age = None if s.last_drone_hb is None else round(now - s.last_drone_hb, 2)
        return {
            "state": self.link_state(),
            "url": self.url,
            "transport_up": s.transport_up,
            "drone_hb_age_s": hb_age,
            "dongle_age_s": None if s.last_dongle_rx is None else round(now - s.last_dongle_rx, 2),
            "dongle": s.dongle,
            "air": s.air,
            "pdr_pct": s.pdr_pct,
            "rtt_ms_avg": round(sum(self.rtt_ms) / len(self.rtt_ms), 1) if self.rtt_ms else None,
            "rtt_ms_max": round(max(self.rtt_ms), 1) if self.rtt_ms else None,
            "stats": dict(self.stats),
            "drone_map_crc": s.drone_map_crc,
        }

    async def _periodic(self) -> None:
        """1 Hz: HEARTBEAT_GCS, tính PDR, phát trạng thái liên kết."""
        while True:
            await asyncio.sleep(1.0)
            if self._win.expected:
                self.state.pdr_pct = round(100.0 * self._win.received / self._win.expected, 1)
            self._win = _Window()
            if self.transport is not None:
                t_ms = int((time.monotonic() - self._t0) * 1000) & 0xFFFFFFFF
                await self.send(m.HeartbeatGcs(t_ms, self.map_crc_provider(), self.session_id))
            st = self.status()
            if st["state"] != self._prev_link_state:
                self.bus.emit("link.state_changed", old=self._prev_link_state, new=st["state"])
                self._prev_link_state = st["state"]
            self.bus.emit("link.status", status=st)
