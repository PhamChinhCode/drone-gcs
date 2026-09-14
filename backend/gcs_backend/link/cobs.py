"""COBS — Consistent Overhead Byte Stuffing (mục 5.1).

Khung trên dây UART: COBS(chan_id || đơn vị lớp 3) || 0x00.
Byte 0x00 không bao giờ xuất hiện trong thân khung đã mã hóa.
"""
from __future__ import annotations


class CobsError(ValueError):
    pass


def encode(data: bytes) -> bytes:
    """Mã hóa COBS, KHÔNG kèm byte 0x00 kết khung."""
    out = bytearray()
    code_idx = 0
    out.append(0)  # chỗ cho mã đầu tiên
    code = 1
    for b in data:
        if b == 0:
            out[code_idx] = code
            code_idx = len(out)
            out.append(0)
            code = 1
        else:
            out.append(b)
            code += 1
            if code == 0xFF:
                out[code_idx] = code
                code_idx = len(out)
                out.append(0)
                code = 1
    out[code_idx] = code
    return bytes(out)


def decode(data: bytes) -> bytes:
    """Giải mã COBS (đầu vào không chứa byte 0x00 kết khung)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        code = data[i]
        if code == 0:
            raise CobsError("byte 0 trong thân khung")
        i += 1
        end = i + code - 1
        if end > n:
            raise CobsError("khung cụt")
        chunk = data[i:end]
        if 0 in chunk:
            raise CobsError("byte 0 trong thân khung")
        out += chunk
        i = end
        if code != 0xFF and i < n:
            out.append(0)
    return bytes(out)


class FrameSplitter:
    """Tách luồng byte thành các khung theo dấu 0x00. Đồng bộ lại sau nhiễu chỉ mất một khung."""

    MAX_FRAME = 512

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for b in chunk:
            if b == 0:
                if self._buf:
                    frames.append(bytes(self._buf))
                    self._buf.clear()
            else:
                if len(self._buf) < self.MAX_FRAME:
                    self._buf.append(b)
                else:  # khung quá dài → bỏ, chờ dấu kết khung kế tiếp
                    self._buf.clear()
        return frames
