"""Chuyển kế hoạch trong CSDL thành mục trên dây, và xuất `tags.yaml` cho Pi (giao ước 8.3, 8.6).

Việc nạp do `link_mav.mission_client` lo; ở đây chỉ là phép quy đổi, tách ra để kiểm bằng pytest
không cần socket.

**Bản đồ tag không còn nạp qua dây.** Giao ước chốt quy trình ngược lại: GCS **xuất `tags.yaml`** để
người triển khai chép sang Pi, còn trên dây chỉ so `tagmap_crc` (8.6). Lý do là `tags.yaml` là nguồn
vị trí duy nhất của drone khi không có GPS — nó phải nằm trong git của bên vận hành drone, không
phải xuất hiện từ một gói UDP.
"""
from __future__ import annotations

import re

from ..link_mav.dialect import drone_gcs as mav
from ..link_mav.mission_client import Waypoint, to_ascii
from ..link_mav.tagmap import yaw_cdeg

ACTION = {"none": mav.DRONE_ACTION_NONE, "pickup": mav.DRONE_ACTION_PICKUP,
          "dropoff": mav.DRONE_ACTION_DROPOFF}


def to_wire(waypoints: list[dict], tags: list[dict], max_vel_mps: float) -> list[Waypoint]:
    """Mục CSDL -> mục trên dây. `alt_m` đổi gốc: CSDL theo gốc bản đồ, trên dây theo TAG ĐÍCH (8.3)."""
    tag_d = {t["tag_id"]: t.get("pos_d_m", 0.0) for t in tags}
    out = []
    for w in waypoints:
        if w.get("tag_id") is None:
            raise ValueError(f"mục {w['seq']} không có tag_id — không diễn đạt được trên kênh này")
        if w["tag_id"] not in tag_d:
            raise ValueError(f"mục {w['seq']} trỏ tới tag {w['tag_id']} không còn trong bản đồ")
        out.append(Waypoint(
            expected_marker_id=int(w["tag_id"]),
            alt_m=round(tag_d[w["tag_id"]] - w["pos_d_m"], 3),
            acceptance_radius_m=w.get("accept_radius_m") or 0.3,
            max_vel_mps=min(w.get("max_vel_mps") or max_vel_mps, 1.9),  # trần của giao ước 8.3
            loiter_s=w.get("loiter_s") or 0.0,
            action=ACTION[(w.get("action") or "none").lower()]))
    return out


def frame_name(t: dict) -> str:
    """Tên khung TF của một tag. Tag home là `pad_home`, còn lại lấy theo nhãn: "A" -> `pad_a`."""
    if t["kind"] == "home":
        return "pad_home"
    slug = re.sub(r"[^a-z0-9]+", "_", to_ascii(t.get("label") or "", 32).decode().lower()).strip("_")
    return f"pad_{slug}" if slug else f"pad_tag{t['tag_id']}"


def tags_yaml(tags: list[dict], origin: dict | None = None) -> str:
    """`config/tags.yaml` của Pi — **file tham số ROS**, không phải YAML tự do.

    Ba chỗ bắt buộc phải đúng, sai một chỗ là ROS từ chối nạp hoặc nạp ra số vô nghĩa:

    - Phải có header `/**: ros__parameters:` — nhiều node cùng đọc file này.
    - `known_tags` là danh sách **phẳng** `[id, x, y, z, id, x, y, z, …]`, không phải danh sách lồng.
    - Mọi phần tử phải **cùng kiểu float**, kể cả `id`: danh sách trộn int với float làm ROS báo lỗi
      kiểu. Đó là lý do bản của Pi ghi `0.0` chứ không ghi `0`.

    Toạ độ theo **ENU mét**: `x = e`, `y = n`, `z = −d` (quy đổi ngược lại nằm ở `tagmap.py`).

    `origin` (0.7): gốc WGS84 của bản đồ — Pi dùng để đưa GPS vào khung bản đồ và quy vị trí ra
    lat/lon. None thì ghi `geo_origin_valid: false`.
    """
    rows = sorted((t for t in tags if t.get("enabled", True)), key=lambda t: t["tag_id"])
    lines = ["# Sinh từ trang thiết kế khu vực của GCS — nguồn vị trí duy nhất của drone khi không có",
             "# GPS (giao ước 8.6). Chép vào config/ của Pi rồi khởi động lại; trên dây hai bên chỉ so",
             "# tagmap_crc, và GCS khoá nạp kế hoạch khi lệch.",
             "#",
             "# tag_frames PHẢI khớp tag.ids / tag.frames trong apriltag.yaml phía Pi — GCS không biết",
             "# file đó nên tên dưới đây sinh từ nhãn tag, hãy đối chiếu trước khi dùng.",
             "/**:",
             "  ros__parameters:"]
    nums = []
    for t in rows:
        d = t.get("pos_d_m") or 0.0
        nums.append((t["tag_id"], t["pos_e_m"], t["pos_n_m"], -d if d else 0.0))
    if nums:
        body = [f"{tag_id:.1f}, {x:.3f}, {y:.3f}, {z:.3f}," for tag_id, x, y, z in nums]
        body[-1] = body[-1].rstrip(",")
        pad = " " * len("    known_tags: [")
        lines.append("    known_tags: [" + f"\n{pad}".join(body) + "]")
        lines.append("    tag_frames: [" + ", ".join(frame_name(t) for t in rows) + "]")
        # 0.8: hướng tag ĐÃ ĐO [id, độ, ...] — cùng quy ước trên dây (từ N bản đồ, chiều kim đồng hồ),
        # cùng phép làm tròn cdeg với tagmap_crc để Pi đọc lại ra đúng CRC.
        yaws = [f"{t['tag_id']:.1f}, {yaw_cdeg(t['yaw_deg']) / 100:.2f}" for t in rows if t.get("yaw_valid")]
        lines.append("    known_tags_heading: [" + ", ".join(yaws) + "]")
    else:
        lines.append("    known_tags: []")
        lines.append("    tag_frames: []")
    if origin is None:
        lines.append("    geo_origin_valid: false")
    else:
        lines += ["    geo_origin_valid: true",
                  f"    geo_origin_lat: {origin['lat']:.7f}",
                  f"    geo_origin_lon: {origin['lon']:.7f}",
                  f"    geo_origin_alt: {origin['alt_m']:.3f}",
                  f"    geo_north_yaw_deg: {origin['north_yaw_deg']:.2f}"]
    return "\n".join(lines) + "\n"
