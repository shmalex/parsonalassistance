"""Image renderers: morning dashboard, habit streak heatmap, countdown calendar.

Rendered locally with matplotlib (Agg) → PNG bytes, sent via bot.send_photo.
Data-accurate by construction (no AI image generation). Avoids emoji in text so
no missing-glyph boxes; uses DejaVu Sans which covers Cyrillic.
"""
from __future__ import annotations

import calendar as _cal
import datetime as dt
import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle, Rectangle  # noqa: E402

plt.rcParams["font.family"] = "DejaVu Sans"

# Palette (dark card).
BG = "#0b1220"
WHITE = "#e8edf5"
MUTED = "#9aa7bd"
ACCENT = "#38bdf8"   # cyan
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED = "#ef4444"
TRACK = "#243045"
GRID_EMPTY = "#0f1830"
GRID_BORDER = "#1b2740"

RU_MONTHS = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


RU_WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _n(x) -> str:
    try:
        f = float(x)
    except Exception:  # noqa: BLE001
        return str(x)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _placeholder(text: str) -> bytes:
    fig = plt.figure(figsize=(8, 4))
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.5, 0.5, text, color=MUTED, fontsize=16, ha="center", va="center")
    return _png(fig)


def render_dashboard(d: dict) -> bytes:
    """d: {name, date_str, time_str, goals:[{title,progress,days_left}],
           habits:[{title,target_minutes,schedule_time,done}],
           tasks:[{title,status}], footer}"""
    fig = plt.figure(figsize=(8, 10))
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    LM, RM = 0.06, 0.94

    y = 0.96
    ax.text(LM, y, f"Доброе утро, {d.get('name','')}", color=WHITE,
            fontsize=24, fontweight="bold", va="top")
    y -= 0.045
    ax.text(LM, y, d.get("date_str", ""), color=MUTED, fontsize=13, va="top")
    ax.text(RM, y, d.get("time_str", ""), color=MUTED, fontsize=13, va="top", ha="right")
    y -= 0.028
    ax.plot([LM, RM], [y, y], color=TRACK, lw=1)
    y -= 0.038

    # The ONE main thing for today — the most prominent block.
    main_thing = d.get("main_thing")
    ax.text(LM, y, "ГЛАВНОЕ СЕГОДНЯ", color=AMBER, fontsize=12,
            fontweight="bold", va="top")
    y -= 0.04
    if main_thing:
        ax.add_patch(Rectangle((LM, y - 0.052), RM - LM, 0.06, color="#1c2230"))
        ax.text(LM + 0.015, y - 0.022, main_thing, color=WHITE, fontsize=16,
                fontweight="bold", va="center")
        y -= 0.085
    else:
        ax.text(LM, y, "Назови ОДНУ главную вещь дня — и я прослежу.",
                color=MUTED, fontsize=12, va="top")
        y -= 0.05

    # Goals with progress + days left.
    ax.text(LM, y, "ЦЕЛИ", color=ACCENT, fontsize=12, fontweight="bold", va="top")
    y -= 0.036
    goals = d.get("goals") or []
    if not goals:
        ax.text(LM, y, "Целей пока нет — назови, к чему идёшь.", color=MUTED,
                fontsize=12, va="top")
        y -= 0.04
    for g in goals[:4]:
        ax.text(LM, y, g["title"], color=WHITE, fontsize=14, va="top")
        dl = g.get("days_left")
        if dl is not None:
            txt = f"{dl} дн." if dl >= 0 else "просрочено"
            ax.text(RM, y, txt, color=(RED if dl < 0 else MUTED),
                    fontsize=12, va="top", ha="right")
        y -= 0.03
        bh = 0.018
        ax.add_patch(Rectangle((LM, y - bh), RM - LM, bh, color=TRACK))
        p = g.get("progress")
        if isinstance(p, (int, float)):
            frac = max(0, min(100, p)) / 100.0
            ax.add_patch(Rectangle((LM, y - bh), (RM - LM) * frac, bh, color=ACCENT))
            ax.text(RM, y - bh - 0.006, f"{int(p)}%", color=MUTED,
                    fontsize=10, va="top", ha="right")
        y -= 0.052

    # Metrics with progress toward period targets.
    metrics = d.get("metrics") or []
    if metrics:
        y -= 0.012
        ax.text(LM, y, "МЕТРИКИ", color=ACCENT, fontsize=12, fontweight="bold", va="top")
        y -= 0.036
        per_ru = {"day": "сегодня", "week": "за неделю", "month": "за месяц"}
        for m in metrics[:5]:
            unit = (" " + m["unit"]) if m.get("unit") else ""
            if m.get("kind") == "gauge":
                val = m.get("latest")
                txt = f"{m['title']}: {_n(val)}{unit}" if val is not None else f"{m['title']}: —"
                ax.text(LM, y, txt, color=WHITE, fontsize=14, va="top")
                y -= 0.038
                continue
            ax.text(LM, y, m["title"], color=WHITE, fontsize=14, va="top")
            if m.get("target"):
                ax.text(RM, y, f"{_n(m['period_sum'])}/{_n(m['target'])}{unit}",
                        color=MUTED, fontsize=12, va="top", ha="right")
                y -= 0.03
                bh = 0.018
                ax.add_patch(Rectangle((LM, y - bh), RM - LM, bh, color=TRACK))
                frac = max(0.0, min(1.0, m["period_sum"] / m["target"])) if m["target"] else 0
                ax.add_patch(Rectangle((LM, y - bh), (RM - LM) * frac, bh, color=GREEN))
                ax.text(RM, y - bh - 0.006,
                        f"{m.get('pct', 0)}% · прогноз ~{_n(m['projected'])}",
                        color=MUTED, fontsize=10, va="top", ha="right")
                y -= 0.052
            else:
                ax.text(RM, y, f"{_n(m['period_sum'])}{unit} {per_ru.get(m['period'], '')}",
                        color=MUTED, fontsize=12, va="top", ha="right")
                y -= 0.04

    y -= 0.012
    # Habits today.
    ax.text(LM, y, "ПРИВЫЧКИ СЕГОДНЯ", color=ACCENT, fontsize=12,
            fontweight="bold", va="top")
    y -= 0.036
    habits = d.get("habits") or []
    if not habits:
        ax.text(LM, y, "Привычек нет — добавь спорт, чтение и т.п.", color=MUTED,
                fontsize=12, va="top")
        y -= 0.04
    for h in habits[:6]:
        sq = 0.024
        col = GREEN if h.get("done") else TRACK
        ax.add_patch(Rectangle((LM, y - sq), sq, sq, color=col, ec="#3b4a63", lw=1))
        if h.get("done"):
            cx, cy = LM + sq / 2, y - sq / 2
            ax.plot([cx - 0.007, cx - 0.001, cx + 0.009],
                    [cy, cy - 0.006, cy + 0.008], color="#06240f", lw=2)
        label = h["title"]
        meta = []
        if h.get("target_minutes"):
            meta.append(f"{h['target_minutes']} мин")
        if h.get("schedule_time"):
            meta.append(h["schedule_time"])
        if meta:
            label += f"  ({', '.join(meta)})"
        ax.text(LM + sq + 0.02, y - sq + 0.002, label,
                color=(MUTED if h.get("done") else WHITE), fontsize=13, va="bottom")
        y -= 0.042

    y -= 0.012
    # Today's priorities.
    ax.text(LM, y, "СЕГОДНЯ ВАЖНО", color=ACCENT, fontsize=12,
            fontweight="bold", va="top")
    y -= 0.036
    tasks = d.get("tasks") or []
    if tasks:
        for t in tasks[:5]:
            done = t.get("status") == "done"
            mark = "✓" if done else "•"
            ax.text(LM, y, f"{mark}  {t['title']}",
                    color=(MUTED if done else WHITE), fontsize=13, va="top")
            y -= 0.034
    else:
        ax.text(LM, y, "Назови 1–3 приоритета на сегодня — и я прослежу.",
                color=MUTED, fontsize=12, va="top")
        y -= 0.034

    # Carried-over unfinished tasks from earlier days — honest backlog.
    overdue = d.get("overdue") or []
    if overdue:
        y -= 0.014
        ax.text(LM, y, "ХВОСТЫ — НЕ ЗАКРЫТО С ПРОШЛЫХ ДНЕЙ", color=RED,
                fontsize=12, fontweight="bold", va="top")
        y -= 0.036
        for t in overdue[:6]:
            ax.text(LM, y, f"• {t['title']}", color=WHITE, fontsize=13, va="top")
            ax.text(RM, y, f"от {t.get('date', '')}", color=RED, fontsize=11,
                    va="top", ha="right")
            y -= 0.034

    footer = d.get("footer")
    if footer:
        ax.text(LM, 0.035, footer, color=MUTED, fontsize=11, va="bottom", style="italic")
    return _png(fig)


