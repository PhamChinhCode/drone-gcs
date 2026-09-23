"""Bản sao bóng trạng thái drone + kiểm chứng lệnh bằng phản hồi trạng thái (giao ước 5.2, 6.2).

GCS KHÔNG chạy FSM nhiệm vụ. Mọi thứ hiển thị lấy từ `DRONE_TELEMETRY` / `LOCAL_POSITION_NED` của
drone, kèm tuổi dữ liệu. Sau khi GCS khởi động lại, shadow dựng lại HOÀN TOÀN từ gói telemetry kế
tiếp, không từ CSDL — đó là lý do mục 5.1 bắt `DRONE_TELEMETRY` phát định kỳ kể cả khi không có gì
đổi (R5).

**Lệnh được coi là có hiệu lực khi TRẠNG THÁI đổi, không phải khi có ACK** (4.2): `ACCEPTED` cho lệnh
chạy dài chỉ nghĩa là FSM đã nhận và bắt đầu chuyển trạng thái.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from typing import Callable

from ..link_mav.dialect import drone_gcs as mav

STALE_S = 3.0  # telemetry 2 Hz: quá 3 s là đã lỡ vài gói

IDLE = mav.DRONE_STATE_IDLE
RTH = mav.DRONE_STATE_RTH
LANDING = mav.DRONE_STATE_EMERGENCY_LAND  # tên gọi của giao ước; đây là hạ cánh CHUNG (8.5)
COMPLETE = mav.DRONE_STATE_MISSION_COMPLETE
FAILSAFE = mav.DRONE_STATE_FAILSAFE

_STATE_NAME = {v: e.name.replace("DRONE_STATE_", "") for v, e in mav.enums["DRONE_MISSION_STATE"].items()}
_FS_NAME = {v: e.name.replace("DRONE_FS_", "") for v, e in mav.enums["DRONE_FAILSAFE_TYPE"].items()}


@dataclass
class DroneShadow:
    drone_id: int
    fsm_state: int | None = None
    mission_id: int = 0
    wp_index: int = 0
    wp_total: int = 0
    expected_tag: int | None = None
    retry_count: int = 0
    failsafe_type: int = 0
    contract_ver: int | None = None
    tagmap_crc: int | None = None
    last_state_change: float | None = None  # thời điểm GCS nhận, không phải thời điểm drone đổi
    last_state_rx: float | None = None
    fast: dict | None = None
    fast_rx: float | None = None
    telem: dict | None = None

    def apply_fast(self, si: dict, now: float) -> None:
        self.fast, self.fast_rx = si, now

    def apply_telem(self, si: dict, now: float) -> bool:
        changed = si["fsm_state"] != self.fsm_state
        if changed:
            self.last_state_change = now
        self.fsm_state = si["fsm_state"]
        self.mission_id = si["mission_id"]
        self.wp_index, self.wp_total = si["wp_index"], si["wp_total"]
        self.expected_tag, self.retry_count = si["expected_tag"], si["retry_count"]
        self.failsafe_type = si["failsafe_type"]
        self.contract_ver, self.tagmap_crc = si["contract_ver"], si["tagmap_crc"]
        self.telem, self.last_state_rx = si, now
        return changed

    def stale_fast(self, now: float | None = None) -> bool:
        """Pi NGỪNG PHÁT vị trí khi mất POS_VALID, nên im lặng ở đây nghĩa là không còn biết vị trí."""
        if self.fast_rx is None:
            return True
        return (now or time.monotonic()) - self.fast_rx > 1.0

    @property
    def armed(self) -> bool | None:
        """None = không biết (đường FC không còn tươi) — không được coi là 'đã disarm'."""
        return self.telem["armed"] if self.telem else None

    @property
    def emergency(self) -> bool:
        """Giá trị 8 là hạ cánh CHUNG; chỉ `failsafe_type` mới phân biệt khẩn cấp (8.5)."""
        return self.failsafe_type != 0

    def age(self, now: float | None = None) -> float | None:
        ts = [t for t in (self.fast_rx, self.last_state_rx) if t is not None]
        if not ts:
            return None
        return (now or time.monotonic()) - max(ts)

    def snapshot(self) -> dict:
        now = time.monotonic()
        age = self.age(now)
        return {
            "drone_id": self.drone_id,
            "fsm_state": self.fsm_state,
            "fsm_name": _STATE_NAME.get(self.fsm_state) if self.fsm_state is not None else None,
            "mission_id": self.mission_id, "wp_index": self.wp_index, "wp_total": self.wp_total,
            "expected_tag": self.expected_tag, "retry_count": self.retry_count,
            "failsafe_type": self.failsafe_type,
            "failsafe_name": _FS_NAME.get(self.failsafe_type),
            "emergency": self.emergency,
            "contract_ver": self.contract_ver, "tagmap_crc": self.tagmap_crc,
            "age_s": None if age is None else round(age, 2),
            "stale": age is None or age > STALE_S,
            "state_age_s": None if self.last_state_change is None else round(now - self.last_state_change, 1),
            "fast": self.fast, "telem": self.telem,
        }


# ─────────────────────────── kiểm chứng lệnh ─────────────────────────────────────────────────

# giai đoạn hiển thị trên nút: sending → acked → effective
STAGES = ("sending", "acked", "effective", "rejected", "failed", "not_effective", "cancelled")


@dataclass
class TrackedCommand:
    id: int
    kind: str            # simple | emergency | mission
    action: str
    stage: str = "sending"
    created: float = field(default_factory=time.time)
    ack: dict | None = None
    detail: str | None = None
    verify_window_s: float | None = None
    user_id: int | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _check: Callable[[DroneShadow], bool] | None = None
    _deadline: float | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "action": self.action, "stage": self.stage,
                "created": self.created, "ack": self.ack, "detail": self.detail,
                "verify_window_s": self.verify_window_s}


def make_verifier(action: str, shadow: DroneShadow):
    """Trả (cửa sổ giây, hàm kiểm) hoặc None nếu lệnh không có tiêu chí phản hồi trạng thái.

    Mọi tiêu chí đều đọc từ `DRONE_TELEMETRY` 2 Hz, nên cửa sổ tính bằng giây chứ không mili giây.
    """
    if action == "DISARM":
        # armed is False, KHÔNG phải `not armed`: None nghĩa là mất đường FC, lúc đó không kết luận được
        return 3.0, lambda sh: sh.armed is False
    if action == "RTH":
        return 3.0, lambda sh: sh.fsm_state == RTH
    if action == "LAND":
        return 3.0, lambda sh: sh.fsm_state in (LANDING, IDLE)
    if action == "ABORT_MISSION":
        return 5.0, lambda sh: sh.fsm_state in (LANDING, IDLE)
    if action == "START":
        return 5.0, lambda sh: sh.fsm_state not in (None, IDLE, COMPLETE)
    return None  # PAUSE/CONTINUE: Pi chưa hiện thực, trả UNSUPPORTED (11.P7)


class CommandTracker:
    def __init__(self, shadow: DroneShadow, on_update: Callable[[TrackedCommand], None]) -> None:
        self.shadow = shadow
        self.on_update = on_update
        self._ids = itertools.count(1)
        self.commands: dict[int, TrackedCommand] = {}

    def new(self, kind: str, action: str, user_id: int | None) -> TrackedCommand:
        c = TrackedCommand(next(self._ids), kind, action, user_id=user_id)
        self.commands[c.id] = c
        if len(self.commands) > 200:
            for k in sorted(self.commands)[:50]:
                self.commands.pop(k)
        self.on_update(c)
        return c

    def set_stage(self, c: TrackedCommand, stage: str, detail: str | None = None) -> None:
        c.stage = stage
        if detail:
            c.detail = detail
        self.on_update(c)

    def acked(self, c: TrackedCommand, ack: dict, verifier) -> None:
        c.ack = ack
        if not ack["ok"]:
            self.set_stage(c, "rejected", ack["result_name"])
            return
        if verifier is None:
            self.set_stage(c, "acked")
            return
        c.verify_window_s, c._check = verifier
        c._deadline = time.monotonic() + c.verify_window_s
        self.set_stage(c, "acked")
        self.evaluate()

    def evaluate(self) -> list[TrackedCommand]:
        """Gọi mỗi khi shadow cập nhật và định kỳ. Trả danh sách lệnh vừa quá cửa sổ mà không có hiệu lực."""
        now = time.monotonic()
        failed = []
        for c in self.commands.values():
            if c.stage != "acked" or c._check is None:
                continue
            if c._check(self.shadow):
                c._check = None
                self.set_stage(c, "effective")
            elif c._deadline is not None and now > c._deadline:
                c._check = None
                self.set_stage(c, "not_effective", "lệnh đã nhận nhưng không có hiệu lực")
                failed.append(c)
        return failed

    def recent(self, n: int = 50) -> list[dict]:
        return [c.as_dict() for c in sorted(self.commands.values(), key=lambda c: -c.id)[:n]]
