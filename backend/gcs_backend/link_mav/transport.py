"""Tầng vận chuyển UDP của kênh GCS <-> Pi (giao ước 2.1).

GCS bind :14550 và KHÔNG bao giờ chủ động gọi Pi: modem 4G nằm sau CGNAT của nhà mạng nên chỉ có
chiều Pi gọi ra là đi được. Đường về là địa chỉ nguồn của gói HỢP LỆ gần nhất — không phải gói đầu
tiên (modem nối lại thì mất đường lên mãi mãi), cũng không phải gói mới nhất bất kỳ (một gói giả mạo
tuy không ra lệnh được nhờ chữ ký nhưng vẫn cắt được lệnh khẩn của người vận hành). Phép kiểm C5.

Lớp này không biết thế nào là "hợp lệ" — tầng trên giải mã và kiểm chữ ký xong thì gọi note_valid().

Nhận bằng socket thô + loop.sock_recvfrom(), KHÔNG dùng loop.create_datagram_endpoint(): trên
Windows, DatagramProtocol chạy trên ProactorEventLoop (uvicorn TỰ ÉP dùng loại này, đặt
event-loop-policy trong app không đổi được) NGỪNG nhận datagram VĨNH VIỄN sau ĐÚNG MỘT lần ICMP
Port Unreachable — tức đúng lúc Pi tắt (GCS vẫn phát heartbeat mỗi giây vào cổng đã đóng). Bug
CPython đã biết (gh-91227/bpo-39148), tái hiện được bằng script độc lập. Hậu quả: Pi khởi động lại
không bao giờ nối lại được, chỉ khởi động lại GCS mới hết vì đó là cách duy nhất tạo socket UDP
mới. loop.sock_recvfrom() không dính lỗi này — lỗi rơi thẳng vào await, bắt rồi gọi lại là xong.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Callable

log = logging.getLogger(__name__)

MTU = 1400      # giao ước 2.1 — trên mức này datagram bị phân mảnh IP trên đường 4G
RECV_BUF = 2048  # rộng rãi hơn MTU, không phải giới hạn giao thức


class UdpTransport:
    def __init__(self, on_datagram: Callable[[bytes, tuple], None]) -> None:
        self._on_datagram = on_datagram
        self.peer: tuple | None = None
        self._sock: socket.socket | None = None
        self._recv_task: asyncio.Task | None = None

    async def open(self, host: str, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._sock.bind((host, port))
        log.info("UDP nghe tại %s", self._sock.getsockname())
        self._recv_task = asyncio.create_task(self._recv_loop())

    def close(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def note_valid(self, addr: tuple) -> None:
        """Gói hợp lệ từ (1, 191) vừa tới từ addr — đường về đổi sang đó."""
        if addr != self.peer:
            log.info("đường về: %s -> %s", self.peer, addr)
            self.peer = addr

    def send(self, data: bytes) -> bool:
        """False khi chưa từng nhận gói hợp lệ nào: chưa biết gửi đi đâu, không phải lỗi."""
        if self._sock is None or self.peer is None:
            return False
        try:
            self._sock.sendto(data, self.peer)
        except OSError as exc:
            # ICMP port unreachable: Pi chưa lên hoặc vừa tắt. Không phải lỗi của ta, UDP không có kết nối.
            log.debug("UDP lỗi khi gửi: %s", exc)
        return True

    async def _recv_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                data, addr = await loop.sock_recvfrom(self._sock, RECV_BUF)
            except OSError as exc:
                log.debug("UDP lỗi khi nhận: %s", exc)
                continue
            self._on_datagram(data, addr)