def render_streak(d: dict) -> bytes:
    """d: {today: date, habits: [{title, done_dates: set[date]}]}"""
    habits = d.get("habits") or []
    today = d["today"]
    if not habits:
        return _placeholder("Пока нет привычек для статистики")

    n_weeks = 18
    start = today - dt.timedelta(days=today.weekday()) \
        - dt.timedelta(weeks=n_weeks - 1)
    n = len(habits)
    fig, axes = plt.subplots(n, 1, figsize=(8, 1.25 * n + 1.0))
    fig.patch.set_facecolor(BG)
    if n == 1:
        axes = [axes]
    fig.suptitle("Серии привычек (последние ~4 месяца)", color=WHITE,
                 fontsize=15, fontweight="bold", x=0.06, ha="left")

    for ax, h in zip(axes, habits):
        ax.set_facecolor(BG)
        ax.axis("off")
        ax.set_title(h["title"], color=WHITE, fontsize=13, loc="left", pad=4)
        done = h.get("done_dates") or set()
        for w in range(n_weeks):
            for dow in range(7):
                day = start + dt.timedelta(weeks=w, days=dow)
                x, yy = w, 6 - dow
                if day > today:
                    c, ec = GRID_EMPTY, GRID_BORDER
                elif day in done:
                    c, ec = GREEN, GREEN
                else:
                    c, ec = TRACK, TRACK
                ax.add_patch(Rectangle((x, yy), 0.88, 0.88, color=c, ec=ec, lw=0.5))
        ax.set_xlim(-0.5, n_weeks + 0.5)
        ax.set_ylim(-0.5, 7.5)
        ax.set_aspect("equal")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return _png(fig)


