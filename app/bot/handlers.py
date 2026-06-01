"""Telegram handlers: voice + text + commands. Built on aiogram 3."""
from __future__ import annotations

import asyncio
import io
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import briefing
from app import reflection
from app import repository as repo
from app.bot import text as T
from app.config import get_settings
from app.db.session import get_sessionmaker
from app.services import calendar_service, charts, openai_service, tools

logger = logging.getLogger(__name__)
router = Router()


def _short(text: str | None, limit: int = 800) -> str:
    if not text:
        return ""
    text = " ⏎ ".join(text.splitlines())
    return text if len(text) <= limit else text[:limit] + "…"


class IncomingLogMiddleware(BaseMiddleware):
    """Log every incoming message (incl. voice) so traffic is visible in logs."""

    async def __call__(self, handler, event: Message, data):
        try:
            uid = event.from_user.id if event.from_user else None
            name = event.from_user.first_name if event.from_user else "?"
            extra = ""
            if event.voice:
                extra = f" voice={event.voice.duration}s/{event.voice.file_size}b"
            elif event.audio:
                extra = f" audio={event.audio.file_size}b"
            elif event.video_note:
                extra = f" video_note={event.video_note.duration}s"
            body = f": {_short(event.text)}" if event.text else ""
            logger.info(
                "⬇️  ВХОД  %s[%s] type=%s%s%s", name, uid, event.content_type, extra, body
            )
        except Exception:  # noqa: BLE001 — logging must never break handling
            pass
        return await handler(event, data)


router.message.outer_middleware(IncomingLogMiddleware())


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _is_allowed(user_id: int) -> bool:
    allowed = get_settings().allowed_ids
    return not allowed or user_id in allowed


def local_today(tz_name: str):
    return datetime.now(ZoneInfo(tz_name)).date()


def local_now_str(tz_name: str) -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%A %d.%m %H:%M")


def _habit_status_lines(habits, logs) -> list[str]:
    """Human-readable per-habit status for today."""
    lines = []
    for h in habits:
        log = logs.get(h.id)
        if log and log.status == "done":
            mark = "✅ сделано"
        elif log and log.status == "partial":
            mark = "🟡 частично"
        elif log and log.status == "skipped":
            mark = "⏭ пропущено"
        else:
            mark = "❌ ещё нет"
        meta = []
        if h.target_minutes:
            meta.append(f"{h.target_minutes} мин")
        if h.schedule_time:
            meta.append(h.schedule_time)
        suffix = f" ({', '.join(meta)})" if meta else ""
        lines.append(f"• {h.title}{suffix}: {mark}")
    return lines


