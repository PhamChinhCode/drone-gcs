"""Đối chiếu ngưỡng với Pi — CHỈ ĐỌC (giao ước 9.4).

`PARAM_REQUEST_LIST` (21) -> Pi trả tám `PARAM_VALUE` (22). `PARAM_SET` (23) thì Pi bỏ qua và phát
STATUSTEXT mức WARN: ngưỡng chỉ được sửa ở `safety.yaml` phía Pi, nơi có người chịu trách nhiệm và
có lịch sử git. Vì vậy module này không có hàm ghi — thiếu sót đó là cố ý.
"""
from __future__ import annotations

import asyncio
import logging

from .codec import PI
from .dialect import drone_gcs as mav

log = logging.getLogger(__name__)

# tên trên dây (≤ 16 ký tự, giới hạn MAVLink) -> khoá bảng system_config phía GCS
PARAM_MAP = {
    "LOW_BATT_PCT": "low_battery_pct",
    "CRIT_BATT_PCT": "critical_battery_pct",
    "LINK_LOST_S": "link_lost_timeout_s",
    "MARKER_SRCH_S": "marker_search_timeout_s",
    "GRIP_CONF_S": "grip_confirm_timeout_s",
    "MAX_RETRIES": "max_retries",
    "TAKEOFF_ALT_M": "takeoff_alt_m",
    "ACCEPT_RAD_M": "acceptance_radius_m",
}

LIST_TIMEOUT_S = 10.0  # im lặng quá lâu giữa hai PARAM_VALUE thì bỏ cuộc, trả về phần đã đọc


class ParamClient:
    def __init__(self, link) -> None:
        self.link = link
        self.values: dict[str, float] = {}
        self._queue: asyncio.Queue | None = None
        link.handlers.append(self._on_msg)

    def _on_msg(self, msg) -> None:
        if msg.get_type() != "PARAM_VALUE":
            return
        name = msg.param_id if isinstance(msg.param_id, str) else msg.param_id.decode("ascii", "replace")
        name = name.rstrip("\x00")
        self.values[name] = msg.param_value
        if self._queue is not None:
            self._queue.put_nowait((name, msg.param_count))

    async def request_all(self) -> dict[str, float]:
        """Đọc toàn bộ danh sách. Trả về cả khi thiếu: đọc được ba phần tư vẫn hơn không đọc được gì."""
        self._queue = asyncio.Queue()
        got: dict[str, float] = {}
        try:
            self.link.send(mav.MAVLink_param_request_list_message(PI[0], PI[1]))
            while True:
                try:
                    name, count = await asyncio.wait_for(self._queue.get(), LIST_TIMEOUT_S)
                except asyncio.TimeoutError:
                    log.warning("đọc tham số: im lặng %.0f s, mới có %d", LIST_TIMEOUT_S, len(got))
                    return got
                got[name] = self.values[name]
                if len(got) >= count:
                    return got
        finally:
            self._queue = None

    def compare(self, gcs_config: dict[str, float]) -> dict[str, dict]:
        """So giá trị Pi báo về với bảng system_config. Tham số không đọc được thì KHÔNG báo là khớp."""
        out = {}
        for wire, key in PARAM_MAP.items():
            drone = self.values.get(wire)
            ours = gcs_config.get(key)
            out[key] = {"wire_name": wire, "gcs": ours, "drone": drone,
                        "in_sync": None if drone is None or ours is None else abs(drone - ours) < 1e-6}
        return out
