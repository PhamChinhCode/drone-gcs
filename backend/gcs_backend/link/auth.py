"""Xác thực tầng ứng dụng + chống phát lại (mục 4.3, 5.5).

auth_tag = 4 byte đầu HMAC-SHA256(session_key, header || payload).
CCMP của ESP-NOW chống nghe lén/giả mạo nhưng KHÔNG chống phát lại → cửa sổ seq 64 gói.

GHI CHÚ: bản tin bắt tay sinh session_key mỗi phiên CHƯA có msg_id trong đặc tả (mục 5.3).
Hiện dùng khóa phiên cấu hình tĩnh (GCS_SESSION_KEY) — xem docs/DIEM_CAN_CHOT.md.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from collections import deque

from .crc import crc32


def compute_auth_tag(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()[:4]


def emergency_confirm_code(action: int, cmd_seq: int, session_key: bytes) -> int:
    """confirm_code = CRC32(action || cmd_seq || session_key) — mục 5.4, bắt buộc với KILL."""
    return crc32(struct.pack("<BH", action, cmd_seq) + session_key)


class ReplayWindow:
    """Cửa sổ seq kiểu IPsec: 16 bit, cho phép đảo thứ tự nhẹ trong 64 gói.

    accept() trả True nếu seq chưa từng thấy và nằm trong cửa sổ.
    """

    SIZE = 64

    def __init__(self) -> None:
        self._top: int | None = None
        self._bitmap = 0

    def observe(self, seq: int) -> None:
        """Dời đỉnh cửa sổ theo MỌI khung hợp lệ CRC của nguồn này (không đánh dấu đã dùng).

        Cần thiết vì seq dùng chung cho telemetry: nếu chỉ lệnh mới dời cửa sổ thì sau ~30 phút
        không có lệnh, seq quay vòng 16 bit và lệnh hợp lệ bị coi là cũ.
        """
        if self._top is None:
            self._top, self._bitmap = seq, 0
            return
        diff = (seq - self._top) & 0xFFFF
        if 0 < diff < 0x8000:
            self._bitmap = (self._bitmap << diff) & ((1 << self.SIZE) - 1)
            self._top = seq

    def check(self, seq: int) -> bool:
        if self._top is None:
            return True
        diff = (seq - self._top) & 0xFFFF
        if diff == 0:
            return not self._bitmap & 1
        if diff < 0x8000:  # seq mới hơn
            return True
        back = 0x10000 - diff
        if back >= self.SIZE:
            return False
        return not (self._bitmap >> back) & 1

    def accept(self, seq: int) -> bool:
        if not self.check(seq):
            return False
        if self._top is None:
            self._top, self._bitmap = seq, 1
            return True
        diff = (seq - self._top) & 0xFFFF
        if diff == 0:
            self._bitmap |= 1
        elif diff < 0x8000:
            self._bitmap = ((self._bitmap << diff) | 1) & ((1 << self.SIZE) - 1)
            self._top = seq
        else:
            self._bitmap |= 1 << (0x10000 - diff)
        return True


class SeenSeq:
    """Bộ nhớ 64 seq gần nhất cho một nguồn — dùng chống thực thi trùng (mục 5.5)."""

    def __init__(self, size: int = 64) -> None:
        self._dq: deque[int] = deque(maxlen=size)
        self._set: set[int] = set()

    def seen(self, seq: int) -> bool:
        return seq in self._set

    def add(self, seq: int) -> None:
        if seq in self._set:
            return
        if len(self._dq) == self._dq.maxlen:
            self._set.discard(self._dq[0])
        self._dq.append(seq)
        self._set.add(seq)
