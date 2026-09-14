"""Bản sao bóng trạng thái drone (6.2) + kiểm chứng lệnh bằng phản hồi trạng thái (6.3).

GCS KHÔNG chạy FSM nhiệm vụ. Mọi trạng thái hiển thị lấy từ MISSION_STATE / TELEM_* của drone, kèm tuổi dữ liệu.
Sau khi GCS khởi động lại, shadow dựng lại HOÀN TOÀN từ gói MISSION_STATE kế tiếp, không từ CSDL.
"""
from __future__ import annotations

import asyncio
import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Callable

from ..link import messages as m

STALE_S = 3.0
MS = m.MissionState


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
    last_state_change: float | None = None   # thời điểm GCS nhận, không phải thời điểm drone đổi
    last_state_rx: float | None = None
    stale_since: float | None = None          # đặt khi mất heartbeat
    fast: dict | None = None
    fast_rx: float | None = None
    slow: dict | None = None
    slow_rx: float | None = None
    tag_detect: dict | None = None
    tag_detect_rx: float | None = None

    def apply_mission_state(self, si: dict, now: float) -> bool:
        changed = si["fsm_state"] != self.fsm_state
        if changed:
            self.last_state_change = now
        self.fsm_state = si["fsm_state"]
        self.mission_id = si["mission_id"]
        self.wp_index, self.wp_total = si["wp_index"], si["wp_total"]
        self.expected_tag, self.retry_count = si["expected_tag"], si["retry_count"]
        self.failsafe_type = si["failsafe_type"]
        self.last_state_rx = now
        return changed

    def apply_fast(self, si: dict, now: float) -> None:
        self.fast, self.fast_rx = si, now
        if self.fsm_state is None:  # chưa có MISSION_STATE: tạm lấy fsm từ TELEM_FAST
            self.fsm_state = si["fsm_state"]
        self.wp_index = si["wp_index"]

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
            "fsm_name": MS(self.fsm_state).name if self.fsm_state is not None and self.fsm_state in MS._value2member_map_ else None,
            "mission_id": self.mission_id, "wp_index": self.wp_index, "wp_total": self.wp_total,
            "expected_tag": self.expected_tag, "retry_count": self.retry_count, "failsafe_type": self.failsafe_type,
            "failsafe_name": m.FailsafeType(self.failsafe_type).name if self.failsafe_type in m.FailsafeType._value2member_map_ else None,
            "age_s": None if age is None else round(age, 2),
            "stale": age is None or age > STALE_S,
            "state_age_s": None if self.last_state_change is None else round(now - self.last_state_change, 1),
            "fast": self.fast, "slow": self.slow, "tag_detect": self.tag_detect,
        }


# ─────────────────────────── kiểm chứng lệnh (6.3) ───────────────────────────────────────────

# giai đoạn hiển thị ba bước trên nút khẩn cấp: sending → acked → effective (8.3)
STAGES = ("sending", "acked", "effective", "rejected", "failed", "not_effective", "cancelled")


@dataclass
class TrackedCommand:
    id: int
    kind: str            # simple | goto | emergency | mission
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


def _pos(sh: DroneShadow):
    return sh.fast["pos"] if sh.fast else None


def make_verifier(kind: str, action: str, shadow: DroneShadow, target_ned: list[float] | None = None):
    """Trả (cửa sổ giây, hàm kiểm) theo bảng 6.3; None nếu lệnh không có tiêu chí phản hồi trạng thái."""
    if kind == "simple" and action == "ARM":
        return 2.0, lambda sh: bool(sh.fast and sh.fast["armed"])
    if kind == "simple" and action == "DISARM":
        return 2.0, lambda sh: bool(sh.fast and not sh.fast["armed"])
    if kind == "simple" and action == "TAKEOFF":
        p0 = _pos(shadow)
        alt0 = -p0[2] if p0 else 0.0
        return 5.0, lambda sh: bool(sh.fast and -sh.fast["pos"][2] >= alt0 + 0.5)
    if kind == "goto" and target_ned is not None:
        p0 = _pos(shadow)
        if p0 is None:
            return None
        d0 = math.dist(p0, target_ned)
        state = {"best": d0, "increases": 0, "t0": time.monotonic()}

        def check(sh: DroneShadow) -> bool:
            # khoảng cách tới đích giảm đơn điệu (dung sai nhiễu 0,2 m) trong 3 s đầu
            d = math.dist(sh.fast["pos"], target_ned) if sh.fast else d0
            if d > state["best"] + 0.2:
                state["increases"] += 1
            state["best"] = min(state["best"], d)
            elapsed = time.monotonic() - state["t0"]
            if d < 0.5:
                return True
            return elapsed >= 3.0 and state["increases"] == 0 and state["best"] < d0 - 0.3
        return 5.0, check
    if kind in ("emergency", "simple") and action == "RTH":
        return 2.0, lambda sh: sh.fsm_state == MS.RTH
    if kind == "emergency" and action == "ABORT_MISSION":
        return 2.0, lambda sh: sh.fsm_state == MS.RTH
    if kind == "emergency" and action == "LAND_NOW":
        return 2.0, lambda sh: sh.fsm_state == MS.EMERGENCY_LAND
    if kind == "emergency" and action == "KILL":
        return 2.0, lambda sh: bool(sh.fast and not sh.fast["armed"])
    if kind == "mission" and action == "START":
        return 3.0, lambda sh: sh.fsm_state is not None and sh.fsm_state != MS.IDLE
    return None


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
        if ack["result"] not in ("OK", "DEFERRED"):
            self.set_stage(c, "rejected", ack["result"])
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
