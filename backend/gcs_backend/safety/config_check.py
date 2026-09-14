"""Đối chiếu tham số hai bên khi nối lại liên kết (mục 10.1) — tự động, không phụ thuộc ai nhớ kiểm tra."""
from __future__ import annotations

import asyncio
import logging

from ..data.repo import Database
from ..link import messages as m
from ..link.session import LinkSession, LinkTimeout

log = logging.getLogger(__name__)


class ConfigChecker:
    def __init__(self, db: Database, link: LinkSession, on_result) -> None:
        self.db, self.link, self.on_result = db, link, on_result
        self._task: asyncio.Task | None = None

    def on_param(self, pv: m.ParamValue) -> None:
        name = m.PARAM_IDS.get(pv.param_id)
        if name:
            self.db.set_drone_value(name, f"{pv.value:.4g}")

    def request(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self.link.send(m.Request(m.RequestWhat.PARAMS))
        except LinkTimeout:
            log.warning("REQUEST(PARAMS) không có ACK")
            return
        await asyncio.sleep(1.5)  # chờ loạt PARAM_VALUE
        self.on_result(self.drift())

    def drift(self) -> list[dict]:
        return [c for c in self.db.get_config() if c["in_sync"] == 0]

    async def push(self, key: str, value: float) -> None:
        pid = m.PARAM_NAMES[key]
        await self.link.send(m.ParamSet(pid, 0, float(value)))
