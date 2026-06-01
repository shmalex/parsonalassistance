"""Learn the user's real active window from when they actually talk to the bot.

Pure logic here; the DB query lives in repository.user_message_hours().
"""
from __future__ import annotations


def suggest_window(hour_counts: list[int], coverage: float = 0.85) -> tuple[int, int] | None:
    """Given 24 per-hour message counts (local time), return (start_hour,
    end_hour) — the tightest circular window covering >= `coverage` of activity.
    end_hour is the LAST active hour (inclusive). None if there is no data.
    """
    total = sum(hour_counts)
    if total <= 0:
        return None
    need = coverage * total
    best: tuple[int, int, int] | None = None  # (start, last, width)
    for start in range(24):
        cum = 0
        for width in range(1, 25):
            last = (start + width - 1) % 24
            cum += hour_counts[last]
            if cum >= need:
                if best is None or width < best[2]:
                    best = (start, last, width)
                break
    if best is None:
        return None
    return best[0], best[1]


def in_window(hour: int, start: int, end: int) -> bool:
    """Is `hour` inside [start, end] on a 24h circle (end may wrap past midnight)?"""
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def coverage_inside(hour_counts: list[int], start: int, end: int) -> float:
    """Fraction of activity that falls inside the [start, end] window."""
    total = sum(hour_counts)
    if total <= 0:
        return 1.0
    inside = sum(c for h, c in enumerate(hour_counts) if in_window(h, start, end))
    return inside / total


def fmt_window(start: int, end: int) -> str:
    return f"{start:02d}:00–{end:02d}:59"
