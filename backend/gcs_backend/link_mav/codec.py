"""Mã hoá / giải mã MAVLink 2 cho kênh GCS <-> Pi (giao ước 2.2, 7.4, 7.6).

Một Codec giữ một đối tượng MAVLink của pymavlink: seq phát, khoá chữ ký, và các bộ đếm của mục 7.4.
Sinh mã từ drone_gcs.xml chứ không tự viết bộ mã hoá — hai bên tự viết tay là đường chắc chắn dẫn
tới lệch (giao ước 7.1).

Ba quy tắc của giao ước được cài ở đây:
- 2.2: gói ngoài bảng sysid/compid bị BỎ IM LẶNG — không log, không tính là lỗi.
- 7.6: khoá chữ ký đã đặt thì gói không ký hoặc sai chữ ký bị bỏ, tăng rx_bad_sig, KHÔNG xử lý lệnh.
- 7.4: rx_drop suy từ khoảng trống seq, đếm riêng theo từng (sysid, compid).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .dialect import drone_gcs as mav

log = logging.getLogger(__name__)

GCS = (255, 190)  # sysid, compid — MAV_COMP_ID_MISSIONPLANNER (giao ước 2.2)
PI = (1, 191)     # MAV_COMP_ID_ONBOARD_COMPUTER

UINT16_MAX = 0xFFFF
UINT32_MAX = 0xFFFFFFFF


@dataclass
class Counters:
    """Tám trường của DRONE_LINK_STATS (giao ước 7.4)."""
    rx_ok: int = 0
    rx_drop: int = 0
    rx_bad_crc: int = 0
    rx_bad_sig: int = 0
    tx_sent: int = 0
    tx_dropped: int = 0


class Codec:
    def __init__(self, signing_key: bytes | None = None, timestamp: int = 0) -> None:
        if signing_key is not None and len(signing_key) != 32:
            raise ValueError("khoá chữ ký phải đúng 32 byte (giao ước 7.6)")
        self.counters = Counters()
        self.mav = mav.MAVLink(file=None, srcSystem=GCS[0], srcComponent=GCS[1])
        self.mav.robust_parsing = True  # gói hỏng thành MAVLink_bad_data để phân loại, thay vì ném ra
        self._last_seq: dict[tuple[int, int], int] = {}
        if signing_key is not None:
            self.mav.signing.secret_key = signing_key
            self.mav.signing.sign_outgoing = True
            self.mav.signing.link_id = 0
            self.mav.signing.timestamp = timestamp

    @property
    def signing_on(self) -> bool:
        return self.mav.signing.secret_key is not None

    @property
    def timestamp(self) -> int:
        """Đồng hồ chữ ký hiện tại — phải lưu lại và nạp lại khi khởi động để không lùi (giao ước 7.6)."""
        return self.mav.signing.timestamp

    def encode(self, msg) -> bytes:
        """Gói đã dựng bằng mav.<tên>_encode(...) thành byte trên dây, có chữ ký nếu đã bật khoá.

        seq phải tự tăng ở đây: pack() của pymavlink CHỈ đọc mav.seq, việc tăng nằm trong send() mà
        ta không dùng (send() ghi vào file, ta cần bytes để đẩy qua UDP). Quên chỗ này thì mọi gói
        mang seq = 0, và bộ đếm mất gói của bên kia báo (0-0-1) & 0xFF = 255 gói mất mỗi gói nhận —
        tức DRONE_LINK_STATS nói dối ngay từ ngày đầu. Pi tìm ra lỗi này ở phép kiểm 10.A.
        """
        buf = msg.pack(self.mav)
        self.mav.seq = (self.mav.seq + 1) % 256
        return buf

    def decode(self, data: bytes) -> list:
        """Một datagram -> danh sách bản tin đã nhận từ Pi. Cập nhật bộ đếm 7.4.

        Buffer được đặt lại mỗi datagram: với UDP, ranh giới datagram LÀ ranh giới gói, nên byte thừa
        của một gói cụt không được phép làm hỏng gói của datagram sau.
        """
        self.mav.buf = bytearray()
        self.mav.buf_index = 0
        self.mav.expected_length = 12
        out = []
        msg = self.mav.parse_char(data)
        while msg is not None:
            if isinstance(msg, mav.MAVLink_bad_data):
                self._note_bad(msg.reason)
            elif (msg.get_srcSystem(), msg.get_srcComponent()) == PI:
                self.counters.rx_ok += 1
                self._note_seq(msg)
                out.append(msg)
            # còn lại: gói của bên thứ ba — bỏ im lặng (giao ước 2.2)
            msg = self.mav.parse_char(b"")
        return out

    def _note_bad(self, reason: str) -> None:
        # pymavlink không có mã lỗi, chỉ có chuỗi. Hai loại này cần phân biệt vì cách sửa khác hẳn
        # nhau: sai chữ ký là chuyện an ninh, sai CRC là chuyện đường truyền (giao ước 7.4).
        low = reason.lower()
        if "signature" in low:
            self.counters.rx_bad_sig += 1
            log.warning("gói sai/thiếu chữ ký bị bỏ: %s", reason)
        elif "crc" in low:
            self.counters.rx_bad_crc += 1

    def _note_seq(self, msg) -> None:
        key = (msg.get_srcSystem(), msg.get_srcComponent())
        seq = msg.get_seq()
        prev = self._last_seq.get(key)
        self._last_seq[key] = seq
        if prev is not None:
            self.counters.rx_drop += (seq - prev - 1) & 0xFF
