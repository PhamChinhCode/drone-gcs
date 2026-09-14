"""Giao thức tải lên ba pha (mục 5.6) cho nhiệm vụ và bản đồ tag.

BEGIN → từng mục một gói, ACK từng gói → END(crc). KHÔNG BAO GIỜ tự kích hoạt bay: START là bản tin riêng.
"""
from __future__ import annotations

from ..link import messages as m
from ..link.session import LinkSession, LinkTimeout

OUTER_RETRIES = 3  # ngoài bảng RETRY của phiên: gửi lại đúng gói bị mất, không gửi lại cả kế hoạch


class UploadError(Exception):
    def __init__(self, stage: str, reason: str):
        super().__init__(f"{stage}: {reason}")
        self.stage, self.reason = stage, reason


async def _send_checked(link: LinkSession, msg: m.Message, stage: str) -> None:
    last = "timeout"
    for _ in range(OUTER_RETRIES):
        try:
            ack = await link.send(msg)
        except LinkTimeout:
            continue
        if ack is not None and ack.result == m.AckResult.OK:
            return
        last = m.AckResult(ack.result).name if ack else "no-ack"
        if ack and ack.result != m.AckResult.REJECT_BUSY:
            break
    raise UploadError(stage, last)


async def upload_mission(link: LinkSession, mission: dict, map_crc: int, progress=None) -> dict:
    mid = mission["id"] & 0xFFFF
    wps_si = mission["waypoints"]
    total = len(wps_si)
    wps = [m.MissionWp.from_si(mid, i, total, w) for i, w in enumerate(wps_si)]
    wp_crc = m.compute_wp_crc(wps)
    await _send_checked(link, m.MissionBegin(mid, total, 0, map_crc, round(mission["cruise_alt_m"] * 100)), "BEGIN")
    for wp in wps:
        await _send_checked(link, wp, f"WP{wp.seq}")
        if progress:
            progress(wp.seq + 1, total)
    await _send_checked(link, m.MissionEnd(mid, total, 0, wp_crc), "END")
    return {"mission_wire_id": mid, "total": total, "wp_crc": wp_crc}


async def sync_tagmap(link: LinkSession, site_id: int, tags: list[dict]) -> int:
    enabled = [t for t in tags if t.get("enabled", True)]
    entries = sorted((m.tag_wire_tuple(t) for t in enabled), key=lambda e: e[0])
    crc = m.map_crc_from_wire(entries)
    total = len(entries)
    await _send_checked(link, m.TagmapBegin(site_id & 0xFFFF, total, 0, crc), "TAGMAP_BEGIN")
    for i, e in enumerate(entries):
        await _send_checked(link, m.TagmapEntry(i, total, *e), f"TAGMAP_ENTRY{i}")
    await _send_checked(link, m.TagmapEnd(site_id & 0xFFFF, total, 0, crc), "TAGMAP_END")
    return crc
