"""Nạp kế hoạch GCS -> Pi: COUNT -> REQUEST(seq) -> ITEM(seq) -> ACK (giao ước 3.2, 3.3).

Giữ nguyên hình dạng bắt tay của giao thức mission chuẩn (đã kiểm nghiệm nhiều năm: có số thứ tự, có
phát lại, có chống mất gói) nhưng dùng bản tin riêng, vì vị trí waypoint suy từ `expected_marker_id`
chứ không từ toạ độ — nhồi vào MISSION_ITEM_INT thì mọi công cụ MAVLink hiện một nhiệm vụ GPS sai
hoàn toàn (3.1).

**Pi điều khiển nhịp, GCS chỉ đáp.** Pi hỏi từng điểm, hết 1,0 s không thấy thì hỏi lại cùng `seq`,
5 lần rồi bỏ. GCS không tự phát lại, không tự đẩy cả khối — cứ mỗi REQUEST là một ITEM.

GCS **không kiểm kế hoạch hợp lệ hay không** (1.1): `mission_manager_node` là bên duy nhất được từ
chối, và lý do từ chối của nó là thứ người vận hành cần nhất. Ở đây chỉ kiểm những gì không đóng gói
lên dây được.
"""
from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from dataclasses import dataclass, field

from .codec import PI
from .dialect import drone_gcs as mav

log = logging.getLogger(__name__)

CONTRACT_VER = 800  # bản 0.8 — MAJOR*10000 + MINOR*100 (giao ước 6.2)
SILENCE_TIMEOUT_S = 20.0  # Pi hỏi lại mỗi 1 s tối đa 5 lần; im lặng lâu hơn thế là đường truyền chết


def to_ascii(s: str, limit: int) -> bytes:
    """pymavlink giải mã char[] bằng ASCII nên chữ có dấu thành rác — bỏ dấu trước khi gửi (P26)."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return s.encode("ascii", "replace")[:limit]


@dataclass(frozen=True)
class Waypoint:
    """Một mục = bay tới -> tìm marker -> hạ chính xác -> làm action -> cất cánh lại (3.2b)."""
    expected_marker_id: int
    alt_m: float               # so với TAG ĐÍCH của mục này, không phải điểm cất cánh (8.3)
    acceptance_radius_m: float
    max_vel_mps: float
    loiter_s: float = 0.0
    action: int = mav.DRONE_ACTION_NONE


@dataclass(frozen=True)
class MissionAck:
    result: int
    reason: str

    @property
    def ok(self) -> bool:
        return self.result == mav.DRONE_MISSION_ACCEPTED

    def as_dict(self) -> dict:
        name = mav.enums["DRONE_MISSION_RESULT"][self.result].name if self.result in \
            mav.enums["DRONE_MISSION_RESULT"] else str(self.result)
        return {"result": self.result, "result_name": name, "reason": self.reason, "ok": self.ok}


class MissionCancelled(Exception):
    """Lượt nạp bị một lượt mới thay thế — GCS thao tác lại là hợp lệ (3.2)."""


@dataclass
class _Upload:
    mission_id: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class MissionClient:
    def __init__(self, link) -> None:
        self.link = link
        self._active: _Upload | None = None
        link.handlers.append(self._on_msg)

    def _on_msg(self, msg) -> None:
        if msg.get_type() in ("DRONE_MISSION_REQUEST", "DRONE_MISSION_ACK") and self._active is not None:
            self._active.queue.put_nowait(msg)

    async def upload(self, mission_id: int, waypoints: list[Waypoint], plan_name: str = "",
                     max_retries: int = 0, search_timeout_s: float = 0.0) -> MissionAck:
        if self._active is not None:
            self._active.queue.put_nowait(None)  # COUNT mới huỷ lượt cũ (3.2)
        up = _Upload(mission_id)
        self._active = up
        try:
            self.link.send(mav.MAVLink_drone_mission_count_message(
                mission_id=mission_id, issued_stamp_us=_now_us(), search_timeout_s=search_timeout_s,
                count=len(waypoints), max_retries=max_retries,
                plan_name=to_ascii(plan_name, 20), contract_ver=CONTRACT_VER))
            while True:
                msg = await asyncio.wait_for(up.queue.get(), SILENCE_TIMEOUT_S)
                if msg is None:
                    raise MissionCancelled(f"lượt nạp {mission_id} bị lượt mới thay thế")
                if msg.mission_id != mission_id:
                    continue  # ITEM/REQUEST của lượt cũ đến trễ — không được lẫn vào lượt này
                if msg.get_type() == "DRONE_MISSION_ACK":
                    ack = MissionAck(msg.result, _text(msg.reason))
                    log.info("nạp kế hoạch %d: %s", mission_id, ack.as_dict())
                    return ack
                if 0 <= msg.seq < len(waypoints):
                    self._send_item(mission_id, msg.seq, waypoints[msg.seq])
                else:
                    log.warning("Pi hỏi điểm %d ngoài kế hoạch %d điểm — bỏ qua", msg.seq, len(waypoints))
        finally:
            if self._active is up:
                self._active = None

    def _send_item(self, mission_id: int, seq: int, wp: Waypoint) -> None:
        self.link.send(mav.MAVLink_drone_mission_item_message(
            mission_id=mission_id, expected_marker_id=wp.expected_marker_id, alt_m=wp.alt_m,
            acceptance_radius_m=wp.acceptance_radius_m, max_vel_mps=wp.max_vel_mps,
            loiter_s=wp.loiter_s, seq=seq, action=wp.action))


def _now_us() -> int:
    return time.time_ns() // 1000


def _text(raw) -> str:
    return raw.decode("ascii", "replace").rstrip("\x00") if isinstance(raw, (bytes, bytearray)) else str(raw)