async def _build_context_block(session, user) -> str:
    today = local_today(user.timezone)
    yesterday = today - timedelta(days=1)
    parts = [f"Сегодня {today.isoformat()}, сейчас {local_now_str(user.timezone)} ({user.timezone})."]

    # Today's focus — stated explicitly so the model doesn't pull yesterday's
    # "main thing" out of the message history and present it as today's.
    dl_today = await repo.get_day_log(session, user.id, today)
    if dl_today and dl_today.main_thing:
        parts.append(f"Главная вещь на СЕГОДНЯ: «{dl_today.main_thing}».")
    else:
        parts.append("Главная вещь на СЕГОДНЯ: пока не задана — предложи выбрать одну.")
    dl_yest = await repo.get_day_log(session, user.id, yesterday)
    if dl_yest and dl_yest.main_thing:
        out = {"done": "выполнена", "partial": "частично", "missed": "не сделана"}.get(
            dl_yest.outcome or "", "без отметки"
        )
        parts.append(
            f"(Справка: ВЧЕРА главным было «{dl_yest.main_thing}» — {out}. "
            "Это вчерашнее, не выдавай за сегодняшнее.)"
        )

    prof = await repo.get_or_create_profile(session, user.id)
    if prof.summary:
        parts.append("Досье о человеке:\n" + prof.summary)
    if prof.about:
        parts.append("Факты о человеке:\n" + prof.about)
    if prof.week_theme:
        parts.append("Тема недели (держи фокус на ней): " + prof.week_theme)
    if prof.playbook:
        parts.append(
            "Как работать ИМЕННО с этим человеком (выучено за прошлые недели):\n"
            + prof.playbook
        )

    goals = await repo.list_active_goals(session, user.id)
    if goals:
        parts.append("Активные цели:\n" + "\n".join(f"• {g.title}" for g in goals))

    habits = await repo.list_active_habits(session, user.id)
    if habits:
        logs = await repo.habit_logs_for_day(session, user.id, today)
        parts.append("Привычки на сегодня:\n" + "\n".join(_habit_status_lines(habits, logs)))

    tasks = await repo.tasks_for_day(session, user.id, today)
    if tasks:
        lines = []
        for t in tasks:
            mark = {"done": "✅", "doing": "▶️", "skipped": "⏭"}.get(t.status, "•")
            tm = f"{t.planned_time} " if t.planned_time else ""
            lines.append(f"{mark} {tm}{t.title}")
        parts.append("План на сегодня:\n" + "\n".join(lines))

    overdue = await repo.open_tasks_before(session, user.id, today)
    if overdue:
        parts.append(
            "Не закрыто с прошлых дней (хвосты — мягко напомни, предложи закрыть, "
            "перенести или отказаться):\n"
            + "\n".join(
                f"• {t.title} (от {t.plan_date.strftime('%d.%m')})" for t in overdue
            )
        )

    # Calendar is read-only and sync; run it off the event loop.
    events = await asyncio.get_running_loop().run_in_executor(
        None, calendar_service.get_events_for_day, today, user.timezone
    )
    formatted = calendar_service.format_events(events)
    if formatted:
        parts.append("События календаря на сегодня:\n" + formatted)

    return "\n\n".join(parts)


async def _apply_signals(session, user_id: int, plan_day, signals: dict) -> None:
    """Quietly log the current activity (deduped). Best-effort, never raises.
    Explicit data (goals, habits, mood, tasks, settings) is handled by tools."""
    try:
        activity = signals.get("activity")
        if isinstance(activity, str) and activity.strip():
            focus = signals.get("focus_level")
            focus = focus if isinstance(focus, int) and 1 <= focus <= 5 else None
            category = signals.get("category")
            category = category if isinstance(category, str) and category else None
            act = await repo.add_activity(
                session, user_id, activity.strip(), category=category, focus_level=focus
            )
            if act is not None:
                foc = f", фокус {focus}/5" if focus else ""
                logger.info("💾 активность «%s»%s", activity.strip(), foc)
    except Exception:  # noqa: BLE001
        logger.exception("failed to log activity")


