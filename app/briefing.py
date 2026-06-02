"""Assembles the data dicts that feed the chart renderers. Shared by the chat
commands and the scheduled morning briefing.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import repository as repo
from app import rhythm

RU_WEEKDAYS = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]
RU_WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _local_now(tz_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001
        return datetime.utcnow()


async def gather_dashboard_data(session, user) -> dict:
    now = _local_now(user.timezone)
    today = now.date()
    goals = await repo.list_active_goals(session, user.id)
    habits = await repo.list_active_habits(session, user.id)
    logs = await repo.habit_logs_for_day(session, user.id, today)
    tasks = await repo.tasks_for_day(session, user.id, today)
    overdue = await repo.open_tasks_before(session, user.id, today)
    day_log = await repo.get_day_log(session, user.id, today)
    metrics = await repo.list_active_metrics(session, user.id)
    metric_rows = [
        await repo.metric_progress(session, m, today, user.timezone)
        for m in metrics
    ]

    goal_rows = []
    for g in goals[:4]:
        days_left = (g.target_date - today).days if g.target_date else None
        pct, done, total = await repo.goal_progress_pct(session, g)
        goal_rows.append({
            "title": g.title, "progress": pct, "days_left": days_left,
            "done": done, "total": total,
        })
    habit_rows = [
        {
            "title": h.title,
            "target_minutes": h.target_minutes,
            "schedule_time": h.schedule_time,
            "done": bool(logs.get(h.id) and logs[h.id].status == "done"),
        }
        for h in habits[:6]
    ]
    # Show today's open + done tasks; skipped ones are dismissed, don't clutter.
    task_rows = [
        {"title": t.title, "status": t.status}
        for t in tasks if t.status != "skipped"
    ][:8]
    overdue_rows = [
        {"title": t.title, "date": t.plan_date.strftime("%d.%m")} for t in overdue[:6]
    ]

    return {
        "name": user.first_name or "",
        "date_str": f"{RU_WEEKDAYS[now.weekday()]}, {now:%d.%m.%Y}",
        "time_str": now.strftime("%H:%M"),
        "main_thing": day_log.main_thing if day_log else None,
        "goals": goal_rows,
        "habits": habit_rows,
        "metrics": metric_rows,
        "tasks": task_rows,
        "overdue": overdue_rows,
        "footer": "Один шаг за раз. Начни с самого важного.",
    }


async def gather_report_data(session, user) -> dict:
    """Last-7-days data per counter metric, for the report chart."""
    today = _local_now(user.timezone).date()
    start = today - timedelta(days=6)
    metrics = await repo.list_active_metrics(session, user.id)
    rows = []
    for m in metrics:
        if m.kind != "counter":
            continue
        days = await repo.metric_daily_sums(session, m.id, start, today, user.timezone)
        labels = [RU_WD_SHORT[(start + timedelta(days=i)).weekday()] for i in range(7)]
        prog = await repo.metric_progress(session, m, today, user.timezone)
        daily_target = (m.target / prog["days_total"]) if m.target else None
        rows.append({
            "title": m.title, "unit": m.unit or "",
            "days": days, "labels": labels,
            "target": m.target, "period_sum": prog["period_sum"],
            "projected": prog["projected"], "pct": prog.get("pct") or 0,
            "daily_target": daily_target,
        })
    return {"today": today, "title": "Отчёт за неделю", "metrics": rows}


async def gather_streak_data(session, user) -> dict:
    today = _local_now(user.timezone).date()
    since = today - timedelta(days=132)
    habits = await repo.list_active_habits(session, user.id)
    rows = []
    for h in habits:
        dates = await repo.habit_done_dates(session, user.id, h.id, since)
        rows.append({"title": h.title, "done_dates": set(dates)})
    return {"today": today, "habits": rows}


def _hour_of(hhmm: str, fallback: int) -> int:
    try:
        return int(hhmm.split(":")[0])
    except Exception:  # noqa: BLE001
        return fallback


async def gather_rhythm_data(session, user) -> dict:
    counts, total, days = await repo.user_message_hours(session, user.id, user.timezone, 14)
    return {
        "counts": counts,
        "total": total,
        "days": days,
        "suggested": rhythm.suggest_window(counts),
        "current": (_hour_of(user.active_from, 9), _hour_of(user.active_to, 22)),
        "tz": user.timezone,
    }


async def gather_calendar_data(session, user) -> dict:
    today = _local_now(user.timezone).date()
    goals = await repo.list_active_goals(session, user.id)
    dated = sorted([g for g in goals if g.target_date], key=lambda g: g.target_date)
    if dated:
        g = dated[0]
        return {"today": today, "target_date": g.target_date, "label": g.title}
    return {
        "today": today,
        "target_date": None,
        "label": goals[0].title if goals else "",
    }
