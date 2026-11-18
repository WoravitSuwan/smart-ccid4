"""Server-drawn SVG charts. Geometry here; colour only from design tokens in CSS (.chart-bar uses var(--primary))."""

from __future__ import annotations

import math


def nice_max(v: float) -> float:
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for step in (1, 2, 2.5, 5, 10):
        if v <= step * exp:
            return step * exp
    return 10 * exp


def compact(n: float) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n / 1000:.0f}K"
    return f"{n:,.0f}"


def columns(points: list[tuple[str, float]], width=640, height=220) -> dict:
    """Single-series column chart: bars <= 24px, 4px rounded top, square at the baseline, one hairline baseline."""
    left, right, top, bottom = 40, 8, 22, 28
    plot_w, plot_h = width - left - right, height - top - bottom
    vmax = nice_max(max((v for _, v in points), default=0))
    band = plot_w / max(len(points), 1)
    bw = min(24, band * 0.6)
    peak = max(range(len(points)), key=lambda i: points[i][1]) if points else -1
    bars = []
    for i, (label, v) in enumerate(points):
        h = 0 if not vmax else plot_h * v / vmax
        x = left + i * band + (band - bw) / 2
        y = top + plot_h - h
        r = min(4, h / 2, bw / 2)
        path = (f"M{x:.1f},{top + plot_h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bw - r:.1f} "
                f"Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{top + plot_h:.1f} Z") if h > 0 else ""  # fmt: skip
        bars.append({"label": label, "value": v, "path": path, "cx": x + bw / 2, "top": y, "hit_x": left + i * band, "hit_w": band,
                     "show_value": i == peak and v > 0, "text": compact(v)})  # fmt: skip
    ticks = [{"y": top + plot_h - plot_h * f, "text": compact(vmax * f)} for f in (0, 0.5, 1)]
    return {"w": width, "h": height, "bars": bars, "ticks": ticks, "base_y": top + plot_h, "left": left, "right": width - right,
            "label_y": height - 8, "plot_top": top, "hit_h": plot_h + bottom}  # fmt: skip


def hbars(points: list[tuple[str, float]], width=640, row=30) -> dict:
    """Single-series horizontal bars, value at the tip (text token), label on the left."""
    label_w, right = 230, 56
    plot_w = width - label_w - right
    vmax = max((v for _, v in points), default=0) or 1
    rows = []
    for i, (label, v) in enumerate(points):
        w = plot_w * v / vmax
        y = 6 + i * row
        bh = min(18, row - 10)
        r = min(4, w / 2, bh / 2)
        x0 = label_w
        path = (f"M{x0},{y:.1f} H{x0 + w - r:.1f} Q{x0 + w:.1f},{y:.1f} {x0 + w:.1f},{y + r:.1f} V{y + bh - r:.1f} "
                f"Q{x0 + w:.1f},{y + bh:.1f} {x0 + w - r:.1f},{y + bh:.1f} H{x0} Z") if w > 0 else ""  # fmt: skip
        short = label if len(label) <= 32 else label[:31] + "…"
        rows.append(
            {"label": label, "short": short, "value": v, "path": path, "y": y, "mid": y + bh / 2, "tip": x0 + w + 6, "bh": bh}
        )
    return {"w": width, "h": 12 + len(points) * row, "rows": rows, "label_w": label_w}
