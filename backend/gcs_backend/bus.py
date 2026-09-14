"""Bus sự kiện nội bộ đồng bộ, đơn giản. Callback phải không chặn."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[str, dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, prefix: str, handler: Handler) -> Callable[[], None]:
        """prefix "" = nhận tất cả; "link." = mọi topic bắt đầu bằng "link."."""
        self._subs[prefix].append(handler)
        return lambda: self._subs[prefix].remove(handler)

    def emit(self, topic: str, **data: Any) -> None:
        for prefix, handlers in list(self._subs.items()):
            if topic.startswith(prefix):
                for h in list(handlers):
                    try:
                        h(topic, data)
                    except Exception:  # một subscriber lỗi không được làm sập vòng liên kết
                        log.exception("subscriber lỗi ở topic %s", topic)