async def _converse(bot: Bot, message: Message, user_text: str, kind: str,
                    voice_file_id: str | None = None,
                    transcription: str | None = None) -> None:
    """Shared pipeline for any incoming user utterance (text or voice)."""
    tg_user = message.from_user
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, tg_user)
        await repo.touch_interaction(session, user)
        await repo.log_message(
            session, user.id, "user", user_text, kind=kind,
            voice_file_id=voice_file_id, transcription=transcription,
        )

        # If the bot recently asked a check-in, treat this as the answer.
        pending = await repo.latest_unanswered_checkin(session, user.id)
        if pending is not None:
            await repo.answer_checkin(session, pending, user_text)
            logger.info("✅ ЗАЧТЕНО как ответ на проверку #%s", pending.id)

        history_rows = await repo.recent_messages(session, user.id, limit=20)
        history = [{"role": m.role, "content": m.content} for m in history_rows if m.content]
        context_block = await _build_context_block(session, user)

        await session.commit()

    logger.info(
        "🧠 ДУМАЮ над ответом: беру %d реплик истории + %d символов контекста (план/календарь)",
        len(history), len(context_block or ""),
    )
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    async def _tool_executor(name: str, args: dict) -> str:
        if name == "show_card":
            kind = args.get("kind") or "dashboard"
            try:
                ok = await _send_card(message, user, kind)
                res = (f"ок: отправил карточку «{kind}»" if ok
                       else f"ошибка: неизвестная карточка «{kind}»")
            except Exception:  # noqa: BLE001
                logger.exception("show_card failed")
                res = "ошибка при отправке карточки"
        else:
            res = await tools.run_tool(user.id, name, args)
        logger.info("🛠 ИНСТРУМЕНТ %s(%s) → %s", name, args, res)
        try:
            async with sm() as ev_session:
                await repo.add_tool_event(
                    ev_session, user.id, name,
                    json.dumps(args, ensure_ascii=False), res,
                )
                await ev_session.commit()
        except Exception:  # noqa: BLE001 — audit must never break the chat
            logger.exception("failed to record tool event")
        return res

    try:
        reply = await openai_service.mentor_reply_with_tools(
            history, context_block, _tool_executor
        )
    except Exception:  # noqa: BLE001
        logger.exception("mentor_reply failed")
        await message.answer(T.ERROR_GENERIC)
        return

    await message.answer(reply)
    logger.info("🤖 ОТВЕТ  %s[%s]: %s", tg_user.first_name, tg_user.id, _short(reply))

    # Passive activity logging only (everything explicit goes via tools).
    signals = await openai_service.extract_signals(user_text)
    if signals:
        useful = {k: v for k, v in signals.items() if v not in (None, [], "")}
        if useful:
            logger.info("🔎 АКТИВНОСТЬ: %s", useful)
    async with sm() as session:
        await repo.log_message(session, user.id, "assistant", reply, kind="text")
        if signals:
            await _apply_signals(session, user.id, local_today(user.timezone), signals)
        await session.commit()

    # Refresh the rolling profile summary (the reconciled long-term memory).
    if len(user_text.split()) >= 8:
        try:
            async with sm() as session:
                prof = await repo.get_or_create_profile(session, user.id)
                recent = await repo.recent_messages(session, user.id, limit=12)
                recent_text = "\n".join(
                    f"{m.role}: {m.content}" for m in recent if m.content
                )
                new_summary = await openai_service.update_profile_summary(
                    prof.summary, recent_text
                )
                if new_summary:
                    await repo.update_summary(session, user.id, new_summary)
                    await session.commit()
                    logger.info("🧠 ДОСЬЕ обновлено (%d симв.)", len(new_summary))
        except Exception:  # noqa: BLE001
            logger.exception("summary refresh failed")


