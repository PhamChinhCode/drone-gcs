"""tagmap_crc — giao ước GCS <-> Pi 8.6.

CRC-32 IEEE trên bản ghi little-endian (uint16 tag_id, int32 n_mm, int32 e_mm, int32 d_mm), sắp theo
tag_id tăng dần, chỉ tag đang bật. GCS lưu tag sẵn theo NED mét nên chỉ cần đổi sang mm bằng round()
(Pi đổi từ ENU của tags.yaml: n = y, e = x, d = -z).

Bản 0.7: Site có gốc WGS84 thì nối THÊM một bản ghi (int32 lat_e7, int32 lon_e7, int32 alt_mm,
int16 north_yaw_cdeg) sau các tag. Không có gốc thì chuỗi y hệt 0.6 — bản đồ cũ giữ nguyên CRC.

Bản 0.8: mỗi tag `yaw_valid` nối thêm (uint16 tag_id, int16 yaw_cdeg) sau bản ghi gốc, sắp theo
tag_id. `yaw_deg` cũng là "từ trục N bản đồ, chiều kim đồng hồ" như trên dây; không tag nào có hướng
thì chuỗi y hệt 0.7.
"""
from __future__ import annotations

import struct
from zlib import crc32


def origin_ints(origin: dict) -> tuple[int, int, int, int]:
    """Gốc bản đồ -> số nguyên trên dây (lat_e7, lon_e7, alt_mm, north_yaw_cdeg), round() như tag."""
    return (round(origin["lat"] * 1e7), round(origin["lon"] * 1e7), round(origin["alt_m"] * 1000),
            round(origin["north_yaw_deg"] * 100))


def site_origin(site: dict | None) -> dict | None:
    """Site -> gốc WGS84 của bản đồ tag, hoặc None khi chưa khai lat/lon.

    `yaw_offset_deg` của Site = phương vị trục N bản đồ đo từ Bắc THẬT, chiều kim đồng hồ (0.7).
    Thiếu độ cao thì lấy 0: độ cao gốc chỉ ảnh hưởng z, mà Pi không fuse z của GPS.
    """
    if not site or site.get("origin_lat") is None or site.get("origin_lon") is None:
        return None
    return {"lat": float(site["origin_lat"]), "lon": float(site["origin_lon"]),
            "alt_m": float(site.get("origin_alt_m") or 0.0),
            "north_yaw_deg": float(site.get("yaw_offset_deg") or 0.0)}


def yaw_cdeg(deg: float) -> int:
    """Độ -> cdeg dạng CHUẨN TẮC -18000..17999: 180° phải ra -18000 thì CRC hai bên mới khớp (8.6)."""
    return (round(deg * 100) + 18000) % 36000 - 18000


def tagmap_crc(tags: list[dict], origin: dict | None = None) -> int:
    recs = sorted((int(t["tag_id"]), round(t["pos_n_m"] * 1000), round(t["pos_e_m"] * 1000),
                   round(t.get("pos_d_m", 0.0) * 1000)) for t in tags if t.get("enabled", True))
    buf = b"".join(struct.pack("<Hiii", *r) for r in recs)
    if origin is not None:
        buf += struct.pack("<iiih", *origin_ints(origin))
    yaws = sorted((int(t["tag_id"]), yaw_cdeg(t["yaw_deg"])) for t in tags
                  if t.get("enabled", True) and t.get("yaw_valid"))
    buf += b"".join(struct.pack("<Hh", *r) for r in yaws)
    return crc32(buf)
