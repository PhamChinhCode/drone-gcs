"""Nạp bản đồ tag GCS -> Pi: COUNT -> REQUEST(seq) -> ITEM(seq) -> ACK (giao ước 8.7, P31).

Cùng hình dạng bắt tay với mission_client.py (mục 3.2), nhưng `tagmap_crc` thay cho `mission_id`
làm khoá tương quan giữa các gói của một lượt: bản đồ sắp gửi tự có một CRC duy nhất, không cần
cấp ID riêng.

`ACCEPTED` ở đây KHÔNG có nghĩa "đã có hiệu lực": Pi đã kiểm và ghi xong nhưng cần khởi động lại
stack mới áp dụng (giao ước 11.6, Pi trả lời P31 câu 1). Muốn biết đã có hiệu lực chưa thì so
`tagmap_crc` của `DRONE_TELEMETRY` với giá trị đã khai ở đây — không có tín hiệu nào khác.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .dialect import drone_gcs as mav
from .tagmap import origin_ints, tagmap_crc, yaw_cdeg

log = logging.getLogger(__name__)

SO_TAG_TOI_DA = 32           # muc 8.7
SILENCE_TIMEOUT_S = 20.0     # Pi hoi lai moi 1 s toi da 5 lan; im lang lau hon the la duong truyen chet


def build_items(tags: list[dict]) -> list[tuple[int, int, int, int, int, int, int]]:
    """[{tag_id, pos_n_m, pos_e_m, pos_d_m?, yaw_deg?, yaw_valid?, enabled?}, ...]
    -> [(seq, tag_id, n_mm, e_mm, d_mm, yaw_valid, yaw_cdeg), ...].

    Cùng thứ tự (sắp theo `tag_id` tăng dần, chỉ tag `enabled`) và cùng phép làm tròn với
    `tagmap_crc()` — hai hàm phải luôn đồng bộ nên đọc chung một nguồn `tags`.
    """
    rows = sorted((t for t in tags if t.get("enabled", True)), key=lambda t: int(t["tag_id"]))
    return [(seq, int(t["tag_id"]), round(t["pos_n_m"] * 1000), round(t["pos_e_m"] * 1000),
             round(t.get("pos_d_m", 0.0) * 1000), 1 if t.get("yaw_valid") else 0,
             yaw_cdeg(t["yaw_deg"]) if t.get("yaw_valid") else 0) for seq, t in enumerate(rows)]


@dataclass(frozen=True)
class TagmapAck:
    result: int
    reason: str

    @property
    def ok(self) -> bool:
        return self.result == mav.DRONE_TAGMAP_ACCEPTED

    def as_dict(self) -> dict:
        name = mav.enums["DRONE_TAGMAP_RESULT"][self.result].name if self.result in \
            mav.enums["DRONE_TAGMAP_RESULT"] else str(self.result)
        return {"result": self.result, "result_name": name, "reason": self.reason, "ok": self.ok}


class TagmapCancelled(Exception):
    """Lượt nạp bị một lượt mới thay thế."""


@dataclass
class _Upload:
    crc: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class TagmapClient:
    def __init__(self, link) -> None:
        self.link = link
        self._active: _Upload | None = None
        link.handlers.append(self._on_msg)

    def _on_msg(self, msg) -> None:
        if msg.get_type() in ("DRONE_TAGMAP_REQUEST", "DRONE_TAGMAP_ACK") and self._active is not None:
            self._active.queue.put_nowait(msg)

    async def upload(self, tags: list[dict], origin: dict | None = None) -> TagmapAck:
        """`origin` = gốc WGS84 của bản đồ (`tagmap.site_origin`), None = không gửi gốc (0.7)."""
        items = build_items(tags)
        if not 1 <= len(items) <= SO_TAG_TOI_DA:
            raise ValueError(f"số tag phải trong 1..{SO_TAG_TOI_DA}, đang có {len(items)}")
        crc = tagmap_crc(tags, origin)
        if self._active is not None:
            self._active.queue.put_nowait(None)  # COUNT moi huy luot cu (dung nhu muc 3.2)
        up = _Upload(crc)
        self._active = up
        try:
            lat_e7, lon_e7, alt_mm, yaw_cdeg = origin_ints(origin) if origin else (0, 0, 0, 0)
            self.link.send(mav.MAVLink_drone_tagmap_count_message(
                tagmap_crc=crc, count=len(items), origin_valid=1 if origin else 0,
                origin_lat_e7=lat_e7, origin_lon_e7=lon_e7, origin_alt_mm=alt_mm,
                north_yaw_cdeg=yaw_cdeg))
            while True:
                msg = await asyncio.wait_for(up.queue.get(), SILENCE_TIMEOUT_S)
                if msg is None:
                    raise TagmapCancelled(f"lượt nạp bản đồ (crc 0x{crc:08X}) bị lượt mới thay thế")
                if msg.tagmap_crc != crc:
                    continue  # ITEM/REQUEST của lượt cũ đến trễ — không được lẫn vào lượt này
                if msg.get_type() == "DRONE_TAGMAP_ACK":
                    ack = TagmapAck(msg.result, _text(msg.reason))
                    log.info("nạp bản đồ tag (crc 0x%08X): %s", crc, ack.as_dict())
                    return ack
                if 0 <= msg.seq < len(items):
                    self._send_item(crc, items[msg.seq])
                else:
                    log.warning("Pi hỏi tag %d ngoài lượt %d tag — bỏ qua", msg.seq, len(items))
        finally:
            if self._active is up:
                self._active = None

    def _send_item(self, crc: int, item: tuple[int, int, int, int, int, int, int]) -> None:
        seq, tag_id, n_mm, e_mm, d_mm, yaw_valid, yaw = item
        self.link.send(mav.MAVLink_drone_tagmap_item_message(
            tagmap_crc=crc, tag_id=tag_id, n_mm=n_mm, e_mm=e_mm, d_mm=d_mm, seq=seq,
            yaw_valid=yaw_valid, yaw_cdeg=yaw))


def _text(raw) -> str:
    return raw.decode("ascii", "replace").rstrip("\x00") if isinstance(raw, (bytes, bytearray)) else str(raw)