# ─── Commands ────────────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer(T.NOT_ALLOWED)
        return
    sm = get_sessionmaker()
    async with sm() as session:
        await repo.get_or_create_user(session, message.from_user)
        await session.commit()
    await message.answer(T.START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    await message.answer(T.HELP)


@router.message(Command("pause"))
async def cmd_pause(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        user.paused = True
        await session.commit()
    await message.answer(T.PAUSED)


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        user.paused = False
        await session.commit()
    await message.answer(T.RESUMED)


@router.message(Command("interval"))
async def cmd_interval(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(T.INTERVAL_USAGE)
        return
    n = int(parts[1])
    if not (1 <= n <= 1440):
        await message.answer(T.INTERVAL_USAGE)
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        user.checkin_interval_min = n
        await session.commit()
    await message.answer(T.INTERVAL_SET.format(n=n))


@router.message(Command("mood"))
async def cmd_mood(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(T.MOOD_USAGE)
        return
    score = max(1, min(5, int(parts[1])))
    note = parts[2] if len(parts) > 2 else None
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        await repo.add_mood(session, user.id, score, note=note)
        await session.commit()
    await message.answer(T.MOOD_SAVED.format(score=score, note=note or ""))


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        today = local_today(user.timezone)
        acts = await repo.activities_for_day(session, user.id, today, user.timezone)
        moods = await repo.moods_for_day(session, user.id, today, user.timezone)
        tasks = await repo.tasks_for_day(session, user.id, today)
        overdue = await repo.open_tasks_before(session, user.id, today)
        goals = await repo.list_active_goals(session, user.id)
        habits = await repo.list_active_habits(session, user.id)
        hlogs = await repo.habit_logs_for_day(session, user.id, today)
        await session.commit()

    if not (acts or moods or tasks or overdue or goals or habits):
        await message.answer(T.STATUS_EMPTY)
        return

    lines = [f"📅 Сегодня ({today.strftime('%d.%m')}):"]
    if goals:
        lines.append("\n🎯 Цели:")
        lines += [f"• {g.title}" for g in goals]
    if habits:
        lines.append("\n🔁 Привычки:")
        lines += _habit_status_lines(habits, hlogs)
    if tasks:
        lines.append("\n📝 План:")
        for t in tasks:
            mark = {"done": "✅", "doing": "▶️", "skipped": "⏭"}.get(t.status, "•")
            tm = f"{t.planned_time} " if t.planned_time else ""
            lines.append(f"{mark} {tm}{t.title}")
    if overdue:
        lines.append("\n⏳ Хвосты (не закрыто с прошлых дней):")
        for t in overdue:
            lines.append(f"• {t.title} (от {t.plan_date.strftime('%d.%m')})")
    if acts:
        lines.append("\n⏱ Чем занимался:")
        for a in acts:
            tm = a.started_at.astimezone(ZoneInfo(user.timezone)).strftime("%H:%M")
            foc = f" (фокус {a.focus_level}/5)" if a.focus_level else ""
            lines.append(f"• {tm} {a.description}{foc}")
    if moods:
        avg = sum(m.score for m in moods) / len(moods)
        lines.append(f"\n🙂 Настроение: средн. {avg:.1f}/5 ({len(moods)} отметок)")
    await message.answer("\n".join(lines))


@router.message(Command("plan"))
async def cmd_plan(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    # Reuse the conversation pipeline with a planning nudge.
    await _converse(
        message.bot, message,
        "Давай спланируем мой день. Задай мне нужные вопросы и помоги составить "
        "короткий реалистичный план с приоритетами.",
        kind="text",
    )


def _parse_time_token(tok: str) -> str | None:
    """Return 'HH:MM' if tok looks like a time, else None."""
    if ":" not in tok:
        return None
    h, _, m = tok.partition(":")
    if h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
        return f"{int(h):02d}:{int(m):02d}"
    return None


@router.message(Command("goals"))
async def cmd_goals(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    arg = (message.text or "").split(maxsplit=1)
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        if len(arg) > 1 and arg[1].strip():
            created = await repo.add_goal(session, user.id, arg[1].strip())
            await session.commit()
            await message.answer(
                f"🎯 Добавил цель: {arg[1].strip()}" if created
                else "Такая цель уже есть."
            )
            return
        goals = await repo.list_active_goals(session, user.id)
        await session.commit()
    if not goals:
        await message.answer("Целей пока нет. Добавь, например: /goals Запустить свой проект")
        return
    await message.answer("🎯 Твои цели:\n" + "\n".join(f"• {g.title}" for g in goals))


@router.message(Command("habits"))
async def cmd_habits(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    parts = (message.text or "").split()
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        if len(parts) > 1:
            # Parse: title words + optional minutes (int) + optional HH:MM.
            sched, mins, words = None, None, []
            for tok in parts[1:]:
                t = _parse_time_token(tok)
                if t:
                    sched = t
                elif tok.isdigit():
                    mins = int(tok)
                else:
                    words.append(tok)
            title = " ".join(words).strip()
            if not title:
                await message.answer("Использование: /habits спорт 60 15:00")
                return
            created = await repo.add_habit(session, user.id, title, mins, sched)
            await session.commit()
            await message.answer(
                f"🔁 Привычка «{title}» добавлена." if created
                else f"🔁 Привычка «{title}» обновлена."
            )
            return
        habits = await repo.list_active_habits(session, user.id)
        today = local_today(user.timezone)
        logs = await repo.habit_logs_for_day(session, user.id, today)
        await session.commit()
    if not habits:
        await message.answer(
            "Привычек пока нет. Добавь, например: /habits спорт 60 15:00\n"
            "Отметить выполнение: /done спорт"
        )
        return
    await message.answer("🔁 Привычки на сегодня:\n" + "\n".join(_habit_status_lines(habits, logs)))


@router.message(Command("done"))
async def cmd_done(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Что отметить? Например: /done спорт 45")
        return
    mins = None
    body = parts[1:]
    if body and body[-1].isdigit():
        mins = int(body[-1])
        body = body[:-1]
    title = " ".join(body).strip()
    if not title:
        await message.answer("Что отметить? Например: /done спорт 45")
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        habit = await repo.find_habit(session, user.id, title)
        if habit is None:
            habit = await repo.add_habit(session, user.id, title)
        await repo.log_habit(
            session, user.id, habit.id, local_today(user.timezone),
            status="done", minutes=mins,
        )
        await session.commit()
    extra = f" ({mins} мин)" if mins else ""
    await message.answer(f"✅ Отметил «{title}» как выполненную сегодня{extra}. Молодец!")


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        prof = await repo.get_or_create_profile(session, user.id)
        goals = await repo.list_active_goals(session, user.id)
        habits = await repo.list_active_habits(session, user.id)
        await session.commit()
    blocks = ["🧠 Что я о тебе знаю:"]
    if prof.summary:
        blocks.append("\n📋 Досье:\n" + prof.summary)
    if prof.about:
        blocks.append("\nℹ️ Факты:\n" + prof.about)
    if goals:
        blocks.append("\n🎯 Цели:\n" + "\n".join(f"• {g.title}" for g in goals))
    if habits:
        blocks.append("\n🔁 Привычки:\n" + "\n".join(
            f"• {h.title}" + (f" ({h.target_minutes} мин)" if h.target_minutes else "")
            + (f" в {h.schedule_time}" if h.schedule_time else "")
            for h in habits
        ))
    if len(blocks) == 1:
        blocks.append("\nПока почти ничего — расскажи о себе, целях и привычках, "
                      "и я всё запомню.")
    await message.answer("\n".join(blocks))


@router.message(Command("tz"))
async def cmd_tz(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Использование: /tz America/New_York")
        return
    tzname = parts[1].strip()
    try:
        ZoneInfo(tzname)
    except Exception:  # noqa: BLE001
        await message.answer("Не знаю такой пояс. Пример: /tz America/New_York")
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        user.timezone = tzname
        await session.commit()
    await message.answer(f"🕐 Часовой пояс: {tzname}. Сейчас у тебя {local_now_str(tzname)}.")


# ─── Visual cards (images) ───────────────────────────────────────────────────
async def _render(func, data) -> bytes:
    """Run a (sync) matplotlib renderer off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, func, data)


# kind -> (gather data fn, render fn, caption, filename). Used by both the slash
# commands and the show_card tool (so the bot can send cards from voice/text too).
_CARD_SPECS = {
    "dashboard": (briefing.gather_dashboard_data, charts.render_dashboard,
                  "Карточка дня.", "dashboard.png"),
    "report": (briefing.gather_report_data, charts.render_report,
               "Отчёт за неделю.", "report.png"),
    "streak": (briefing.gather_streak_data, charts.render_streak,
               "Серии привычек.", "streak.png"),
    "calendar": (briefing.gather_calendar_data, charts.render_countdown_calendar,
                 "Обратный отсчёт до цели.", "calendar.png"),
    "rhythm": (briefing.gather_rhythm_data, charts.render_rhythm,
               "Когда ты активен.", "rhythm.png"),
}


async def _send_card(message: Message, user, kind: str) -> bool:
    """Render and send a visual card. Returns False for an unknown kind."""
    spec = _CARD_SPECS.get((kind or "").lower())
    if spec is None:
        return False
    gather, render, caption, fname = spec
    sm = get_sessionmaker()
    async with sm() as session:
        data = await gather(session, user)
        await session.commit()
    png = await _render(render, data)
    await message.answer_photo(BufferedInputFile(png, filename=fname), caption=caption)
    return True


@router.message(Command("dashboard"))
async def cmd_dashboard(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        data = await briefing.gather_dashboard_data(session, user)
        await session.commit()
    png = await _render(charts.render_dashboard, data)
    await message.answer_photo(
        BufferedInputFile(png, filename="dashboard.png"),
        caption="Твоя карточка дня. Вперёд 💪",
    )


@router.message(Command("streak"))
async def cmd_streak(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        data = await briefing.gather_streak_data(session, user)
        await session.commit()
    png = await _render(charts.render_streak, data)
    await message.answer_photo(
        BufferedInputFile(png, filename="streak.png"),
        caption="Серии привычек. Не разрывай цепочку.",
    )


@router.message(Command("calendar"))
async def cmd_calendar(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        data = await briefing.gather_calendar_data(session, user)
        await session.commit()
    png = await _render(charts.render_countdown_calendar, data)
    await message.answer_photo(
        BufferedInputFile(png, filename="calendar.png"),
        caption="Сколько дней осталось. Каждый зачёркнутый — назад не вернуть.",
    )


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        data = await briefing.gather_report_data(session, user)
        await session.commit()
    png = await _render(charts.render_report, data)
    await message.answer_photo(
        BufferedInputFile(png, filename="report.png"),
        caption="Отчёт за неделю. Цифры не врут — так прошла твоя неделя.",
    )


@router.message(Command("rhythm"))
async def cmd_rhythm(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        data = await briefing.gather_rhythm_data(session, user)
        await session.commit()
    png = await _render(charts.render_rhythm, data)
    sug = data["suggested"]
    cur = data["current"]
    kb = None
    caption = "Вот когда ты обычно на связи."
    if sug and data["days"] >= 3:
        s, e = sug
        caption += (f"\nСейчас активные часы {cur[0]:02d}:00–{cur[1]:02d}:59, "
                    f"а по факту ты активен {s:02d}:00–{e:02d}:59. Подстроить?")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Применить {s:02d}:00–{e:02d}:59",
                                 callback_data=f"rhythm:{s}:{e}"),
            InlineKeyboardButton(text="Оставить", callback_data="rhythm:keep"),
        ]])
    elif sug:
        s, e = sug
        caption += (f"\nПо пока коротким данным ({data['days']} дн.) похоже на "
                    f"{s:02d}:00–{e:02d}:59. Накоплю за несколько дней — предложу "
                    f"точнее. Можешь и сам задать словами или через /tz.")
    else:
        caption += " Пока мало данных — поговори со мной ещё, и я подстрою часы под тебя."
    await message.answer_photo(
        BufferedInputFile(png, filename="rhythm.png"), caption=caption, reply_markup=kb
    )


@router.callback_query(F.data.startswith("rhythm:"))
async def on_rhythm_apply(cb: CallbackQuery) -> None:
    if not _is_allowed(cb.from_user.id):
        await cb.answer()
        return
    parts = (cb.data or "").split(":")
    if len(parts) == 2 and parts[1] == "keep":
        await cb.answer("Ок, оставил как есть.")
        return
    try:
        s, e = int(parts[1]), int(parts[2])
    except (ValueError, IndexError):
        await cb.answer("Не понял кнопку.")
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, cb.from_user)
        user.active_from, user.active_to = f"{s:02d}:00", f"{e:02d}:59"
        await session.commit()
    logger.info("🕐 active hours updated via /rhythm: %02d:00–%02d:59 for %s", s, e, cb.from_user.id)
    await cb.answer("Готово! Подстроил под тебя.")
    await cb.message.answer(
        f"🕐 Активные часы теперь {s:02d}:00–{e:02d}:59. Теперь и проверки каждые "
        f"N минут, и утренний дашборд будут приходить в твоё реальное время."
    )


# ─── Weekly self-reflection (the learning loop) ──────────────────────────────
async def send_reflection_proposal(bot, user) -> bool:
    """Run the weekly self-analysis and propose updates (applied on confirm).
    Returns False if there isn't enough data yet."""
    sm = get_sessionmaker()
    async with sm() as session:
        refl = await reflection.run_reflection(session, user)
        await session.commit()
    if refl is None:
        return False
    theme = refl.proposed_theme or "—"
    text = (
        "🧭 Итоги недели\n\n"
        f"{refl.retrospective or ''}\n\n"
        f"Предлагаю фокус на следующую неделю: «{theme}».\n"
        "Применить выводы и тему?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Применить", callback_data="reflect:apply"),
        InlineKeyboardButton(text="Оставить", callback_data="reflect:keep"),
    ]])
    await bot.send_message(user.id, text, reply_markup=kb)
    return True


@router.message(Command("reflect"))
async def cmd_reflect(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, message.from_user)
        await session.commit()
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    if not await send_reflection_proposal(message.bot, user):
        await message.answer(T.REFLECT_NO_DATA)


@router.callback_query(F.data.startswith("reflect:"))
async def on_reflect(cb: CallbackQuery) -> None:
    if not _is_allowed(cb.from_user.id):
        await cb.answer()
        return
    action = (cb.data or "reflect:").split(":", 1)[1]
    if action == "keep":
        await cb.answer()
        await cb.message.answer(T.REFLECT_KEPT)
        return
    sm = get_sessionmaker()
    async with sm() as session:
        user = await repo.get_or_create_user(session, cb.from_user)
        refl = await repo.latest_unapplied_reflection(session, user.id)
        if refl is None:
            await session.commit()
            await cb.answer("Нечего применять.")
            return
        theme = refl.proposed_theme or "—"
        await repo.apply_reflection(session, refl, local_today(user.timezone))
        await session.commit()
    logger.info("🧭 reflection applied for %s; theme=%s", cb.from_user.id, theme)
    await cb.answer("Готово!")
    await cb.message.answer(T.REFLECT_APPLIED.format(theme=theme))


# ─── Voice / audio ───────────────────────────────────────────────────────────
async def _handle_audio(message: Message, file_id: str) -> None:
    """Download an audio-bearing message, transcribe it and converse."""
    if not _is_allowed(message.from_user.id):
        await message.answer(T.NOT_ALLOWED)
        return
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        tg_file = await message.bot.get_file(file_id)
        buf = io.BytesIO()
        await message.bot.download_file(tg_file.file_path, buf)
        audio = buf.getvalue()
        transcription = await openai_service.transcribe_voice(audio)
    except Exception:  # noqa: BLE001
        logger.exception("voice transcription failed")
        await message.answer(T.ERROR_GENERIC)
        return

    if not transcription:
        await message.answer("Не разобрал голосовое. Повтори, пожалуйста, или напиши текстом.")
        return

    logger.info("🗣 transcribed (%d chars): %s", len(transcription), transcription[:80])
    await _converse(
        message.bot, message, transcription, kind="voice",
        voice_file_id=file_id, transcription=transcription,
    )


@router.message(F.voice | F.audio | F.video_note)
async def on_audio(message: Message) -> None:
    media = message.voice or message.audio or message.video_note
    await _handle_audio(message, media.file_id)


@router.message(F.document)
async def on_document(message: Message) -> None:
    mime = (message.document.mime_type or "").lower()
    if mime.startswith("audio") or mime in ("application/ogg", "video/ogg"):
        await _handle_audio(message, message.document.file_id)
    else:
        logger.info("unhandled document mime=%s", mime)
        await message.answer("Пока умею текст и голосовые. Пришли голосовое или напиши текстом.")


# ─── Plain text ──────────────────────────────────────────────────────────────
@router.message(F.text)
async def on_text(message: Message) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer(T.NOT_ALLOWED)
        return
    await _converse(message.bot, message, message.text, kind="text")


# ─── Fallback (anything else: photos, stickers, …). Registered last. ─────────
@router.message()
async def on_other(message: Message) -> None:
    logger.info("unhandled message content_type=%s", message.content_type)
    if not _is_allowed(message.from_user.id):
        return
    await message.answer("Пока понимаю только текст и голосовые сообщения.")
