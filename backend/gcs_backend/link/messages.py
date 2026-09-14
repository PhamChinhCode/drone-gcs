"""NGUỒN SỰ THẬT DUY NHẤT về layout bản tin (mục 5.3, 5.4, 7.2) và quy đổi đơn vị (mục 3.1).

- Trên dây: số nguyên — mm, cm/s, centi-độ.
- Trong CSDL/API: số thực SI — m, m/s, độ.
Quy đổi CHỈ xảy ra ở file này.

Mọi lớp bản tin có `assert SIZE == ...` chạy lúc import: lệch layout với struct C → backend không khởi động.
Các bản tin mà đặc tả chỉ cho kích thước (không cho từng byte) được đề xuất layout ở đây,
đánh dấu "ĐỀ XUẤT" — cần chốt với đội firmware/Pi 4 (docs/DIEM_CAN_CHOT.md).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, fields
from enum import IntEnum
from typing import ClassVar

from .crc import crc32

# ────────────────────────────── enum dùng chung (mục 14.1 — CHỜ CHỐT với Pi 4) ──────────────


class MissionState(IntEnum):
    IDLE = 0
    TAKEOFF = 1
    ENROUTE = 2
    MARKER_SEARCH = 3
    PRECISION_LAND = 4
    ACTUATE_GRIPPER = 5
    RETRY_LOITER = 6
    RTH = 7
    EMERGENCY_LAND = 8
    MISSION_COMPLETE = 9
    FAILSAFE = 10


class FailsafeType(IntEnum):
    NONE = 0
    MARKER_TIMEOUT = 1
    GRIP_CONFIRM_FAIL = 2
    LINK_LOST = 3
    LOW_BATTERY = 4
    EKF_UNHEALTHY = 5
    FC_COMM_LOST = 6


class GripperState(IntEnum):
    OPEN = 0
    CLOSED = 1
    MOVING = 2
    ERROR = 3


class WpAction(IntEnum):
    NONE = 0
    PICKUP = 1
    DROPOFF = 2
    WAIT = 3


class TagKind(IntEnum):  # ĐỀ XUẤT — dùng trong map_crc và TAGMAP_ENTRY
    HOME = 0
    PICKUP = 1
    DROPOFF = 2
    WAYPOINT = 3


class SimpleAction(IntEnum):  # ĐỀ XUẤT — CMD_SIMPLE.action
    ARM = 1
    DISARM = 2
    TAKEOFF = 3         # param = độ cao cm
    LAND = 4
    HOLD = 5
    PRECISION_LAND = 6  # param = tag_id
    RTH = 7


class MissionCtrlAction(IntEnum):  # ĐỀ XUẤT — MISSION_CTRL.action
    START = 1
    PAUSE = 2
    RESUME = 3
    ABORT = 4


class EmergencyAction(IntEnum):
    RTH = 1
    LAND_NOW = 2
    HOLD = 3
    ABORT_MISSION = 4
    KILL = 5


class RefFrame(IntEnum):
    MAP_NED = 0
    TAG_RELATIVE = 1
    BODY_RELATIVE = 2


class RequestWhat(IntEnum):  # ĐỀ XUẤT — REQUEST.what
    PARAMS = 1
    MISSION_STATE = 2
    TAGMAP_CRC = 3


class AckResult(IntEnum):
    OK = 0
    REJECT_BUSY = 1
    REJECT_INVALID_ARG = 2
    REJECT_WRONG_STATE = 3
    REJECT_CRC = 4
    REJECT_UNSUPPORTED = 5
    REJECT_AUTH = 6
    REJECT_MAP_MISMATCH = 7
    DEFERRED = 8


# bit của TELEM_FAST.flags
TF_ARMED, TF_OFFBOARD, TF_EKF_OK, TF_TAG_LOCK = 0x01, 0x02, 0x04, 0x08
TF_CARRYING, TF_FAILSAFE, TF_GPS_OK = 0x10, 0x20, 0x40

# bit của MISSION_WP.flags
WPF_REQUIRE_TAG_LOCK, WPF_PRECISION_LAND, WPF_PHOTO = 0x01, 0x02, 0x04

# ────────────────────────────── quy đổi đơn vị (điểm DUY NHẤT) ───────────────────────────────


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def m_to_mm(m: float) -> int:
    return _clamp(round(m * 1000), -2**31, 2**31 - 1)


def m_to_mm16(m: float) -> int:
    return _clamp(round(m * 1000), -32768, 32767)


def mm_to_m(mm: int) -> float:
    return mm / 1000.0


def mps_to_cms(v: float) -> int:
    return _clamp(round(v * 100), -32768, 32767)


def mps_to_ucms(v: float) -> int:
    return _clamp(round(v * 100), 0, 65535)


def cms_to_mps(v: int) -> float:
    return v / 100.0


def deg_to_cdeg_signed(deg: float) -> int:
    """→ int16 trong [−18000, 17999]."""
    c = round(deg * 100) % 36000
    return c - 36000 if c >= 18000 else c


def deg_to_cdeg_unsigned(deg: float) -> int:
    """→ uint16 trong [0, 35999]."""
    return round(deg * 100) % 36000


def cdeg_to_deg(c: int) -> float:
    return c / 100.0


# ────────────────────────────── cơ sở bản tin ────────────────────────────────────────────────


class Message:
    MSG_ID: ClassVar[int]
    FMT: ClassVar[str]
    SIZE: ClassVar[int]
    PRIORITY: ClassVar[int]
    NEED_ACK: ClassVar[bool]

    def pack(self) -> bytes:
        return struct.pack(self.FMT, *(getattr(self, f.name) for f in fields(self)))  # type: ignore[arg-type]

    @classmethod
    def unpack(cls, b: bytes):
        if len(b) != cls.SIZE:
            raise ValueError(f"{cls.__name__}: {len(b)}B != {cls.SIZE}B")
        return cls(*struct.unpack(cls.FMT, b))


REGISTRY: dict[int, type[Message]] = {}


def register(msg_id: int, fmt: str, size: int, priority: int, need_ack: bool):
    def deco(cls):
        cls.MSG_ID, cls.FMT, cls.PRIORITY, cls.NEED_ACK = msg_id, fmt, priority, need_ack
        cls.SIZE = struct.calcsize(fmt) if fmt else 0
        if fmt:
            assert cls.SIZE == size, f"layout {cls.__name__} lệch struct C ({cls.SIZE} != {size})"
            assert len(fields(cls)) == len(struct.unpack(fmt, bytes(size))), cls.__name__
        REGISTRY[msg_id] = cls
        return cls
    return deco


# ────────────────────────────── chiều lên GCS → drone (0x10–0x3F) ────────────────────────────


@register(0x10, "<IIHBB", 12, 1, False)
@dataclass(slots=True)
class HeartbeatGcs(Message):  # ĐỀ XUẤT layout
    t_ms: int
    map_crc: int
    session_id: int
    gcs_flags: int = 0
    reserved: int = 0


@register(0x11, "<HBBh", 6, 2, True)
@dataclass(slots=True)
class CmdSimple(Message):  # ĐỀ XUẤT layout
    cmd_seq: int
    action: int
    reserved: int = 0
    param: int = 0


@register(0x12, "<HBhiiihHB", 22, 2, True)
@dataclass(slots=True)
class CmdGoto(Message):
    cmd_seq: int
    ref_frame: int
    ref_tag_id: int
    x_mm: int
    y_mm: int
    z_mm: int
    yaw_cdeg: int
    max_vel_cms: int
    flags: int

    @classmethod
    def from_si(cls, cmd_seq: int, ref_frame: int, ref_tag_id: int | None, x: float, y: float, z: float,
                yaw_deg: float | None, max_vel: float, climb_first: bool = False) -> "CmdGoto":
        flags = (0x01 if yaw_deg is None else 0) | (0x02 if climb_first else 0)
        return cls(cmd_seq, ref_frame, -1 if ref_tag_id is None else ref_tag_id, m_to_mm(x), m_to_mm(y),
                   m_to_mm(z), deg_to_cdeg_signed(yaw_deg or 0.0), mps_to_ucms(max_vel), flags)


@register(0x13, "<HBBIHH", 12, 2, True)
@dataclass(slots=True)
class MissionBegin(Message):  # ĐỀ XUẤT layout
    mission_id: int
    total: int
    flags: int
    map_crc: int
    cruise_alt_cm: int
    reserved: int = 0


@register(0x14, "<HBBiiihhHHHBB", 28, 2, True)
@dataclass(slots=True)
class MissionWp(Message):
    mission_id: int
    seq: int
    total: int
    pos_n_mm: int
    pos_e_mm: int
    pos_d_mm: int
    yaw_cdeg: int
    tag_id: int
    accept_radius_cm: int
    max_vel_cms: int
    loiter_s: int
    action: int
    flags: int

    @classmethod
    def from_si(cls, mission_id: int, seq: int, total: int, wp: dict) -> "MissionWp":
        flags = (WPF_REQUIRE_TAG_LOCK if wp.get("require_tag_lock") else 0) | \
                (WPF_PRECISION_LAND if wp.get("precision_land") else 0)
        tag = wp.get("tag_id")
        return cls(mission_id, seq, total, m_to_mm(wp["pos_n_m"]), m_to_mm(wp["pos_e_m"]), m_to_mm(wp["pos_d_m"]),
                   deg_to_cdeg_signed(wp.get("yaw_deg") or 0.0), -1 if tag is None else tag,
                   _clamp(round((wp.get("accept_radius_m") or 0) * 100), 0, 65535),
                   mps_to_ucms(wp.get("max_vel_mps") or 0), _clamp(round(wp.get("loiter_s") or 0), 0, 65535),
                   int(WpAction[(wp.get("action") or "none").upper()]), flags)

    def to_si(self) -> dict:
        return {
            "seq": self.seq, "tag_id": None if self.tag_id < 0 else self.tag_id,
            "pos_n_m": mm_to_m(self.pos_n_mm), "pos_e_m": mm_to_m(self.pos_e_mm), "pos_d_m": mm_to_m(self.pos_d_mm),
            "yaw_deg": cdeg_to_deg(self.yaw_cdeg), "accept_radius_m": self.accept_radius_cm / 100,
            "max_vel_mps": cms_to_mps(self.max_vel_cms), "loiter_s": self.loiter_s,
            "action": WpAction(self.action).name.lower(),
            "require_tag_lock": bool(self.flags & WPF_REQUIRE_TAG_LOCK),
            "precision_land": bool(self.flags & WPF_PRECISION_LAND),
        }


@register(0x15, "<HBBI", 8, 2, True)
@dataclass(slots=True)
class MissionEnd(Message):  # ĐỀ XUẤT layout
    mission_id: int
    total: int
    reserved: int
    wp_crc: int


@register(0x16, "<HBBH", 6, 2, True)
@dataclass(slots=True)
class MissionCtrl(Message):  # ĐỀ XUẤT layout
    mission_id: int
    action: int
    reserved: int
    cmd_seq: int


@register(0x17, "<HBBI", 8, 3, True)
@dataclass(slots=True)
class Emergency(Message):
    cmd_seq: int
    action: int
    reserved: int
    confirm_code: int


@register(0x18, "<16sf", 20, 2, True)
@dataclass(slots=True)
class ParamSet(Message):  # ĐỀ XUẤT layout
    name: bytes
    value: float


@register(0x19, "<HBBI", 8, 1, True)
@dataclass(slots=True)
class TagmapBegin(Message):  # ĐỀ XUẤT layout
    site_id: int
    total: int
    reserved: int
    map_crc: int


@register(0x1A, "<BBHiiihHBB", 22, 1, True)
@dataclass(slots=True)
class TagmapEntry(Message):  # ĐỀ XUẤT layout — trường giống hệt đầu vào map_crc
    index: int
    total: int
    tag_id: int
    n_mm: int
    e_mm: int
    d_mm: int
    yaw_cdeg: int
    size_mm: int
    kind: int
    reserved: int = 0


@register(0x1B, "<HBBI", 8, 1, True)
@dataclass(slots=True)
class TagmapEnd(Message):  # ĐỀ XUẤT layout
    site_id: int
    total: int
    reserved: int
    map_crc: int


@register(0x1C, "<BBH", 4, 1, True)
@dataclass(slots=True)
class Request(Message):  # ĐỀ XUẤT layout
    what: int
    reserved: int = 0
    arg: int = 0


# 0x1D–0x1F: GIỮ CHỖ cho GEOFENCE_BEGIN/VERTEX/END — không dùng cho mục đích khác.

# ────────────────────────────── chiều xuống drone → GCS (0x40–0x6F) ──────────────────────────


@register(0x40, "<IIBBH", 12, 1, False)
@dataclass(slots=True)
class HeartbeatDrone(Message):  # ĐỀ XUẤT layout
    t_ms: int
    map_crc: int
    fsm_state: int
    flags: int
    session_id: int


@register(0x41, "<IiiihhhhhHBBBB", 32, 0, False)
@dataclass(slots=True)
class TelemFast(Message):
    t_ms: int
    pos_n_mm: int
    pos_e_mm: int
    pos_d_mm: int
    vel_n_cms: int
    vel_e_cms: int
    vel_d_cms: int
    roll_cdeg: int
    pitch_cdeg: int
    yaw_cdeg: int
    fsm_state: int
    flags: int
    wp_index: int
    battery_pct: int

    def to_si(self) -> dict:
        """Quy đổi sang SI — điểm DUY NHẤT được phép làm việc này."""
        return {
            "t_ms": self.t_ms,
            "pos": [mm_to_m(self.pos_n_mm), mm_to_m(self.pos_e_mm), mm_to_m(self.pos_d_mm)],
            "vel": [cms_to_mps(self.vel_n_cms), cms_to_mps(self.vel_e_cms), cms_to_mps(self.vel_d_cms)],
            "att": [cdeg_to_deg(self.roll_cdeg), cdeg_to_deg(self.pitch_cdeg), cdeg_to_deg(self.yaw_cdeg)],
            "fsm_state": self.fsm_state,
            "flags": self.flags,
            "armed": bool(self.flags & TF_ARMED),
            "offboard": bool(self.flags & TF_OFFBOARD),
            "ekf_ok": bool(self.flags & TF_EKF_OK),
            "tag_lock": bool(self.flags & TF_TAG_LOCK),
            "carrying": bool(self.flags & TF_CARRYING),
            "failsafe_active": bool(self.flags & TF_FAILSAFE),
            "gps_ok": bool(self.flags & TF_GPS_OK),
            "wp_index": self.wp_index,
            "battery_pct": self.battery_pct,
        }

    @classmethod
    def from_si(cls, t_ms: int, pos, vel, att_deg, fsm_state: int, flags: int, wp_index: int,
                battery_pct: int) -> "TelemFast":
        return cls(t_ms & 0xFFFFFFFF, m_to_mm(pos[0]), m_to_mm(pos[1]), m_to_mm(pos[2]),
                   mps_to_cms(vel[0]), mps_to_cms(vel[1]), mps_to_cms(vel[2]),
                   deg_to_cdeg_signed(att_deg[0]), deg_to_cdeg_signed(att_deg[1]),
                   deg_to_cdeg_unsigned(att_deg[2]), fsm_state, flags, wp_index & 0xFF,
                   _clamp(round(battery_pct), 0, 100))


@register(0x42, "<IHhHHHBBBBBBBBBB", 24, 0, False)
@dataclass(slots=True)
class TelemSlow(Message):
    t_ms: int
    batt_mv: int
    batt_ca: int
    batt_mah_used: int
    mission_id: int
    uptime_s: int
    batt_pct: int
    rssi_mag: int
    link_loss_pct: int
    ekf_health: int
    gps_sats: int
    cpu_pct: int
    temp_c: int
    gripper_state: int
    failsafe_type: int
    err_flags: int

    def to_si(self) -> dict:
        return {
            "t_ms": self.t_ms, "batt_v": self.batt_mv / 1000, "batt_a": self.batt_ca / 100,
            "batt_mah_used": self.batt_mah_used, "mission_id": self.mission_id, "uptime_s": self.uptime_s,
            "batt_pct": self.batt_pct, "rssi_dbm": -self.rssi_mag, "link_loss_pct": self.link_loss_pct,
            "ekf_health": self.ekf_health, "gps_sats": self.gps_sats, "cpu_pct": self.cpu_pct,
            "temp_c": self.temp_c, "gripper_state": self.gripper_state, "failsafe_type": self.failsafe_type,
            "err_flags": self.err_flags,
        }


@register(0x43, "<IHBBBBhBBH", 16, 1, False)
@dataclass(slots=True)
class MissionStateMsg(Message):  # ĐỀ XUẤT layout
    t_ms: int
    mission_id: int
    fsm_state: int
    prev_state: int
    wp_index: int
    wp_total: int
    expected_tag: int
    retry_count: int
    failsafe_type: int
    reserved: int = 0

    def to_si(self) -> dict:
        return {
            "t_ms": self.t_ms, "mission_id": self.mission_id, "fsm_state": self.fsm_state,
            "prev_state": self.prev_state, "wp_index": self.wp_index, "wp_total": self.wp_total,
            "expected_tag": None if self.expected_tag < 0 else self.expected_tag,
            "retry_count": self.retry_count, "failsafe_type": self.failsafe_type,
        }


@register(0x44, "<IhhhhhBB", 16, 0, False)
@dataclass(slots=True)
class TagDetect(Message):
    t_ms: int
    tag_id: int
    rel_x_mm: int
    rel_y_mm: int
    rel_z_mm: int
    yaw_rel_cdeg: int
    quality: int
    n_visible: int

    def to_si(self) -> dict:
        return {
            "t_ms": self.t_ms, "tag_id": self.tag_id,
            "rel": [mm_to_m(self.rel_x_mm), mm_to_m(self.rel_y_mm), mm_to_m(self.rel_z_mm)],
            "yaw_rel_deg": cdeg_to_deg(self.yaw_rel_cdeg), "quality": self.quality, "n_visible": self.n_visible,
        }


@register(0x45, "", 8, 2, True)
@dataclass(slots=True)
class Event(Message):  # 8B cố định + text UTF-8 — ĐỀ XUẤT layout
    t_ms: int
    severity: int   # 0 info, 1 warn, 2 error, 3 critical
    category: int   # xem EVENT_CATEGORIES
    code: int
    text: str

    _HEAD: ClassVar[str] = "<IBBH"
    MAX_TEXT: ClassVar[int] = 200

    def pack(self) -> bytes:
        return struct.pack(self._HEAD, self.t_ms, self.severity, self.category, self.code) + \
            self.text.encode("utf-8")[:self.MAX_TEXT]

    @classmethod
    def unpack(cls, b: bytes) -> "Event":
        if len(b) < 8:
            raise ValueError("Event quá ngắn")
        t, sev, cat, code = struct.unpack_from(cls._HEAD, b)
        return cls(t, sev, cat, code, b[8:].decode("utf-8", errors="replace"))

    def to_si(self) -> dict:
        return {"t_ms": self.t_ms, "severity": self.severity,
                "category": EVENT_CATEGORIES.get(self.category, "unknown"), "code": self.code, "message": self.text}


EVENT_CATEGORIES = {0: "failsafe", 1: "command", 2: "link", 3: "gripper", 4: "marker", 5: "user", 6: "mission"}
EVENT_CATEGORY_IDS = {v: k for k, v in EVENT_CATEGORIES.items()}


@register(0x46, "<16sf", 20, 1, False)
@dataclass(slots=True)
class ParamValue(Message):  # ĐỀ XUẤT layout
    name: bytes
    value: float

    def to_si(self) -> dict:
        return {"name": self.name.rstrip(b"\0").decode("ascii", errors="replace"), "value": self.value}


@register(0x47, "<HHHHbBH", 12, 0, False)
@dataclass(slots=True)
class LinkStat(Message):  # ĐỀ XUẤT layout — thống kê phía ESP32-AIR
    tx_count: int
    tx_ok: int
    tx_fail: int
    rx_count: int
    rssi_dbm: int
    loss_pct: int
    rtt_ms: int

    def to_si(self) -> dict:
        return {"tx_count": self.tx_count, "tx_ok": self.tx_ok, "tx_fail": self.tx_fail, "rx_count": self.rx_count,
                "rssi_dbm": self.rssi_dbm, "loss_pct": self.loss_pct, "rtt_ms": self.rtt_ms}


# ────────────────────────────── dongle (chan_id 0x02, không phát ra không khí) ───────────────


@register(0x70, "<IIIIbBH", 20, 0, False)
@dataclass(slots=True)
class DongleStat(Message):  # ĐỀ XUẤT — ESP32-GCS gửi mỗi 1 s (mục 4.2 điều 1)
    tx_count: int
    tx_ok: int
    tx_fail: int
    rx_count: int
    last_rssi_dbm: int
    queue_depth: int
    telem_dropped: int

    def to_si(self) -> dict:
        return {"tx_count": self.tx_count, "tx_ok": self.tx_ok, "tx_fail": self.tx_fail, "rx_count": self.rx_count,
                "last_rssi_dbm": self.last_rssi_dbm, "queue_depth": self.queue_depth,
                "telem_dropped": self.telem_dropped}


@register(0x71, "<BB6sH", 10, 1, False)
@dataclass(slots=True)
class DongleConfig(Message):  # ĐỀ XUẤT — đặt kênh / LR / MAC peer cho dongle
    channel: int
    lr_mode: int
    peer_mac: bytes
    reserved: int = 0


# ────────────────────────────── dùng chung ───────────────────────────────────────────────────


@register(0x7F, "<BHBB", 5, 2, False)
@dataclass(slots=True)
class Ack(Message):
    ack_msg_id: int
    ack_seq: int
    result: int
    detail: int = 0


def decode(msg_id: int, payload: bytes) -> Message:
    cls = REGISTRY.get(msg_id)
    if cls is None:
        raise ValueError(f"msg_id 0x{msg_id:02X} không hỗ trợ")
    return cls.unpack(payload)


# ────────────────────────────── checksum bản đồ tag / kế hoạch (mục 3.3, 5.6) ─────────────────

TAG_KIND_IDS = {"home": TagKind.HOME, "pickup": TagKind.PICKUP, "dropoff": TagKind.DROPOFF,
                "waypoint": TagKind.WAYPOINT}


def tag_wire_tuple(tag: dict) -> tuple[int, int, int, int, int, int, int]:
    """(tag_id, n_mm, e_mm, d_mm, yaw_cdeg, size_mm, kind) từ tag dạng SI."""
    return (int(tag["tag_id"]), m_to_mm(tag["pos_n_m"]), m_to_mm(tag["pos_e_m"]), m_to_mm(tag.get("pos_d_m", 0.0)),
            deg_to_cdeg_signed(tag.get("yaw_deg", 0.0)), _clamp(round(tag["tag_size_m"] * 1000), 0, 65535),
            int(TAG_KIND_IDS[tag["kind"]]))


def map_crc_from_wire(entries: list[tuple[int, int, int, int, int, int, int]]) -> int:
    buf = b"".join(struct.pack("<HiiihHB", *e) for e in sorted(entries, key=lambda e: e[0]))
    return crc32(buf)


def compute_map_crc(tags: list[dict]) -> int:
    """map_crc = CRC32 trên (uint16 tag_id, int32 n, e, d mm, int16 yaw_cdeg, uint16 size_mm, uint8 kind),
    tag sắp theo tag_id tăng dần. Chỉ tính tag đang bật."""
    return map_crc_from_wire([tag_wire_tuple(t) for t in tags if t.get("enabled", True)])


def compute_wp_crc(wps: list[MissionWp]) -> int:
    """wp_crc = CRC32 trên payload MISSION_WP theo thứ tự seq — ĐỀ XUẤT."""
    return crc32(b"".join(w.pack() for w in sorted(wps, key=lambda w: w.seq)))
