"""Lệnh GCS -> Pi: COMMAND_LONG + chờ COMMAND_ACK (giao ước 4.1, 4.2).

Hai nhóm phát lại, khác nhau ở chỗ *có được bỏ cuộc không*:

- **Thường** (300 MISSION_START, 193 DO_PAUSE_CONTINUE): 1,0 s × 3 rồi báo người vận hành — thao tác
  lại được bằng tay.
- **Khẩn** (20 RTH, 21 LAND, 400 DISARM, 42100 ABORT): 0,5 s **không giới hạn** tới khi có ACK hoặc
  người vận hành huỷ. Trên 4G chập chờn, một lệnh LAND dừng thử sau 3 giây là bỏ drone lại giữa
  không trung. Báo động sau lần thứ 3 nhưng **vẫn tiếp tục phát**.

An toàn của việc phát lại vô hạn đến từ **tính bất biến theo trạng thái** phía Pi (4.2): lệnh đưa FSM
tới một trạng thái đích, đã ở đó rồi thì Pi trả ACCEPTED và không làm gì thêm. Vì vậy `confirmation`
tăng mỗi lần phát lại mà không sợ bị coi là lệnh mới — và cũng vì vậy không được chống trùng theo
`(command, confirmation)`, cách đó tự triệt tiêu.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass

from .codec import PI
from .dialect import drone_gcs as mav

log = logging.getLogger(__name__)

# giao ước 4.1 — sáu lệnh, không hơn. ARM, TAKEOFF, GOTO, HOLD không thuộc kênh này (9.3)
MISSION_START = mav.MAV_CMD_MISSION_START            # 300
RTH = mav.MAV_CMD_NAV_RETURN_TO_LAUNCH               # 20
LAND = mav.MAV_CMD_NAV_LAND                          # 21
ARM_DISARM = mav.MAV_CMD_COMPONENT_ARM_DISARM        # 400
ABORT_MISSION = mav.MAV_CMD_DRONE_ABORT_MISSION      # 42100
PAUSE_CONTINUE = mav.MAV_CMD_DO_PAUSE_CONTINUE       # 193 — Pi trả UNSUPPORTED, GCS ẩn nút (11.P7)

EMERGENCY = frozenset({RTH, LAND, ARM_DISARM, ABORT_MISSION})
DISARM_FORCE = 21196.0  # param2 của lệnh 400: cắt động cơ ở mọi độ cao

NORMAL_PERIOD_S, NORMAL_TRIES = 1.0, 3
EMERGENCY_PERIOD_S = 0.5
ALERT_AFTER = 3  # lần phát thứ 3 chưa có ACK thì báo người vận hành


class CommandTimeout(Exception):
    def __init__(self, command: int):
        super().__init__(f"lệnh {command} không có COMMAND_ACK sau {NORMAL_TRIES} lần")
        self.command = command


@dataclass(frozen=True)
class CommandResult:
    command: int
    result: int
    attempts: int

    @property
    def ok(self) -> bool:
        return self.result == mav.MAV_RESULT_ACCEPTED

    def as_dict(self) -> dict:
        return {"command": self.command, "result": self.result,
                "result_name": mav.enums["MAV_RESULT"][self.result].name if self.result in mav.enums["MAV_RESULT"]
                else str(self.result),
                "ok": self.ok, "attempts": self.attempts}


class CommandClient:
    def __init__(self, link) -> None:
        self.link = link
        self._waiting: dict[int, asyncio.Future] = {}
        link.handlers.append(self._on_msg)

    def _on_msg(self, msg) -> None:
        if msg.get_type() != "COMMAND_ACK":
            return
        fut = self._waiting.get(msg.command)
        if fut is not None and not fut.done():
            fut.set_result(msg.result)

    async def send(self, command: int, *params: float) -> CommandResult:
        """Phát lệnh và chờ ACK. Lệnh khẩn chỉ dừng khi có ACK hoặc khi task bị huỷ."""
        if command in self._waiting:
            raise RuntimeError(f"lệnh {command} đang chờ ACK")
        p = (list(params) + [0.0] * 7)[:7]
        emergency = command in EMERGENCY
        period = EMERGENCY_PERIOD_S if emergency else NORMAL_PERIOD_S
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[command] = fut
        try:
            for attempt in itertools.count():
                if not emergency and attempt >= NORMAL_TRIES:
                    self.link.bus.emit("command.timeout", command=command)
                    raise CommandTimeout(command)
                self.link.send(mav.MAVLink_command_long_message(
                    PI[0], PI[1], command, min(attempt, 255), *p))
                if emergency and attempt + 1 == ALERT_AFTER:
                    # vẫn phát tiếp; người vận hành cần biết để tìm cách khác song song
                    log.warning("lệnh khẩn %d chưa có ACK sau %d lần — vẫn phát lại", command, ALERT_AFTER)
                    self.link.bus.emit("command.unacked", command=command, attempts=ALERT_AFTER)
                try:
                    result = await asyncio.wait_for(asyncio.shield(fut), period)
                except asyncio.TimeoutError:
                    continue
                return CommandResult(command, result, attempt + 1)
        finally:
            self._waiting.pop(command, None)
        raise AssertionError("không tới được")  # itertools.count không kết thúc

    # --- sáu lệnh của mục 4.1, gọi theo tên cho khỏi nhớ số ---

    async def mission_start(self) -> CommandResult:
        return await self.send(MISSION_START)

    async def return_to_launch(self) -> CommandResult:
        return await self.send(RTH)

    async def land(self) -> CommandResult:
        return await self.send(LAND)

    async def disarm(self) -> CommandResult:
        """param1 = 0 là disarm. ARM (param1 = 1) không có ở đây: GCS không được arm (9.3)."""
        return await self.send(ARM_DISARM, 0.0, DISARM_FORCE)

    async def abort_mission(self) -> CommandResult:
        return await self.send(ABORT_MISSION)

    async def pause_continue(self, go: bool) -> CommandResult:
        return await self.send(PAUSE_CONTINUE, 1.0 if go else 0.0)
