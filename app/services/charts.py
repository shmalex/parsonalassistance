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


def _clip(text: str, n: int = 46) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def render_dashboard(d: dict) -> bytes:
    """Vertically auto-sizing dashboard: the figure grows with the amount of
    content, so everything fits and there's no big blank space.

    Layout is done in "row units" (≈ one text line); the figure height is set
    proportional to the total units, and a constant points font keeps spacing
    even regardless of how many items there are.
    """
    LM, RM = 0.06, 0.94
    UNIT_IN = 0.32          # inches per row unit
    per_ru = {"day": "сегодня", "week": "за неделю", "month": "за месяц"}

    # ── Pass 1: assemble ordered elements (units, kind, payload) ──────────────
    E: list[tuple[float, str, object]] = []
    E.append((1.3, "title", d.get("name", "")))
    E.append((1.15, "subtitle", (d.get("date_str", ""), d.get("time_str", ""))))

    E.append((1.05, "h2", ("ГЛАВНОЕ СЕГОДНЯ", AMBER)))
    if d.get("main_thing"):
        E.append((1.7, "mainbox", d["main_thing"]))
    else:
        E.append((0.95, "muted", "Назови ОДНУ главную вещь дня — и я прослежу."))

    E.append((1.15, "h2", ("ЦЕЛИ", ACCENT)))
    goals = d.get("goals") or []
    if not goals:
        E.append((0.95, "muted", "Целей пока нет — назови, к чему идёшь."))
    for g in goals[:5]:
        E.append((1.8, "goal", g))

    metrics = d.get("metrics") or []
    if metrics:
        E.append((1.15, "h2", ("МЕТРИКИ", ACCENT)))
        for m in metrics[:6]:
            if m.get("kind") == "gauge" or not m.get("target"):
                E.append((1.0, "metric_line", m))
            else:
                E.append((1.8, "metric_bar", m))

    E.append((1.15, "h2", ("ПРИВЫЧКИ СЕГОДНЯ", ACCENT)))
    habits = d.get("habits") or []
    if not habits:
        E.append((0.95, "muted", "Привычек нет — добавь спорт, чтение и т.п."))
    for h in habits[:8]:
        E.append((1.0, "habit", h))

    E.append((1.15, "h2", ("СЕГОДНЯ ВАЖНО", ACCENT)))
    tasks = d.get("tasks") or []
    if tasks:
        for t in tasks[:8]:
            E.append((0.95, "task", t))
    else:
        E.append((0.95, "muted", "Назови 1–3 приоритета на сегодня — и я прослежу."))

    overdue = d.get("overdue") or []
    if overdue:
        E.append((1.15, "h2", ("ХВОСТЫ — НЕ ЗАКРЫТО С ПРОШЛЫХ ДНЕЙ", RED)))
        for t in overdue[:12]:
            E.append((0.95, "overdue", t))

    if d.get("footer"):
        E.append((1.3, "footer", d["footer"]))

    total = sum(u for u, _, _ in E) + 0.6  # +top/bottom padding

    # ── Pass 2: render into an auto-sized figure ──────────────────────────────
    fig = plt.figure(figsize=(8, max(4.5, total * UNIT_IN)))
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, total)
    ax.axis("off")

    y = total - 0.3  # cursor = top of the current element's band
    for units, kind, payload in E:
        if kind == "title":
            ax.text(LM, y, f"Доброе утро, {payload}", color=WHITE,
                    fontsize=22, fontweight="bold", va="top")
        elif kind == "subtitle":
            ds, ts = payload
            ax.text(LM, y - 0.1, ds, color=MUTED, fontsize=12, va="top")
            ax.text(RM, y - 0.1, ts, color=MUTED, fontsize=12, va="top", ha="right")
            ax.plot([LM, RM], [y - 0.85, y - 0.85], color=TRACK, lw=1)
        elif kind == "h2":
            txt, col = payload
            ax.text(LM, y - 0.28, txt, color=col, fontsize=12,
                    fontweight="bold", va="top")
        elif kind == "muted":
            ax.text(LM, y - 0.15, payload, color=MUTED, fontsize=12, va="top")
        elif kind == "mainbox":
            ax.add_patch(Rectangle((LM, y - 1.5), RM - LM, 1.28, color="#1c2230"))
            ax.text(LM + 0.015, y - 0.86, _clip(payload, 52), color=WHITE,
                    fontsize=15, fontweight="bold", va="center")
        elif kind == "goal":
            g = payload
            ax.text(LM, y - 0.1, _clip(g["title"]), color=WHITE, fontsize=14, va="top")
            dl = g.get("days_left")
            if dl is not None:
                ax.text(RM, y - 0.1, (f"{dl} дн." if dl >= 0 else "просрочено"),
                        color=(RED if dl < 0 else MUTED), fontsize=12, va="top", ha="right")
            ax.add_patch(Rectangle((LM, y - 1.3), RM - LM, 0.34, color=TRACK))
            p = g.get("progress")
            if isinstance(p, (int, float)):
                frac = max(0, min(100, p)) / 100.0
                ax.add_patch(Rectangle((LM, y - 1.3), (RM - LM) * frac, 0.34, color=ACCENT))
                tot = g.get("total") or 0
                label = f"{int(p)}% · {g.get('done', 0)}/{tot} вех" if tot else f"{int(p)}%"
                ax.text(RM, y - 1.42, label, color=MUTED, fontsize=10,
                        va="top", ha="right")
            elif not g.get("total"):
                ax.text(RM, y - 1.42, "разбей на вехи", color=MUTED, fontsize=10,
                        va="top", ha="right")
        elif kind == "metric_line":
            m = payload
            unit = (" " + m["unit"]) if m.get("unit") else ""
            if m.get("kind") == "gauge":
                val = m.get("latest")
                txt = f"{m['title']}: {_n(val)}{unit}" if val is not None else f"{m['title']}: —"
            else:
                txt = (f"{m['title']}: {_n(m['period_sum'])}{unit} "
                       f"{per_ru.get(m['period'], '')}".rstrip())
            ax.text(LM, y - 0.1, _clip(txt, 52), color=WHITE, fontsize=14, va="top")
        elif kind == "metric_bar":
            m = payload
            unit = (" " + m["unit"]) if m.get("unit") else ""
            ax.text(LM, y - 0.1, _clip(m["title"]), color=WHITE, fontsize=14, va="top")
            ax.text(RM, y - 0.1, f"{_n(m['period_sum'])}/{_n(m['target'])}{unit}",
                    color=MUTED, fontsize=12, va="top", ha="right")
            ax.add_patch(Rectangle((LM, y - 1.3), RM - LM, 0.34, color=TRACK))
            frac = max(0.0, min(1.0, m["period_sum"] / m["target"])) if m.get("target") else 0
            ax.add_patch(Rectangle((LM, y - 1.3), (RM - LM) * frac, 0.34, color=GREEN))
            ax.text(RM, y - 1.42, f"{m.get('pct', 0)}% · прогноз ~{_n(m['projected'])}",
                    color=MUTED, fontsize=10, va="top", ha="right")
        elif kind == "habit":
            h = payload
            done = h.get("done")
            ax.text(LM, y - 0.12, "●" if done else "○",
                    color=(GREEN if done else MUTED), fontsize=13, va="top")
            label = h["title"]
            meta = []
            if h.get("target_minutes"):
                meta.append(f"{h['target_minutes']} мин")
            if h.get("schedule_time"):
                meta.append(h["schedule_time"])
            if meta:
                label += f"  ({', '.join(meta)})"
            ax.text(LM + 0.035, y - 0.12, _clip(label, 50),
                    color=(MUTED if done else WHITE), fontsize=13, va="top")
        elif kind == "task":
            t = payload
            done = t.get("status") == "done"
            mark = "✓" if done else "•"
            ax.text(LM, y - 0.1, f"{mark}  {_clip(t['title'])}",
                    color=(MUTED if done else WHITE), fontsize=13, va="top")
        elif kind == "overdue":
            t = payload
            ax.text(LM, y - 0.1, f"• {_clip(t['title'], 40)}", color=WHITE,
                    fontsize=13, va="top")
            ax.text(RM, y - 0.1, f"от {t.get('date', '')}", color=RED,
                    fontsize=11, va="top", ha="right")
        elif kind == "footer":
            ax.text(LM, y - 0.15, payload, color=MUTED, fontsize=11,
                    va="top", style="italic")
        y -= units

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
