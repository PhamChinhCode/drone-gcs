"""Gương của safety.yaml phía Pi 4 (mục 10.1). Giá trị thật đọc từ bảng system_config."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Thresholds:
    low_battery_pct: float = 25.0
    critical_battery_pct: float = 15.0
    link_lost_timeout_s: float = 10.0
    marker_search_timeout_s: float = 20.0
    max_retries: float = 3
    takeoff_alt_m: float = 5.0
    acceptance_radius_m: float = 1.5

    @classmethod
    def from_map(cls, cfg: dict[str, float]) -> "Thresholds":
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})
