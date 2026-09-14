"""WebSocket đẩy telemetry (7.6).

Quy tắc: GỘP gói trước khi đẩy — gom trong cửa sổ 100 ms rồi gửi một khung {"type":"batch","items":[...]},
tránh đánh thức vòng lặp sự kiện trình duyệt cho từng bản tin.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)
MAX_CLIENT_BACKLOG = 50


class WsHub:
    def __init__(self, batch_ms: int = 100) -> None:
        self.batch_s = batch_ms / 1000
        self._clients: set[asyncio.Queue[str]] = set()
        self._pending: list[dict] = []

    def push(self, type_: str, data: dict) -> None:
        if self._clients:
            self._pending.append({"type": type_, **data} if "type" not in data else {"type": type_, "data": data})

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.batch_s)
            if not self._pending:
                continue
            items, self._pending = self._pending, []
            frame = json.dumps({"type": "batch", "items": items}, ensure_ascii=False, default=str)
            for q in list(self._clients):
                if q.qsize() < MAX_CLIENT_BACKLOG:  # client chậm: bỏ khung thay vì phình bộ nhớ
                    q.put_nowait(frame)

    async def serve(self, ws: WebSocket, hello: dict) -> None:
        q: asyncio.Queue[str] = asyncio.Queue()
        await ws.send_text(json.dumps({"type": "hello", **hello}, ensure_ascii=False, default=str))
        self._clients.add(q)

        async def pump_out() -> None:
            while True:
                await ws.send_text(await q.get())

        async def pump_in() -> None:  # chỉ để phát hiện đóng kết nối
            while True:
                await ws.receive_text()

        out, inn = asyncio.create_task(pump_out()), asyncio.create_task(pump_in())
        try:
            await asyncio.wait({out, inn}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._clients.discard(q)
            out.cancel()
            inn.cancel()
