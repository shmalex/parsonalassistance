"""Weekly self-analysis ("coach over coach").

Gathers evidence about how the week went for a user (check-in response rate,
habit adherence, metric progress, day outcomes + blockers, tone of replies),
then asks the LLM to update the per-user *playbook* and propose next week's theme.
Output is PROPOSED — applied only on the user's confirmation (nanny: verify).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import repository as repo
from app.services import openai_service


def _today(tz_name: str):
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:  # noqa: BLE001
        return datetime.utcnow().date()


async def gather_week_evidence(session, user) -> tuple[str, dict, object, object]:
    tz = user.timezone
    today = _today(tz)
    start = today - timedelta(days=6)  # rolling 7-day window (useful any day)
    days_elapsed = 7

    prof = await repo.get_or_create_profile(session, user.id)
    asked, answered = await repo.checkin_counts_between(session, user.id, start, today, tz)
    habits = await repo.list_active_habits(session, user.id)
    metrics = await repo.list_active_metrics(session, user.id)
    day_logs = await repo.day_logs_between(session, user.id, start, today)
    recent = await repo.recent_user_message_texts(session, user.id, 12)

    lines: list[str] = []
    lines.append(f"Период: {start.isoformat()} — {today.isoformat()} ({days_elapsed} дн.)")
    lines.append(f"Тема недели: {prof.week_theme or 'не задана'}")
    lines.append("Текущий плейбук:\n" + (prof.playbook or "(пусто)"))
    lines.append(f"Отклик на проверки: отвечено {answered} из {asked}.")

    habit_stats = {}
    if habits:
        hl = ["Привычки за неделю:"]
        for h in habits:
            dates = await repo.habit_done_dates(session, user.id, h.id, start)
            cnt = len([d for d in dates if d <= today])
            habit_stats[h.title] = cnt
            when = f", цель в {h.schedule_time}" if h.schedule_time else ""
            hl.append(f"- {h.title}: {cnt} из {days_elapsed} дн.{when}")
        lines.append("\n".join(hl))

    metric_stats = {}
    if metrics:
        ml = ["Метрики:"]
        for m in metrics:
            pr = await repo.metric_progress(session, m, today, tz)
            if pr.get("kind") == "gauge":
                ml.append(f"- {m.title}: {pr.get('latest')}")
            else:
                tgt = f"/{pr.get('target')}" if pr.get("target") else ""
                metric_stats[m.title] = pr.get("period_sum")
                ml.append(f"- {m.title}: {pr.get('period_sum')}{tgt} {m.unit or ''}".rstrip())
        lines.append("\n".join(ml))

    if day_logs:
        dl = ["Дни (главное и итог):"]
        for d in day_logs:
            mark = d.outcome or "без отметки"
            blk = f"; помешало: {d.blocker}" if d.blocker else ""
            dl.append(f"- {d.plan_date.strftime('%d.%m')}: «{d.main_thing or '—'}» → {mark}{blk}")
        lines.append("\n".join(dl))

    if recent:
        lines.append("Последние реплики человека (тон/стиль):\n"
                     + "\n".join(f"- {t[:160]}" for t in recent))

    stats = {
        "checkins_asked": asked, "checkins_answered": answered,
        "habits": habit_stats, "metrics": metric_stats,
        "days_with_log": len(day_logs), "days_elapsed": days_elapsed,
    }
    return "\n\n".join(lines), stats, start, today


async def run_reflection(session, user):
    """Produce + store a PROPOSED reflection (not applied). Returns the row or None
    (None if there isn't enough data yet or the LLM call failed)."""
    evidence, stats, start, end = await gather_week_evidence(session, user)
    if not has_enough_data(stats):
        return None
    prof = await repo.get_or_create_profile(session, user.id)
    result = await openai_service.reflect(evidence, prof.playbook, prof.week_theme)
    if not result:
        return None
    refl = await repo.add_reflection(
        session, user.id, start, end,
        stats=json.dumps(stats, ensure_ascii=False),
        retrospective=(result.get("retrospective") or "").strip() or None,
        proposed_playbook=(result.get("playbook") or "").strip() or None,
        proposed_theme=(result.get("week_theme") or "").strip() or None,
    )
    return refl


def has_enough_data(stats: dict) -> bool:
    """Don't reflect on near-empty weeks."""
    return (stats.get("checkins_answered", 0) + stats.get("days_with_log", 0)
            + sum((stats.get("habits") or {}).values())) >= 3
