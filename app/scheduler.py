"""Proactive engine. A 1-minute tick decides, per user and within active hours:
  • timed habit reminders  — "пора на час спорта" at the habit's scheduled time;
  • periodic check-ins      — "чем занят?", aware of goals and undone habits.
All respect the pause flag and active hours.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import BufferedInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import briefing
from app import repository as repo
from app.db.models import User
from app.db.session import get_sessionmaker
from app.services import charts, openai_service

# Morning briefing fires at the first tick on/after active_from each day, but
# only within this many minutes of it (so a late start still sends, once).
BRIEFING_GRACE_MIN = 120
# Evening review fires ~1h before active_to, within this grace window.
EVENING_REVIEW_GRACE_MIN = 90

logger = logging.getLogger(__name__)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except Exception:  # noqa: BLE001
        return fallback


def _within_active_hours(now_local: datetime, active_from: str, active_to: str) -> bool:
    start = _parse_hhmm(active_from, time(9, 0))
    end = _parse_hhmm(active_to, time(22, 0))
    cur = now_local.time()
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end  # overnight window


def _checkin_interval_elapsed(user: User, now_utc: datetime) -> bool:
    """True if enough time passed since the last check-in OR real interaction."""
    marks = [t for t in (user.last_checkin_at, user.last_interaction_at) if t]
    if not marks:
        return True
    last = max(marks)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed_min = (now_utc - last).total_seconds() / 60.0
    return elapsed_min >= max(1, user.checkin_interval_min)


# Only remind about a habit within this many minutes after its scheduled time.
# Past that, it's "stale" — we don't fire a separate ping (it still shows up in
# the regular check-in), which also prevents catch-up spam after a tz change.
HABIT_NUDGE_GRACE_MIN = 90


async def _send_habit_nudges(
    bot: Bot, user: User, now_utc: datetime, now_local: datetime
) -> bool:
    """Send AT MOST ONE due habit reminder. Returns True if one was sent."""
    local_day = now_local.date()
    now_min = now_local.hour * 60 + now_local.minute
    sm = get_sessionmaker()
    async with sm() as session:
        habits = await repo.list_active_habits(session, user.id)
        logs = await repo.habit_logs_for_day(session, user.id, local_day)
        for h in habits:
            if not h.schedule_time:
                continue  # timeless habits are nudged via check-in context instead
            st = _parse_hhmm(h.schedule_time, time(23, 59))
            sched_min = st.hour * 60 + st.minute
            if not (0 <= now_min - sched_min <= HABIT_NUDGE_GRACE_MIN):
                continue  # not time yet, or the window has passed (stale)
            lg = logs.get(h.id)
            if lg and lg.status in ("done", "skipped"):
                continue  # already handled today
            if await repo.nudge_sent_today(
                session, user.id, "habit", h.id, local_day, user.timezone
            ):
                continue  # already reminded today
            hint = f"привычка «{h.title}», человек хочет делать её каждый день"
            text = await openai_service.generate_habit_nudge_text(
                h.title, h.target_minutes, hint
            )
            await bot.send_message(user.id, text)
            await repo.add_nudge(session, user.id, "habit", text, habit_id=h.id)
            await repo.log_message(session, user.id, "assistant", text, kind="checkin")
            # Reset the check-in cadence so a generic ping doesn't pile on top.
            fresh = await session.get(User, user.id)
            if fresh is not None:
                fresh.last_checkin_at = now_utc
            await session.commit()
            logger.info(
                "⏰→ напоминание о привычке user=%s «%s»: %s",
                user.id, h.title, text.replace("\n", " "),
            )
            return True  # one proactive message per tick — the rest waits
        await session.commit()
    return False


async def _send_morning_briefing(
    bot: Bot, user: User, now_utc: datetime, now_local: datetime
) -> bool:
    """Send the daily dashboard image once, near the start of active hours."""
    af = _parse_hhmm(user.active_from, time(9, 0))
    start_min = af.hour * 60 + af.minute
    now_min = now_local.hour * 60 + now_local.minute
    if not (0 <= now_min - start_min <= BRIEFING_GRACE_MIN):
        return False

    local_day = now_local.date()
    sm = get_sessionmaker()
    async with sm() as session:
        if await repo.nudge_sent_today(
            session, user.id, "briefing", None, local_day, user.timezone
        ):
            return False
        data = await briefing.gather_dashboard_data(session, user)
        await session.commit()

    png = await asyncio.get_running_loop().run_in_executor(
        None, charts.render_dashboard, data
    )
    await bot.send_photo(
        user.id,
        BufferedInputFile(png, filename="dashboard.png"),
        caption="Доброе утро. Вот твой день. Назови ОДНУ главную вещь на сегодня "
                "— с неё и начнём.",
    )
    async with sm() as session:
        await repo.add_nudge(session, user.id, "briefing", "morning dashboard")
        await repo.log_message(
            session, user.id, "assistant", "[утренний дашборд]", kind="checkin"
        )
        fresh = await session.get(User, user.id)
        if fresh is not None:
            fresh.last_checkin_at = now_utc
        await session.commit()
    logger.info("⏰→ утренний дашборд отправлен user=%s", user.id)
    return True


async def _send_weekly_report(
    bot: Bot, user: User, now_utc: datetime, now_local: datetime
) -> bool:
    """On Sunday, ~2.5h before active_to, send the weekly metrics report once."""
    if now_local.weekday() != 6:  # Sunday
        return False
    at = _parse_hhmm(user.active_to, time(22, 0))
    af = _parse_hhmm(user.active_from, time(9, 0))
    report_min = max(at.hour * 60 + at.minute - 150, af.hour * 60 + af.minute)
    now_min = now_local.hour * 60 + now_local.minute
    if not (0 <= now_min - report_min <= 90):
        return False

    local_day = now_local.date()
    sm = get_sessionmaker()
    async with sm() as session:
        if await repo.nudge_sent_today(
            session, user.id, "report", None, local_day, user.timezone
        ):
            return False
        data = await briefing.gather_report_data(session, user)
        await session.commit()
    if not data.get("metrics"):
        return False  # nothing to report — let other messages run

    png = await asyncio.get_running_loop().run_in_executor(
        None, charts.render_report, data
    )
    await bot.send_photo(
        user.id,
        BufferedInputFile(png, filename="report.png"),
        caption="Итоги недели в цифрах. Где-то добили, где-то — есть куда расти.",
    )
    async with sm() as session:
        await repo.add_nudge(session, user.id, "report", "weekly report")
        await repo.log_message(session, user.id, "assistant", "[недельный отчёт]", kind="checkin")
        fresh = await session.get(User, user.id)
        if fresh is not None:
            fresh.last_checkin_at = now_utc
        await session.commit()
    logger.info("⏰→ недельный отчёт отправлен user=%s", user.id)
    return True


async def _send_evening_review(
    bot: Bot, user: User, now_utc: datetime, now_local: datetime
) -> bool:
    """Initiate the evening review ~1h before active_to, once per day."""
    at = _parse_hhmm(user.active_to, time(22, 0))
    af = _parse_hhmm(user.active_from, time(9, 0))
    review_min = max(at.hour * 60 + at.minute - 60, af.hour * 60 + af.minute)
    now_min = now_local.hour * 60 + now_local.minute
    if not (0 <= now_min - review_min <= EVENING_REVIEW_GRACE_MIN):
        return False

    local_day = now_local.date()
    sm = get_sessionmaker()
    async with sm() as session:
        if await repo.nudge_sent_today(
            session, user.id, "review", None, local_day, user.timezone
        ):
            return False
        dl = await repo.get_day_log(session, user.id, local_day)
        main = dl.main_thing if dl else None
        habits = await repo.list_active_habits(session, user.id)
        logs = await repo.habit_logs_for_day(session, user.id, local_day)
        undone = [
            h.title for h in habits
            if not (logs.get(h.id) and logs[h.id].status == "done")
        ]
        await session.commit()

    text = await openai_service.generate_review_text(main, undone)
    await bot.send_message(user.id, text)
    async with sm() as session:
        await repo.add_nudge(session, user.id, "review", text)
        await repo.log_message(session, user.id, "assistant", text, kind="checkin")
        fresh = await session.get(User, user.id)
        if fresh is not None:
            fresh.last_checkin_at = now_utc
        await session.commit()
    logger.info("⏰→ вечерний разбор user=%s: %s", user.id, text.replace("\n", " "))
    return True


async def _send_checkin(bot: Bot, user: User, now_utc: datetime,
                        now_local: datetime) -> None:
    local_day = now_local.date()
    sm = get_sessionmaker()
    # Build a hint from goals + undone habits so the check-in pushes what matters.
    async with sm() as session:
        goals = await repo.list_active_goals(session, user.id)
        habits = await repo.list_active_habits(session, user.id)
        logs = await repo.habit_logs_for_day(session, user.id, local_day)
        undone = [
            h.title for h in habits
            if not (logs.get(h.id) and logs[h.id].status == "done")
        ]
        await session.commit()

    hint_parts = []
    if goals:
        hint_parts.append("главная цель: " + goals[0].title)
    if undone:
        hint_parts.append("сегодня ещё не сделано: " + ", ".join(undone))
    hint = "; ".join(hint_parts) or None

    question = await openai_service.generate_checkin_text(hint)
    await bot.send_message(user.id, question)
    async with sm() as session:
        fresh = await session.get(User, user.id)
        if fresh is None:
            return
        await repo.create_checkin(session, fresh.id, question)
        await repo.log_message(session, fresh.id, "assistant", question, kind="checkin")
        fresh.last_checkin_at = now_utc
        await session.commit()
    logger.info("⏰→ проверка user=%s: %s", user.id, question.replace("\n", " "))


async def _mark_blocked(user_id: int) -> None:
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            u = await session.get(User, user_id)
            if u is not None:
                u.blocked = True
                await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("failed to mark user %s blocked", user_id)


async def checkin_tick(bot: Bot) -> None:
    now_utc = datetime.now(timezone.utc)
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            users = await repo.list_users(session)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("checkin_tick: failed to load users")
        return

    for user in users:
        if user.paused or user.blocked:
            continue
        try:
            now_local = now_utc.astimezone(ZoneInfo(user.timezone))
        except Exception:  # noqa: BLE001
            now_local = now_utc
        if not _within_active_hours(now_local, user.active_from, user.active_to):
            continue

        # At most ONE proactive message per tick. Priority: morning briefing →
        # weekly report → evening review → habit reminder → periodic check-in.
        try:
            sent = await _send_morning_briefing(bot, user, now_utc, now_local)
            if not sent:
                sent = await _send_weekly_report(bot, user, now_utc, now_local)
            if not sent:
                sent = await _send_evening_review(bot, user, now_utc, now_local)
            if not sent:
                sent = await _send_habit_nudges(bot, user, now_utc, now_local)
            if not sent and _checkin_interval_elapsed(user, now_utc):
                await _send_checkin(bot, user, now_utc, now_local)
        except TelegramForbiddenError:
            await _mark_blocked(user.id)
            logger.info(
                "user %s blocked the bot — marked blocked, stopping proactive sends",
                user.id,
            )
        except Exception:  # noqa: BLE001 — a single bad user must not stop the rest
            logger.exception("checkin_tick: proactive send failed for user %s", user.id)


def build_scheduler(bot: Bot, timezone_name: str) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone_name)
    scheduler.add_job(
        checkin_tick,
        trigger="interval",
        minutes=1,
        args=[bot],
        id="checkin_tick",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    return scheduler
