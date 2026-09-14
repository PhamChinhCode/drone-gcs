"""Hình học phẳng trên mặt (N, E), đơn vị mét."""
from __future__ import annotations

import math

Pt = tuple[float, float] | list[float]


def dist(a: Pt, b: Pt) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_in_polygon(p: Pt, poly: list[Pt]) -> bool:
    """Ray casting; đa giác tự khép đỉnh cuối → đỉnh đầu."""
    n, e = p
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        ni, ei = poly[i]
        nj, ej = poly[j]
        if (ei > e) != (ej > e):
            n_cross = (nj - ni) * (e - ei) / (ej - ei) + ni
            if n < n_cross:
                inside = not inside
        j = i
    return inside


def dist_point_segment(p: Pt, a: Pt, b: Pt) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return dist(p, (ax + t * dx, ay + t * dy))


def dist_to_boundary(p: Pt, poly: list[Pt]) -> float:
    return min(dist_point_segment(p, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))


def _orient(a: Pt, b: Pt, c: Pt) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> bool:
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True

    def on_seg(a: Pt, b: Pt, c: Pt) -> bool:
        return (abs(_orient(a, b, c)) < 1e-9 and min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9
                and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9)
    return on_seg(q1, q2, p1) or on_seg(q1, q2, p2) or on_seg(p1, p2, q1) or on_seg(p1, p2, q2)


def is_simple_polygon(poly: list[Pt]) -> bool:
    """True nếu đa giác ≥ 3 đỉnh, không cạnh nào cắt nhau (trừ hai cạnh kề chung đỉnh)."""
    n = len(poly)
    if n < 3:
        return False
    if abs(polygon_area(poly)) < 1e-9:
        return False
    edges = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_cross(*edges[i], *edges[j]):
                return False
    return True


def polygon_area(poly: list[Pt]) -> float:
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                     for i in range(len(poly)))
