"""Đơn vị lớp 3: link_hdr_t (8B) | payload | auth_tag (0/4B) | crc16 (2B) — mục 5.1, 5.2.

Đơn vị này đi nguyên vẹn qua UART lẫn ESP-NOW; tối đa 250 byte (ESP-NOW v1).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .auth import compute_auth_tag
from .crc import crc16_ccitt_false

MAGIC = 0xD5
HDR_FMT = "<BBBBHH"
HDR_SIZE = struct.calcsize(HDR_FMT)
AUTH_SIZE = 4
CRC_SIZE = 2
MAX_UNIT = 250
MAX_PAYLOAD = MAX_UNIT - HDR_SIZE - AUTH_SIZE - CRC_SIZE  # 236 khi có auth
assert HDR_SIZE == 8, "layout lệch với link_hdr_t phía C"

PROTO_VERSION = 0b01

# flags
F_NEED_ACK = 0x20
F_IS_ACK = 0x10
F_HAS_AUTH = 0x02
F_FRAG = 0x01

# mã node (4 bit)
NODE_GCS = 0x1
NODE_ESP_GCS = 0x2
NODE_ESP_AIR = 0x3
NODE_PI4 = 0x4
NODE_BROADCAST = 0xF

# chan_id của khung UART (mục 5.1)
CHAN_AIR = 0x01
CHAN_DONGLE = 0x02
CHAN_DEBUG = 0x03


class FrameError(ValueError):
    def __init__(self, reason: str, header: "Header | None" = None):
        super().__init__(reason)
        self.reason = reason
        self.header = header


@dataclass(slots=True)
class Header:
    flags: int
    msg_id: int
    src: int
    dst: int
    seq: int
    length: int

    @property
    def version(self) -> int:
        return (self.flags >> 6) & 0x3

    @property
    def priority(self) -> int:
        return (self.flags >> 2) & 0x3

    @property
    def need_ack(self) -> bool:
        return bool(self.flags & F_NEED_ACK)

    @property
    def is_ack(self) -> bool:
        return bool(self.flags & F_IS_ACK)

    @property
    def has_auth(self) -> bool:
        return bool(self.flags & F_HAS_AUTH)


@dataclass(slots=True)
class Unit:
    header: Header
    payload: bytes
    auth_ok: bool | None  # None = không có auth_tag
    raw: bytes


def make_flags(priority: int, need_ack: bool, is_ack: bool = False, has_auth: bool = False) -> int:
    f = (PROTO_VERSION << 6) | ((priority & 0x3) << 2)
    if need_ack:
        f |= F_NEED_ACK
    if is_ack:
        f |= F_IS_ACK
    if has_auth:
        f |= F_HAS_AUTH
    return f


def build(msg_id: int, payload: bytes, seq: int, *, priority: int, need_ack: bool,
          src: int, dst: int, is_ack: bool = False, auth_key: bytes | None = None) -> bytes:
    has_auth = auth_key is not None
    limit = MAX_UNIT - HDR_SIZE - CRC_SIZE - (AUTH_SIZE if has_auth else 0)
    if len(payload) > limit:
        raise FrameError(f"payload {len(payload)}B vượt giới hạn {limit}B")
    flags = make_flags(priority, need_ack, is_ack, has_auth)
    hdr = struct.pack(HDR_FMT, MAGIC, flags, msg_id, ((src & 0xF) << 4) | (dst & 0xF),
                      seq & 0xFFFF, len(payload))
    body = hdr + payload
    if has_auth:
        body += compute_auth_tag(auth_key, body)
    return body + struct.pack("<H", crc16_ccitt_false(body))


def parse(raw: bytes, auth_key: bytes | None = None) -> Unit:
    if len(raw) < HDR_SIZE + CRC_SIZE:
        raise FrameError("đơn vị quá ngắn")
    magic, flags, msg_id, node, seq, length = struct.unpack_from(HDR_FMT, raw)
    if magic != MAGIC:
        raise FrameError("sai magic")
    hdr = Header(flags, msg_id, node >> 4, node & 0xF, seq, length)
    auth_len = AUTH_SIZE if hdr.has_auth else 0
    expected = HDR_SIZE + length + auth_len + CRC_SIZE
    if len(raw) != expected:
        raise FrameError(f"sai độ dài ({len(raw)} != {expected})", hdr)
    body = raw[:-CRC_SIZE]
    (crc,) = struct.unpack_from("<H", raw, len(raw) - CRC_SIZE)
    if crc != crc16_ccitt_false(body):
        raise FrameError("sai CRC", hdr)
    if hdr.version != PROTO_VERSION:
        raise FrameError("sai phiên bản giao thức", hdr)
    payload = raw[HDR_SIZE:HDR_SIZE + length]
    auth_ok: bool | None = None
    if hdr.has_auth:
        tag = raw[HDR_SIZE + length:HDR_SIZE + length + AUTH_SIZE]
        auth_ok = auth_key is not None and compute_auth_tag(auth_key, raw[:HDR_SIZE + length]) == tag
    return Unit(hdr, payload, auth_ok, raw)