def render_countdown_calendar(d: dict) -> bytes:
    """d: {today: date, target_date: date|None, label: str}"""
    today = d["today"]
    target = d.get("target_date")
    label = d.get("label", "")

    # Show current month through the target's month (so the deadline is visible),
    # at least 4 and at most 6 months.
    n_months = 4
    if target:
        span = (target.year * 12 + target.month) - (today.year * 12 + today.month) + 1
        n_months = min(6, max(4, span))
    rows = math.ceil(n_months / 2)

    fig = plt.figure(figsize=(8, 2.55 * rows + 1.4))
    fig.patch.set_facecolor(BG)
    head = fig.add_axes([0, 0.9, 1, 0.1])
    head.axis("off")
    if target:
        dl = (target - today).days
        head.text(0.5, 0.55, f"До «{label}»: {dl} дн.", color=WHITE,
                  fontsize=20, fontweight="bold", ha="center", va="center")
        head.text(0.5, 0.12, f"дедлайн {target.isoformat()}", color=MUTED,
                  fontsize=12, ha="center", va="center")
    else:
        head.text(0.5, 0.5, "У цели нет дедлайна — задай дату, и появится отсчёт",
                  color=MUTED, fontsize=15, ha="center", va="center")

    _cal.setfirstweekday(0)  # Monday
    months = []
    yy, mm = today.year, today.month
    for _ in range(n_months):
        months.append((yy, mm))
        mm += 1
        if mm > 12:
            mm, yy = 1, yy + 1

    top, bottom = 0.86, 0.04
    row_h = (top - bottom) / rows
    for i, (year, month) in enumerate(months):
        col, row = i % 2, i // 2
        pos = [0.05 + 0.48 * col, top - row_h * (row + 1) + 0.015,
               0.42, row_h - 0.04]
        ax = fig.add_axes(pos)
        ax.axis("off")
        ax.set_xlim(0, 7)
        ax.set_ylim(0, 7)
        ax.text(0.1, 6.55, f"{RU_MONTHS[month]} {year}", color=WHITE,
                fontsize=13, fontweight="bold", va="center")
        for r, week in enumerate(_cal.monthcalendar(year, month)):
            for c, dayn in enumerate(week):
                if dayn == 0:
                    continue
                day = dt.date(year, month, dayn)
                cx, cy = c + 0.5, 5.4 - r
                if target and day == target:
                    ax.add_patch(Circle((cx, cy), 0.42, color=AMBER))
                    ax.text(cx, cy, str(dayn), color="#1a1205", fontsize=9,
                            ha="center", va="center", fontweight="bold")
                elif day == today:
                    ax.add_patch(Circle((cx, cy), 0.42, fill=False, ec=ACCENT, lw=2))
                    ax.text(cx, cy, str(dayn), color=ACCENT, fontsize=9,
                            ha="center", va="center", fontweight="bold")
                elif day < today:
                    ax.text(cx, cy, str(dayn), color="#3b4a63", fontsize=9,
                            ha="center", va="center")
                    ax.plot([cx - 0.32, cx + 0.32], [cy - 0.3, cy + 0.3],
                            color="#3b4a63", lw=0.8)
                else:
                    ax.text(cx, cy, str(dayn), color=WHITE, fontsize=9,
                            ha="center", va="center")
    return _png(fig)


