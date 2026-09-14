"""CRC16-CCITT-FALSE (đa thức 0x1021, init 0xFFFF) và CRC32 (mục 5.2, 3.3)."""
from __future__ import annotations

import zlib

_TABLE16 = []
for _i in range(256):
    _c = _i << 8
    for _ in range(8):
        _c = ((_c << 1) ^ 0x1021) if (_c & 0x8000) else (_c << 1)
    _TABLE16.append(_c & 0xFFFF)


def crc16_ccitt_false(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _TABLE16[((crc >> 8) ^ b) & 0xFF]
    return crc


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF
