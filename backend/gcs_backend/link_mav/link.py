"""Vòng liên kết phía GCS: heartbeat, watchdog, TIMESYNC, thống kê, ghép STATUSTEXT.

Giao ước 5.1, 7.4, 7.5, 9.2. Tầng này chỉ lo SỨC KHOẺ ĐƯỜNG TRUYỀN; lệnh, nạp kế hoạch và
telemetry do tầng dịch vụ ở trên đọc qua handlers / bus.

GCS không có hàng đợi ưu tiên như Pi (giao ước 5.3): nó phát 1 gói/s lúc nghỉ, nên gửi thẳng ra
socket là đúng và queue_depth báo về luôn bằng 0 — một con số thật, không phải chỗ trống chưa làm.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from ..bus import EventBus
from .codec import GCS, UINT16_MAX, Codec
from .dialect import drone_gcs as mav
from .transport import UdpTransport

log = logging.getLogger(__name__)

LINK_TIMEOUT_S = 5.0      # giao ước 9.2 — GCS coi là mất liên kết; Pi leo thang RTH sau 10 s nữa
HEARTBEAT_HZ = 1.0        # giao ước 5.1 — không bao giờ tắt, còn để giữ ánh xạ NAT sống (2.1)
SLOW_PERIOD_S = 5.0       # 0,2 Hz: TIMESYNC và DRONE_LINK_STATS
STATUSTEXT_CHUNK = 50     # giao ước 7.5 — chunk đủ 50 byte nghĩa là còn đoạn sau
STATUSTEXT_GAP_S = 1.0    # đoạn sau không tới trong ngần này thì phát phần đã ghép, đừng nuốt luôn


@dataclass
class LinkState:
    up: bool = False
    last_rx: float | None = None
    rtt_ms: float | None = None
    peer_stats: dict = field(default_factory=dict)  # DRONE_LINK_STATS Pi báo về


class MavLink:
    def __init__(self, bus: EventBus, host: str = "0.0.0.0", port: int = 14550,
                 signing_key: bytes | None = None, timestamp: int = 0) -> None:
        self.bus = bus
        self.host, self.port = host, port
        self.codec = Codec(signing_key, timestamp)
        self.transport = UdpTransport(self._on_datagram)
        self.state = LinkState()
        self.handlers: list[Callable[[object], None]] = []  # tầng dịch vụ G2 cắm vào đây
        self._ts1_sent: int | None = None
        self._chunks: dict[int, dict] = {}

    # --- vòng đời ---

    async def run(self) -> None:
        await self.transport.open(self.host, self.port)
        tasks = [asyncio.create_task(self._heartbeat_loop()), asyncio.create_task(self._slow_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self.transport.close()

    def send(self, msg) -> bool:
        """False khi chưa biết đường về (Pi chưa gọi ra) — đếm vào tx_dropped, không ném lỗi."""
        if self.transport.send(self.codec.encode(msg)):
            self.codec.counters.tx_sent += 1
            return True
        self.codec.counters.tx_dropped += 1
        return False

    def status(self) -> dict:
        c = self.codec.counters
        age = None if self.state.last_rx is None else round(time.monotonic() - self.state.last_rx, 2)
        return {
            "up": self.state.up,
            "peer": None if self.transport.peer is None else "%s:%d" % self.transport.peer,
            "rx_age_s": age,
            "rtt_ms": self.state.rtt_ms,
            "signing": self.codec.signing_on,
            "gcs": {"rx_ok": c.rx_ok, "rx_drop": c.rx_drop, "rx_bad_crc": c.rx_bad_crc,
                    "rx_bad_sig": c.rx_bad_sig, "tx_sent": c.tx_sent, "tx_dropped": c.tx_dropped},
            "drone": dict(self.state.peer_stats),
        }

    # --- chiều nhận ---

    def _on_datagram(self, data: bytes, addr: tuple) -> None:
        for msg in self.codec.decode(data):
            # Tới được đây nghĩa là gói đã đúng sysid/compid, đúng CRC và đúng chữ ký (nếu bật):
            # đây chính là "gói hợp lệ" của giao ước 2.1 quyết định đường về.
            self.transport.note_valid(addr)
            self.state.last_rx = time.monotonic()
            if not self.state.up:
                self._set_up(True)
            self._dispatch(msg)

    def _dispatch(self, msg) -> None:
        name = msg.get_type()
        if name == "TIMESYNC":
            self._on_timesync(msg)
        elif name == "DRONE_LINK_STATS":
            self.state.peer_stats = msg.to_dict()
        elif name == "STATUSTEXT":
            self._on_statustext(msg)
        self.bus.emit("mav.rx", msg=msg)
        for h in self.handlers:
            h(msg)

    def _on_timesync(self, msg) -> None:
        if msg.tc1 == 0:
            # Pi hỏi giờ: trả lời NGAY, cùng ts1 nó gửi — trễ ở đây vào thẳng số rtt của Pi.
            self.send(self.codec.mav.timesync_encode(time.time_ns(), msg.ts1))
        elif self._ts1_sent is not None and msg.ts1 == self._ts1_sent:
            self.state.rtt_ms = round((time.time_ns() - msg.ts1) / 1e6, 2)
            self._ts1_sent = None

    def _on_statustext(self, msg) -> None:
        """Ghép chuỗi bị chia đoạn (giao ước 7.5): id = số hiệu chuỗi, chunk_seq = thứ tự đoạn."""
        sid = getattr(msg, "id", 0)
        if sid == 0:  # quy ước MAVLink: không chia đoạn
            self.bus.emit("mav.statustext", severity=msg.severity, text=msg.text)
            return
        buf = self._chunks.setdefault(sid, {"severity": msg.severity, "parts": {}, "t": 0.0})
        buf["parts"][msg.chunk_seq] = msg.text
        buf["t"] = time.monotonic()
        if len(msg.text) < STATUSTEXT_CHUNK:  # đoạn cuối: ngắn hơn 50 byte nên có NUL kết chuỗi
            self._flush_statustext(sid)

    def _flush_statustext(self, sid: int) -> None:
        buf = self._chunks.pop(sid, None)
        if buf is None:
            return
        text = "".join(buf["parts"][k] for k in sorted(buf["parts"]))
        self.bus.emit("mav.statustext", severity=buf["severity"], text=text)

    # --- chiều phát ---

    async def _heartbeat_loop(self) -> None:
        """1 Hz, kể cả khi chưa biết đường về — nhưng khi chưa biết thì send() chỉ đếm tx_dropped."""
        while True:
            self.send(self.codec.mav.heartbeat_encode(
                mav.MAV_TYPE_GCS, mav.MAV_AUTOPILOT_INVALID, 0, 0, mav.MAV_STATE_ACTIVE))
            now = time.monotonic()
            if self.state.up and (self.state.last_rx is None or now - self.state.last_rx > LINK_TIMEOUT_S):
                self._set_up(False)
            for sid, buf in list(self._chunks.items()):
                if now - buf["t"] > STATUSTEXT_GAP_S:
                    self._flush_statustext(sid)
            self.bus.emit("link.status", status=self.status())
            await asyncio.sleep(1.0 / HEARTBEAT_HZ)

    async def _slow_loop(self) -> None:
        while True:
            await asyncio.sleep(SLOW_PERIOD_S)
            self._ts1_sent = time.time_ns()
            self.send(self.codec.mav.timesync_encode(0, self._ts1_sent))
            c = self.codec.counters
            rtt = UINT16_MAX if self.state.rtt_ms is None else min(UINT16_MAX, round(self.state.rtt_ms))
            self.send(self.codec.mav.drone_link_stats_encode(
                c.rx_ok, c.rx_drop, c.rx_bad_crc, c.rx_bad_sig, c.tx_sent, c.tx_dropped, 0, rtt))

    def _set_up(self, up: bool) -> None:
        self.state.up = up
        log.info("liên kết %s", "lên" if up else "mất (quá %.0f s không nhận gì)" % LINK_TIMEOUT_S)
        self.bus.emit("link.state_changed", up=up)