def render_rhythm(d: dict) -> bytes:
    """d: {counts:[24], total, days, suggested:(s,e)|None, current:(s,e)|None}"""
    from app.rhythm import in_window

    counts = d["counts"]
    sug = d.get("suggested")
    cur = d.get("current")
    hours = list(range(24))

    fig = plt.figure(figsize=(9, 4.6))
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0.08, 0.18, 0.88, 0.62])
    ax.set_facecolor(BG)

    colors = []
    for h in hours:
        if counts[h] <= 0:
            colors.append(TRACK)
        elif sug and in_window(h, sug[0], sug[1]):
            colors.append(GREEN)
        else:
            colors.append(ACCENT)
    ax.bar(hours, counts, color=colors, width=0.85)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xticklabels([f"{h:02d}" for h in range(0, 24, 2)], color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED)
    for sp in ax.spines.values():
        sp.set_color(GRID_BORDER)
    ax.set_xlim(-0.6, 23.6)

    if cur:
        ax.axvline(cur[0] - 0.5, color=AMBER, ls="--", lw=1)
        ax.axvline((cur[1] + 1) % 24 - 0.5, color=AMBER, ls="--", lw=1)

    fig.suptitle("Когда ты активен (по твоим сообщениям, NY)", color=WHITE,
                 fontsize=15, fontweight="bold", x=0.08, ha="left")
    sub = []
    if cur:
        sub.append(f"сейчас: {cur[0]:02d}:00–{cur[1]:02d}:59 (- -)")
    if sug:
        sub.append(f"предлагаю: {sug[0]:02d}:00–{sug[1]:02d}:59 (зелёным)")
    sub.append(f"данных: {d.get('total', 0)} сообщ. за {d.get('days', 0)} дн.")
    ax.set_title("   ·   ".join(sub), color=MUTED, fontsize=10, loc="left", pad=8)
    return _png(fig)


def render_report(d: dict) -> bytes:
    """d: {title, metrics: [{title, unit, days:[7], labels:[7], target, period_sum,
           projected, pct, daily_target}]}"""
    metrics = d.get("metrics") or []
    if not metrics:
        return _placeholder("Пока нет метрик. Скажи: «цель 1000 приседаний в неделю».")

    n = len(metrics)
    fig, axes = plt.subplots(n, 1, figsize=(8, 2.7 * n + 1.0))
    fig.patch.set_facecolor(BG)
    if n == 1:
        axes = [axes]
    fig.suptitle(d.get("title", "Отчёт за неделю"), color=WHITE, fontsize=16,
                 fontweight="bold", x=0.06, ha="left")

    for ax, m in zip(axes, metrics):
        ax.set_facecolor(BG)
        days = m["days"]
        xs = list(range(len(days)))
        bars = ax.bar(xs, days, color=ACCENT, width=0.6)
        if m.get("daily_target"):
            ax.axhline(m["daily_target"], color=AMBER, ls="--", lw=1)
        ax.set_xticks(xs)
        ax.set_xticklabels(m["labels"], color=MUTED, fontsize=9)
        ax.tick_params(colors=MUTED)
        for sp in ax.spines.values():
            sp.set_color(GRID_BORDER)
        unit = (" " + m["unit"]) if m.get("unit") else ""
        if m.get("target"):
            title = (f"{m['title']}: {_n(m['period_sum'])}/{_n(m['target'])}{unit} "
                     f"({m.get('pct', 0)}%) · прогноз ~{_n(m['projected'])}")
        else:
            title = f"{m['title']}: {_n(m['period_sum'])}{unit} за период"
        ax.set_title(title, color=WHITE, fontsize=12, loc="left", pad=6)
        top = max(days + [m.get("daily_target") or 0, 1])
        ax.set_ylim(0, top * 1.25)
        for b, v in zip(bars, days):
            if v:
                ax.text(b.get_x() + b.get_width() / 2, v, _n(v),
                        color=WHITE, fontsize=8, ha="center", va="bottom")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return _png(fig)
