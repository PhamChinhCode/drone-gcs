"""Vận chuyển byte không chặn tới dongle ESP32-GCS.

URL:
  serial://COM5?baud=921600   — dongle ESP32 thật (USB CDC, 921600 8N1)
  tcp://127.0.0.1:5760        — sim/fake_drone.py (thay cho cặp pty socat, vì Windows không có socat)

Cả hai mang CÙNG luồng khung COBS; tầng trên không biết đang nói với phần cứng hay giả lập.
Serial dùng luồng đọc riêng thay vì pyserial-asyncio vì vòng Proactor trên Windows không hỗ trợ add_reader.
"""
from __future__ import annotations

import asyncio
import threading
from abc import ABC, abstractmethod
from urllib.parse import parse_qs, urlparse

from . import cobs


def encode_uart(chan_id: int, unit: bytes) -> bytes:
    """Khung UART = COBS(chan_id || đơn vị lớp 3) || 0x00."""
    return cobs.encode(bytes([chan_id]) + unit) + b"\x00"


class Transport(ABC):
    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def read(self) -> bytes:
        """Trả về một đoạn byte; b"" = kết nối đã đóng."""

    @abstractmethod
    async def write(self, data: bytes) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class TcpTransport(Transport):
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self._r: asyncio.StreamReader | None = None
        self._w: asyncio.StreamWriter | None = None

    async def open(self) -> None:
        self._r, self._w = await asyncio.open_connection(self.host, self.port)

    async def read(self) -> bytes:
        assert self._r
        return await self._r.read(4096)

    async def write(self, data: bytes) -> None:
        assert self._w
        self._w.write(data)
        await self._w.drain()

    async def close(self) -> None:
        if self._w:
            self._w.close()
            try:
                await self._w.wait_closed()
            except Exception:
                pass


class SerialTransport(Transport):
    def __init__(self, port: str, baud: int = 921600) -> None:
        self.port, self.baud = port, baud
        self._ser = None
        self._q: asyncio.Queue[bytes] = asyncio.Queue()
        self._thread: threading.Thread | None = None
        self._wlock = asyncio.Lock()

    async def open(self) -> None:
        import serial  # pyserial

        loop = asyncio.get_running_loop()
        self._ser = await asyncio.to_thread(serial.Serial, self.port, self.baud, timeout=0.05)

        def reader() -> None:
            ser = self._ser
            try:
                while ser is not None and ser.is_open:
                    chunk = ser.read(ser.in_waiting or 1)
                    if chunk:
                        loop.call_soon_threadsafe(self._q.put_nowait, chunk)
            except Exception:
                pass
            loop.call_soon_threadsafe(self._q.put_nowait, b"")

        self._thread = threading.Thread(target=reader, name=f"serial-{self.port}", daemon=True)
        self._thread.start()

    async def read(self) -> bytes:
        return await self._q.get()

    async def write(self, data: bytes) -> None:
        async with self._wlock:
            await asyncio.to_thread(self._ser.write, data)

    async def close(self) -> None:
        if self._ser is not None:
            ser, self._ser = self._ser, None
            await asyncio.to_thread(ser.close)


def open_transport(url: str) -> Transport:
    u = urlparse(url)
    if u.scheme == "tcp":
        return TcpTransport(u.hostname or "127.0.0.1", u.port or 5760)
    if u.scheme == "serial":
        q = parse_qs(u.query)
        port = u.netloc or u.path.lstrip("/")
        return SerialTransport(port, int(q.get("baud", ["921600"])[0]))
    raise ValueError(f"URL liên kết không hỗ trợ: {url}")
