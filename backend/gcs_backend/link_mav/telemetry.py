"""Telemetry MAVLink -> dict SI cho shadow, CSDL và WebSocket (giao ước 5.1, 5.2, 8.4).

Ba bản tin vào:

- `LOCAL_POSITION_NED` (32) + `ATTITUDE` (30), 5 Hz, **chỉ khi `POS_VALID`**. Hai bản tin chuẩn
  riêng biệt nên phải ghép: giữ tư thế mới nhất, và mỗi khi có vị trí mới thì phát ra một mẫu
  `fast`. Không phát = không biết (5.1 ý 2) — im lặng ở đây **không** phải lỗi.
- `DRONE_TELEMETRY` (42010), 2 Hz cố định kể cả khi không nguồn nào cập nhật.

`mission_state = MISSION_COMPLETE` **không** có nghĩa là nhiệm vụ thành công: nó là đích chung của cả
huỷ lệnh, RTH, `NAV_LAND`, hết lượt thử và hết kế hoạch. Thứ phân biệt là `flight_result` (8.5b).

Quy tắc chi phối cả module: **bit hiệu lực = 0 nghĩa là KHÔNG BIẾT, trả `None`, không trả 0** (R3).
Pin hiện luôn rơi vào nhánh đó vì FC chưa gửi `BATTERY_STATUS` (5.2). Từ 0.7, `lat`/`lon` là vị trí
HỢP NHẤT của EKF quy ra WGS84 (có khi Site có gốc và `POS_VALID`), còn `gps` là chất lượng GPS THÔ.

"NED" ở đây **không phải Bắc/Đông địa lý**: N và E là trục của bản đồ tag, `yaw` đo so với trục N
đó (5.2b ý 2). Đừng đem so với la bàn.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .dialect import drone_gcs as mav

POS_JUMP_M = 1.0  # odom neo lại thì vị trí NHẢY tức thì (5.2b ý 3) — frontend không được nội suy qua

VALID = {  # tên cờ -> bit của valid_flags (5.2)
    "pos": mav.DRONE_POS_VALID,
    "global_pos": mav.DRONE_GLOBAL_POS_VALID,
    "battery": mav.DRONE_BATTERY_VALID,
    "fc_link": mav.DRONE_FC_LINK_VALID,
    "marker": mav.DRONE_MARKER_VALID,
    "gripper": mav.DRONE_GRIPPER_VALID,
    "ekf_healthy": mav.DRONE_EKF_HEALTHY,
    "rssi": mav.DRONE_RSSI_VALID,
    "home": mav.DRONE_HOME_VALID,
}


@dataclass
class TelemetryDecoder:
    att: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # roll, pitch, yaw (độ)
    att_rx: float | None = None
    _last_pos: list[float] | None = None

    def feed(self, msg) -> tuple[str, dict] | None:
        """Trả ("fast", si) | ("telem", si) | None. None = bản tin không thuộc telemetry."""
        kind = msg.get_type()
        if kind == "ATTITUDE":
            self.att = [math.degrees(msg.roll), math.degrees(msg.pitch), math.degrees(msg.yaw)]
            self.att_rx = time.monotonic()
            return None
        if kind == "LOCAL_POSITION_NED":
            return "fast", self._fast(msg)
        if kind == "DRONE_TELEMETRY":
            return "telem", self._telem(msg)
        return None

    def _fast(self, msg) -> dict:
        pos = [msg.x, msg.y, msg.z]
        jump = self._last_pos is not None and math.dist(pos, self._last_pos) > POS_JUMP_M
        self._last_pos = pos
        return {
            "t_ms": msg.time_boot_ms,
            "t_gcs": time.time() * 1000,
            "pos": pos,                       # N, E, D (m) trong khung bản đồ tag
            "vel": [msg.vx, msg.vy, msg.vz],
            "att": list(self.att),
            "alt_m": -msg.z,                  # so với gốc bản đồ tag, cùng gốc với DRONE_TELEMETRY.alt_m
            "jump": jump,
        }

    def _telem(self, msg) -> dict:
        v = {name: bool(msg.valid_flags & bit) for name, bit in VALID.items()}
        st = msg.status_flags
        armed = bool(st & mav.DRONE_ARMED)
        carrying = bool(st & mav.DRONE_CARRYING)
        return {
            "t_ms": msg.stamp_us // 1000,     # đồng hồ Pi, chỉ để log; độ tươi xét bằng lúc GCS nhận
            "t_gcs": time.time() * 1000,
            "mission_id": msg.mission_id,
            "contract_ver": msg.contract_ver,
            "fsm_state": msg.mission_state,
            "wp_index": msg.current_wp_index,
            "wp_total": msg.wp_total,
            "retry_count": msg.retry_count,
            "failsafe_type": msg.failsafe_type,
            # tag đang TÌM và tag đang BÁM là hai số khác nhau suốt pha MARKER_SEARCH (8.4)
            "expected_tag": None if msg.expected_marker_id < 0 else msg.expected_marker_id,
            "marker_id_tracking": msg.marker_id_tracking if v["marker"] else None,
            "alt_m": msg.alt_m if v["pos"] else None,
            "vel": list(msg.vel_ned) if v["pos"] else None,
            "battery_pct": msg.battery_pct if v["battery"] else None,
            "battery_v": msg.battery_v if v["battery"] else None,
            "lat": msg.lat / 1e7 if v["global_pos"] else None,
            "lon": msg.lon / 1e7 if v["global_pos"] else None,
            "rssi_dbm": msg.gcs_rssi_dbm if v["rssi"] else None,
            "gripper_state": msg.gripper_state if v["gripper"] else None,
            "carrying": carrying if v["gripper"] else None,
            # ARMED và PI_HAS_AUTHORITY chỉ có nghĩa khi đường FC còn sống (5.2)
            "armed": armed if v["fc_link"] else None,
            "pi_has_authority": bool(st & mav.DRONE_PI_HAS_AUTHORITY) if v["fc_link"] else None,
            # nhà của RTH: vẽ từ đây, KHÔNG vẽ thay bằng pad_home khi thiếu (11.P18)
            "home_ne": [msg.home_n_mm / 1000, msg.home_e_mm / 1000] if v["home"] else None,
            # Kết quả chuyến bay GẦN NHẤT, chốt lại tới khi cất cánh chuyến mới (8.5b). 0 = KHÔNG
            # BIẾT (chưa chuyến nào xong, hoặc Pi cũ chưa khai trường này) — đừng hiện gì khi đọc 0.
            "flight_result": msg.flight_result or None,
            "tagmap_crc": msg.tagmap_crc,
            # GPS THÔ từ FC (0.7): 0 = không có GPS / không biết (Pi cũ không gửi trường này).
            "gps": _gps(msg),
            "valid": v,
            "valid_flags_raw": msg.valid_flags,
            "status_flags_raw": st,
        }


def _gps(msg) -> dict | None:
    """Chất lượng GPS thô. None = không có GPS hoặc Pi cũ (< 0.7) — đừng vẽ gì (R3)."""
    fix = getattr(msg, "gps_fix_type", 0)
    if not fix:
        return None
    sats = getattr(msg, "gps_sats", 255)
    hacc = getattr(msg, "gps_hacc_cm", 0xFFFF)
    return {"fix_type": fix, "sats": None if sats == 255 else sats,
            "hacc_m": None if hacc == 0xFFFF else hacc / 100}


def item_alt_to_map(alt_m: float, tag_d_m: float) -> float:
    """`alt_m` của mục kế hoạch so với TAG ĐÍCH -> độ cao so với gốc bản đồ tag (8.3, 5.1 ý 3).

    Hai trường trùng tên, khác gốc — quy đổi chỉ được làm ở đây. `tag_d_m` là toạ độ D (NED) của tag,
    nên độ cao của nó so với gốc là `-tag_d_m`.
    """
    return -tag_d_m + alt_m
