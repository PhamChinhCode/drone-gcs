"""Sinh cảnh báo chủ động theo ngưỡng (F5, 5.8, 10.1).

GCS chỉ PHÁT HIỆN SỚM và ĐỀ NGHỊ — mọi failsafe ở đây đều có bản sao phía drone (10.3).
Cảnh báo vùng bay là CẢNH BÁO, không cưỡng chế (8.4.5, R9).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable

from ..sitedesign.geometry import dist_to_boundary, point_in_polygon
from .thresholds import Thresholds

NEAR_EDGE_M = 3.0

LINK_ALERTS = {
    "dongle_lost": ("LINK_DONGLE_LOST", 3, "Mất dongle — kiểm tra cáp USB tới ESP32-GCS"),
    "drone_silent": ("LINK_DRONE_SILENT", 2,
                     "Drone im lặng nhưng dongle vẫn gửi thành công — nghi treo phần mềm Pi 4"),
    "drone_lost": ("LINK_DRONE_LOST", 1, "Mất liên kết drone (> 3 s) — vị trí trong 3D đang đóng băng"),
    "drone_lost_long": ("LINK_DRONE_LOST_LONG", 3,
                        "Mất liên kết drone > 10 s — drone sẽ tự kích RTH theo cấu hình"),
}


@dataclass
class Alert:
    code: str
    severity: int         # 0 info, 1 warn, 2 error, 3 critical
    category: str
    message: str
    raised_at: float
    active: bool = True
    acked: bool = False
    expires_at: float | None = None


class SafetyMonitor:
    def __init__(self, on_change: Callable[[dict, str], None]) -> None:
        self.on_change = on_change
        self.active: dict[str, Alert] = {}
        self.history: deque[dict] = deque(maxlen=500)

    def raise_(self, code: str, severity: int, category: str, message: str, transient_s: float | None = None) -> None:
        cur = self.active.get(code)
        if cur and cur.message == message and cur.severity == severity:
            if transient_s:
                cur.expires_at = time.time() + transient_s
            return
        a = Alert(code, severity, category, message, time.time(),
                  expires_at=time.time() + transient_s if transient_s else None)
        self.active[code] = a
        d = asdict(a)
        self.history.appendleft(d)
        self.on_change(d, "raise")

    def clear(self, code: str) -> None:
        a = self.active.pop(code, None)
        if a:
            a.active = False
            self.on_change(asdict(a), "clear")

    def clear_prefix(self, prefix: str, keep: str | None = None) -> None:
        for code in [c for c in self.active if c.startswith(prefix) and c != keep]:
            self.clear(code)

    def ack(self, code: str) -> bool:
        a = self.active.get(code)
        if not a:
            return False
        a.acked = True
        self.on_change(asdict(a), "ack")
        return True

    def tick(self) -> None:
        now = time.time()
        for code in [c for c, a in self.active.items() if a.expires_at and a.expires_at < now]:
            self.clear(code)

    def snapshot(self) -> dict:
        return {"active": sorted((asdict(a) for a in self.active.values()), key=lambda a: -a["severity"]),
                "history": list(self.history)[:200]}

    # ── đánh giá ─────────────────────────────────────────────────────────────────────────────

    def evaluate_link(self, state: str) -> None:
        if state == "ok":
            self.clear_prefix("LINK_")
            return
        code, sev, msg = LINK_ALERTS[state]
        self.clear_prefix("LINK_", keep=code)
        self.raise_(code, sev, "link", msg)

    def evaluate_fast(self, si: dict, th: Thresholds, areas: list[dict]) -> None:
        batt = si["battery_pct"]
        if si["armed"] and batt < th.critical_battery_pct:
            self.clear("BATT_LOW")
            self.raise_("BATT_CRITICAL", 3, "safety", f"Pin tới hạn {batt} % (< {th.critical_battery_pct:g} %)")
        elif si["armed"] and batt < th.low_battery_pct:
            self.clear("BATT_CRITICAL")
            self.raise_("BATT_LOW", 1, "safety", f"Pin yếu {batt} % (< {th.low_battery_pct:g} %)")
        elif batt >= th.low_battery_pct or not si["armed"]:
            self.clear("BATT_LOW")
            self.clear("BATT_CRITICAL")

        if si["failsafe_active"]:
            self.raise_("FAILSAFE_ACTIVE", 3, "failsafe", "Drone đang ở chế độ failsafe")
        else:
            self.clear("FAILSAFE_ACTIVE")

        n, e, d = si["pos"]
        airborne = d < -0.3
        operating = [a["vertices"] for a in areas if a["kind"] == "operating" and a.get("enabled", True)
                     and len(a["vertices"]) >= 3]
        no_fly = [a for a in areas if a["kind"] == "no_fly" and a.get("enabled", True) and len(a["vertices"]) >= 3]
        if airborne and operating:
            inside = [poly for poly in operating if point_in_polygon((n, e), poly)]
            if not inside:
                self.clear("AREA_NEAR_EDGE")
                self.raise_("AREA_OUTSIDE", 2, "safety", "Drone ở NGOÀI vùng bay (cảnh báo — drone không tự cưỡng chế)")
            elif min(dist_to_boundary((n, e), p) for p in inside) < NEAR_EDGE_M:
                self.clear("AREA_OUTSIDE")
                self.raise_("AREA_NEAR_EDGE", 1, "safety", "Drone gần biên vùng bay (cảnh báo — không cưỡng chế)")
            else:
                self.clear("AREA_OUTSIDE")
                self.clear("AREA_NEAR_EDGE")
        else:
            self.clear("AREA_OUTSIDE")
            self.clear("AREA_NEAR_EDGE")
        hit = next((a for a in no_fly if airborne and point_in_polygon((n, e), a["vertices"])), None)
        if hit:
            self.raise_("NOFLY_INSIDE", 3, "safety", f"Drone trong vùng cấm '{hit['name']}' (không cưỡng chế)")
        else:
            self.clear("NOFLY_INSIDE")

    def set_map_mismatch(self, mismatch: bool, gcs_crc: int, drone_crc: int | None) -> None:
        if mismatch:
            self.raise_("MAP_MISMATCH", 2, "marker",
                        f"Bản đồ tag lệch: GCS {gcs_crc:08X} ≠ drone "
                        f"{'?' if drone_crc is None else f'{drone_crc:08X}'} — nhiệm vụ bị chặn, cần đồng bộ lại")
        else:
            self.clear("MAP_MISMATCH")

    def set_drift(self, drift: list[dict]) -> None:
        if drift:
            keys = ", ".join(f"{c['key']} (GCS {c['value']} / drone {c['drone_value']})" for c in drift)
            self.raise_("CONFIG_DRIFT", 1, "safety", f"Cấu hình lệch với drone: {keys}")
        else:
            self.clear("CONFIG_DRIFT")
