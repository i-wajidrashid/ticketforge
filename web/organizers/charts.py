"""Server-side geometry for the dashboard's sales chart.

The chart is plain SVG rendered by the template: it needs no JavaScript to be
readable, prints cleanly, and the same numbers are offered as a table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

WIDTH = 640
HEIGHT = 220
PAD_LEFT = 56
PAD_RIGHT = 8
PAD_TOP = 12
PAD_BOTTOM = 34
BAR_GAP = 2  # px of surface between adjacent bars
MAX_DAYS = 30


@dataclass(frozen=True, slots=True)
class Bar:
    day: date
    value: int
    orders: int
    x: float
    y: float
    width: float
    height: float
    center: float
    path: str  # bar with 4px rounded data end, anchored to the baseline
    show_label: bool


def _nice_step(maximum: int, ticks: int = 3) -> int:
    """A round axis step (1, 2 or 5 times a power of ten) covering ``maximum``."""
    raw = max(1, maximum) / ticks
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 5, 10):
        if raw <= factor * magnitude:
            return int(factor * magnitude)
    return int(10 * magnitude)  # pragma: no cover - the loop always returns


def _bar_path(x: float, y: float, width: float, height: float) -> str:
    radius = min(4.0, width / 2, height)
    bottom = y + height
    return (
        f"M{x:.1f},{bottom:.1f}V{y + radius:.1f}"
        f"Q{x:.1f},{y:.1f} {x + radius:.1f},{y:.1f}"
        f"H{x + width - radius:.1f}"
        f"Q{x + width:.1f},{y:.1f} {x + width:.1f},{y + radius:.1f}"
        f"V{bottom:.1f}Z"
    )


def _show_label(index: int, count: int, every: int) -> bool:
    """Label every ``every``-th day and always the last one (today), dropping
    a periodic label that would sit close enough to today's to collide."""
    last = count - 1
    if index == last:
        return True
    return index % every == 0 and last - index >= every / 2


def daily_revenue_chart(sales_by_day: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """Revenue per day from the first sale to today (capped at 30 days)."""
    if not sales_by_day:
        return None
    by_day = {date.fromisoformat(str(row["day"])): row for row in sales_by_day}
    start = max(min(by_day), today - timedelta(days=MAX_DAYS - 1))
    end = max(today, max(by_day))
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    days = days[-MAX_DAYS:]

    values = [int(by_day.get(day, {}).get("revenue_cents", 0)) for day in days]
    step = _nice_step(max(values))
    top = step * max(1, math.ceil(max(values) / step))

    plot_w = WIDTH - PAD_LEFT - PAD_RIGHT
    plot_h = HEIGHT - PAD_TOP - PAD_BOTTOM
    slot = plot_w / len(days)
    bar_w = max(2.0, min(28.0, slot - BAR_GAP))
    label_every = max(1, math.ceil(len(days) / 6))

    bars = []
    for index, (day, value) in enumerate(zip(days, values, strict=True)):
        height = plot_h * value / top if top else 0
        x = PAD_LEFT + index * slot + (slot - bar_w) / 2
        y = PAD_TOP + plot_h - height
        bars.append(
            Bar(
                day=day,
                value=value,
                orders=int(by_day.get(day, {}).get("orders", 0)),
                x=x,
                y=y,
                width=bar_w,
                height=height,
                center=x + bar_w / 2,
                path=_bar_path(x, y, bar_w, height) if value else "",
                show_label=_show_label(index, len(days), label_every),
            )
        )

    gridlines = [
        {"value": tick, "y": PAD_TOP + plot_h - plot_h * tick / top}
        for tick in range(0, top + 1, step)
    ]
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "plot_left": PAD_LEFT,
        "plot_right": WIDTH - PAD_RIGHT,
        "baseline": PAD_TOP + plot_h,
        "slot": slot,
        "bars": bars,
        "gridlines": gridlines,
        "label_y": HEIGHT - 6,
        "total_cents": sum(values),
    }
