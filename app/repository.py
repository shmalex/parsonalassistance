"""Data-access helpers. Append-only: these functions INSERT and UPDATE, never
DELETE. There is intentionally no delete/drop helper in this module.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings


def _norm(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for fuzzy matching."""
    text = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _similar(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


from app.db.models import (  # noqa: E402
    Activity,
    CheckIn,
    DayLog,
    Goal,
    Habit,
    HabitLog,
    Message,
    Metric,
    MetricEntry,
    Milestone,
    Mood,
    Nudge,
    Profile,
    Reflection,
    Task,
    ToolEvent,
    User,
)


# ─── Users ───────────────────────────────────────────────────────────────────
async def get_or_create_user(session: AsyncSession, tg_user) -> User:
    """tg_user is an aiogram types.User."""
    user = await session.get(User, tg_user.id)
    if user is None:
        s = get_settings()
        user = User(
            id=tg_user.id,
            username=getattr(tg_user, "username", None),
            first_name=getattr(tg_user, "first_name", None),
            last_name=getattr(tg_user, "last_name", None),
            language_code=getattr(tg_user, "language_code", None),
            timezone=s.timezone,
            checkin_interval_min=s.checkin_interval_min,
            active_from=s.active_from,
            active_to=s.active_to,
        )
        session.add(user)
        await session.flush()
    else:
        # Keep profile fields fresh, but never wipe them.
        if getattr(tg_user, "username", None):
            user.username = tg_user.username
        if getattr(tg_user, "first_name", None):
            user.first_name = tg_user.first_name
        # They're messaging us → they haven't blocked the bot; clear the flag.
        if user.blocked:
            user.blocked = False
    return user


async def list_users(session: AsyncSession) -> list[User]:
    res = await session.execute(select(User))
    return list(res.scalars().all())


async def touch_interaction(session: AsyncSession, user: User) -> None:
    user.last_interaction_at = datetime.now(timezone.utc)


# ─── Messages (append-only conversation log) ─────────────────────────────────
async def log_message(
    session: AsyncSession,
    user_id: int,
    role: str,
    content: str,
    kind: str = "text",
    voice_file_id: str | None = None,
    transcription: str | None = None,
) -> Message:
    msg = Message(
        user_id=user_id,
        role=role,
        kind=kind,
        content=content or "",
        voice_file_id=voice_file_id,
        transcription=transcription,
    )
    session.add(msg)
    await session.flush()
    return msg


async def recent_messages(
    session: AsyncSession, user_id: int, limit: int = 20
) -> list[Message]:
    res = await session.execute(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    rows = list(res.scalars().all())
    rows.reverse()  # chronological order for the LLM
    return rows


# ─── Check-ins ───────────────────────────────────────────────────────────────
async def create_checkin(session: AsyncSession, user_id: int, question: str) -> CheckIn:
    ci = CheckIn(user_id=user_id, question=question)
    session.add(ci)
    await session.flush()
    return ci


async def latest_unanswered_checkin(
    session: AsyncSession, user_id: int
) -> CheckIn | None:
    res = await session.execute(
        select(CheckIn)
        .where(CheckIn.user_id == user_id, CheckIn.answer.is_(None))
        .order_by(CheckIn.asked_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def answer_checkin(session: AsyncSession, checkin: CheckIn, answer: str) -> None:
    checkin.answer = answer
    checkin.answered_at = datetime.now(timezone.utc)


# ─── Activities / Mood / Tasks ───────────────────────────────────────────────
async def add_activity(
    session: AsyncSession,
    user_id: int,
    description: str,
    category: str | None = None,
    focus_level: int | None = None,
    dedup_minutes: int = 25,
) -> Activity | None:
    """Insert an activity, unless it duplicates the most recent one within the
    dedup window — in that case enrich the existing row and return None."""
    desc = description.strip()
    res = await session.execute(
        select(Activity)
        .where(Activity.user_id == user_id)
        .order_by(Activity.started_at.desc())
        .limit(1)
    )
    last = res.scalar_one_or_none()
    if last and _similar(last.description, desc) >= 0.7:
        la = last.started_at
        if la.tzinfo is None:
            la = la.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - la).total_seconds() <= dedup_minutes * 60:
            if focus_level and not last.focus_level:
                last.focus_level = focus_level
            if category and not last.category:
                last.category = category
            return None

    act = Activity(
        user_id=user_id,
        description=desc,
        category=category,
        focus_level=focus_level,
    )
    session.add(act)
    await session.flush()
    return act


async def add_mood(
    session: AsyncSession, user_id: int, score: int, note: str | None = None
) -> Mood:
    mood = Mood(user_id=user_id, score=score, note=note)
    session.add(mood)
    await session.flush()
    return mood


async def add_task(
    session: AsyncSession,
    user_id: int,
    title: str,
    plan_date: date,
    planned_time: str | None = None,
) -> Task:
    task = Task(
        user_id=user_id, title=title, plan_date=plan_date, planned_time=planned_time
    )
    session.add(task)
    await session.flush()
    return task


def _day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """Start/end of `day` in the user's local timezone (tz-aware)."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


async def activities_for_day(
    session: AsyncSession, user_id: int, day: date, tz_name: str
) -> list[Activity]:
    start, end = _day_bounds(day, tz_name)
    res = await session.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.started_at >= start,
            Activity.started_at < end,
        )
        .order_by(Activity.started_at)
    )
    return list(res.scalars().all())


async def moods_for_day(
    session: AsyncSession, user_id: int, day: date, tz_name: str
) -> list[Mood]:
    start, end = _day_bounds(day, tz_name)
    res = await session.execute(
        select(Mood)
        .where(
            Mood.user_id == user_id,
            Mood.created_at >= start,
            Mood.created_at < end,
        )
        .order_by(Mood.created_at)
    )
    return list(res.scalars().all())


# ─── Day log (morning commitment + evening review) ──────────────────────────
async def get_day_log(
    session: AsyncSession, user_id: int, day: date
) -> DayLog | None:
    res = await session.execute(
        select(DayLog).where(DayLog.user_id == user_id, DayLog.plan_date == day)
    )
    return res.scalar_one_or_none()


async def get_or_create_day_log(
    session: AsyncSession, user_id: int, day: date
) -> DayLog:
    dl = await get_day_log(session, user_id, day)
    if dl is None:
        dl = DayLog(user_id=user_id, plan_date=day)
        session.add(dl)
        await session.flush()
    return dl


async def set_day_focus(
    session: AsyncSession, user_id: int, day: date,
    main_thing: str, intention: str | None = None,
) -> DayLog:
    dl = await get_or_create_day_log(session, user_id, day)
    dl.main_thing = main_thing.strip()
    if intention:
        dl.intention = intention.strip()
    dl.committed_at = datetime.now(timezone.utc)
    return dl


async def log_day_review(
    session: AsyncSession, user_id: int, day: date,
    outcome: str | None = None, reflection: str | None = None,
    blocker: str | None = None, energy: int | None = None,
) -> DayLog:
    dl = await get_or_create_day_log(session, user_id, day)
    if outcome:
        dl.outcome = outcome
    if reflection:
        dl.reflection = reflection.strip()
    if blocker:
        dl.blocker = blocker.strip()
    if isinstance(energy, int) and 1 <= energy <= 5:
        dl.energy = energy
    dl.reviewed_at = datetime.now(timezone.utc)
    return dl


async def recent_day_logs(
    session: AsyncSession, user_id: int, limit: int = 14
) -> list[DayLog]:
    res = await session.execute(
        select(DayLog)
        .where(DayLog.user_id == user_id)
        .order_by(DayLog.plan_date.desc())
        .limit(limit)
    )
    rows = list(res.scalars().all())
    rows.reverse()
    return rows


async def tasks_for_day(session: AsyncSession, user_id: int, day: date) -> list[Task]:
    res = await session.execute(
        select(Task)
        .where(Task.user_id == user_id, Task.plan_date == day)
        .order_by(Task.planned_time.nullslast(), Task.id)
    )
    return list(res.scalars().all())


async def open_tasks_before(
    session: AsyncSession, user_id: int, day: date, limit: int = 12
) -> list[Task]:
    """Unfinished tasks (todo/doing) from earlier days — the carried-over backlog."""
    res = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status.in_(("todo", "doing")),
            Task.plan_date < day,
        )
        .order_by(Task.plan_date, Task.id)
        .limit(limit)
    )
    return list(res.scalars().all())


async def _find_task_fuzzy(
    session: AsyncSession, user_id: int, title: str, statuses: tuple[str, ...] | None
) -> Task | None:
    stmt = select(Task).where(Task.user_id == user_id)
    if statuses:
        stmt = stmt.where(Task.status.in_(statuses))
    res = await session.execute(stmt)
    best, best_score = None, 0.0
    for t in res.scalars().all():
        sc = _similar(t.title, title)
        if sc > best_score:
            best, best_score = t, sc
    return best if best is not None and best_score >= 0.6 else None


async def set_task_status(
    session: AsyncSession, user_id: int, title: str, status: str
) -> str | None:
    """Change status of the best-matching OPEN task (any day). Returns its title."""
    t = await _find_task_fuzzy(session, user_id, title, ("todo", "doing"))
    if t is not None:
        t.status = status
        return t.title
    return None


async def reopen_task(session: AsyncSession, user_id: int, title: str) -> str | None:
    """Put a task back to 'todo' (undo a wrong/early completion)."""
    t = await _find_task_fuzzy(session, user_id, title, ("done", "skipped"))
    if t is None:  # fall back to any status (idempotent)
        t = await _find_task_fuzzy(session, user_id, title, None)
    if t is not None:
        t.status = "todo"
        return t.title
    return None


async def rename_task(
    session: AsyncSession, user_id: int, old_title: str, new_title: str
) -> str | None:
    """Fix a task's wording (e.g. mis-transcription). Returns the NEW title."""
    t = await _find_task_fuzzy(session, user_id, old_title, ("todo", "doing"))
    if t is None:
        t = await _find_task_fuzzy(session, user_id, old_title, None)
    if t is not None:
        t.title = new_title.strip()
        return t.title
    return None


# ─── Profile (long-term memory) ──────────────────────────────────────────────
async def get_or_create_profile(session: AsyncSession, user_id: int) -> Profile:
    prof = await session.get(Profile, user_id)
    if prof is None:
        prof = Profile(user_id=user_id, about="", summary="")
        session.add(prof)
        await session.flush()
    return prof


async def update_summary(session: AsyncSession, user_id: int, summary: str) -> None:
    prof = await get_or_create_profile(session, user_id)
    prof.summary = summary.strip()


async def set_playbook(session: AsyncSession, user_id: int, playbook: str) -> None:
    prof = await get_or_create_profile(session, user_id)
    prof.playbook = (playbook or "").strip()


async def set_week_theme(
    session: AsyncSession, user_id: int, theme: str, day: date
) -> None:
    prof = await get_or_create_profile(session, user_id)
    prof.week_theme = (theme or "").strip() or None
    prof.week_started = day


# ─── Reflections (weekly self-analysis) ──────────────────────────────────────
async def add_reflection(
    session: AsyncSession, user_id: int, period_start: date, period_end: date,
    stats: str | None, retrospective: str | None,
    proposed_playbook: str | None, proposed_theme: str | None,
) -> Reflection:
    r = Reflection(
        user_id=user_id, period_start=period_start, period_end=period_end,
        stats=stats, retrospective=retrospective,
        proposed_playbook=proposed_playbook, proposed_theme=proposed_theme,
    )
    session.add(r)
    await session.flush()
    return r


async def latest_unapplied_reflection(
    session: AsyncSession, user_id: int
) -> Reflection | None:
    res = await session.execute(
        select(Reflection)
        .where(Reflection.user_id == user_id, Reflection.applied.is_(False))
        .order_by(Reflection.created_at.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


async def apply_reflection(session: AsyncSession, reflection: Reflection, day: date) -> None:
    """Apply a proposed reflection: update playbook + week theme, mark applied."""
    prof = await get_or_create_profile(session, reflection.user_id)
    if reflection.proposed_playbook:
        prof.playbook = reflection.proposed_playbook.strip()
    if reflection.proposed_theme:
        prof.week_theme = reflection.proposed_theme.strip()
        prof.week_started = day
    reflection.applied = True


async def reflection_sent_recently(
    session: AsyncSession, user_id: int, since: date
) -> bool:
    start = datetime.combine(since, time.min, tzinfo=timezone.utc)
    res = await session.execute(
        select(func.count()).select_from(Reflection).where(
            Reflection.user_id == user_id, Reflection.created_at >= start
        )
    )
    return (res.scalar() or 0) > 0


# ─── Weekly evidence helpers (for self-analysis) ─────────────────────────────
async def checkin_counts_between(
    session: AsyncSession, user_id: int, start: date, end: date, tz_name: str
) -> tuple[int, int]:
    s_start, _ = _day_bounds(start, tz_name)
    _, e_end = _day_bounds(end, tz_name)
    asked = (await session.execute(
        select(func.count()).select_from(CheckIn).where(
            CheckIn.user_id == user_id,
            CheckIn.asked_at >= s_start, CheckIn.asked_at < e_end,
        )
    )).scalar() or 0
    answered = (await session.execute(
        select(func.count()).select_from(CheckIn).where(
            CheckIn.user_id == user_id,
            CheckIn.asked_at >= s_start, CheckIn.asked_at < e_end,
            CheckIn.answer.isnot(None),
        )
    )).scalar() or 0
    return int(asked), int(answered)


async def day_logs_between(
    session: AsyncSession, user_id: int, start: date, end: date
) -> list[DayLog]:
    res = await session.execute(
        select(DayLog)
        .where(DayLog.user_id == user_id,
               DayLog.plan_date >= start, DayLog.plan_date <= end)
        .order_by(DayLog.plan_date)
    )
    return list(res.scalars().all())


async def recent_user_message_texts(
    session: AsyncSession, user_id: int, limit: int = 12
) -> list[str]:
    res = await session.execute(
        select(Message.content)
        .where(Message.user_id == user_id, Message.role == "user", Message.content != "")
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return [c for c in res.scalars().all() if c]


async def append_about_facts(
    session: AsyncSession, user_id: int, facts: list[str]
) -> list[str]:
    """Add durable facts, skipping near-duplicates. Returns the facts added."""
    prof = await get_or_create_profile(session, user_id)
    existing_lines = [ln.strip() for ln in (prof.about or "").splitlines() if ln.strip()]
    existing_low = {ln.lower() for ln in existing_lines}
    added: list[str] = []
    for fact in facts:
        f = (fact or "").strip()
        if f and f.lower() not in existing_low:
            existing_lines.append(f)
            existing_low.add(f.lower())
            added.append(f)
    if added:
        prof.about = "\n".join(existing_lines)
    return added


# ─── Goals ───────────────────────────────────────────────────────────────────
async def list_active_goals(session: AsyncSession, user_id: int) -> list[Goal]:
    res = await session.execute(
        select(Goal)
        .where(Goal.user_id == user_id, Goal.status == "active")
        .order_by(Goal.created_at)
    )
    return list(res.scalars().all())


async def add_goal(
    session: AsyncSession, user_id: int, title: str, detail: str | None = None
) -> Goal | None:
    """Insert a goal unless a similar active one already exists (fuzzy match)."""
    title = title.strip()
    existing = await list_active_goals(session, user_id)
    for g in existing:
        if _similar(g.title, title) >= 0.8:
            return None  # near-duplicate, skip
    goal = Goal(user_id=user_id, title=title, detail=detail)
    session.add(goal)
    await session.flush()
    return goal


async def _find_goal_fuzzy(
    session: AsyncSession, user_id: int, title: str
) -> Goal | None:
    res = await session.execute(select(Goal).where(Goal.user_id == user_id))
    best, best_score = None, 0.0
    for g in res.scalars().all():
        sc = _similar(g.title, title)
        if sc > best_score:
            best, best_score = g, sc
    return best if best is not None and best_score >= 0.6 else None


async def set_goal_status(
    session: AsyncSession, user_id: int, title: str, status: str
) -> bool:
    """Change a goal's status (never deletes). Matches by fuzzy title."""
    goal = await _find_goal_fuzzy(session, user_id, title)
    if goal is not None:
        goal.status = status
        return True
    return False


async def set_goal_deadline(
    session: AsyncSession, user_id: int, title: str, target_date: date
) -> bool:
    goal = await _find_goal_fuzzy(session, user_id, title)
    if goal is not None:
        goal.target_date = target_date
        return True
    return False


async def set_goal_progress(
    session: AsyncSession, user_id: int, title: str, percent: int
) -> bool:
    goal = await _find_goal_fuzzy(session, user_id, title)
    if goal is not None:
        goal.progress = max(0, min(100, percent))
        return True
    return False


# ─── Milestones (honest, derived goal progress) ──────────────────────────────
async def list_milestones(
    session: AsyncSession, user_id: int, goal_id: int
) -> list[Milestone]:
    res = await session.execute(
        select(Milestone)
        .where(Milestone.user_id == user_id, Milestone.goal_id == goal_id)
        .order_by(Milestone.position, Milestone.id)
    )
    return list(res.scalars().all())


def _milestone_stats(milestones: list[Milestone]) -> tuple[int, int]:
    done = sum(1 for m in milestones if m.status == "done")
    return done, len(milestones)


async def goal_progress_pct(
    session: AsyncSession, goal: Goal
) -> tuple[int | None, int, int]:
    """Derived progress from milestones (done/total). Falls back to the manual
    `goal.progress` only when there are no milestones. Returns (pct, done, total)."""
    ms = await list_milestones(session, goal.user_id, goal.id)
    done, total = _milestone_stats(ms)
    if total > 0:
        return round(done / total * 100), done, total
    return goal.progress, 0, 0


async def _recompute_goal_cache(session: AsyncSession, goal_id: int) -> tuple[int, int, int, str]:
    """Recompute and cache goal.progress from milestones. Returns (pct,done,total,title)."""
    goal = await session.get(Goal, goal_id)
    if goal is None:
        return 0, 0, 0, ""
    ms = await list_milestones(session, goal.user_id, goal_id)
    done, total = _milestone_stats(ms)
    pct = round(done / total * 100) if total else (goal.progress or 0)
    if total:
        goal.progress = pct
    return pct, done, total, goal.title


async def add_milestone(
    session: AsyncSession, user_id: int, goal_id: int, title: str, done: bool = False
) -> Milestone | None:
    """Add a step to a goal, unless a similar one already exists (fuzzy)."""
    existing = await list_milestones(session, user_id, goal_id)
    for m in existing:
        if _similar(m.title, title) >= 0.8:
            return None
    ms = Milestone(
        user_id=user_id, goal_id=goal_id, title=title.strip(),
        status="done" if done else "todo", position=len(existing),
    )
    session.add(ms)
    await session.flush()
    return ms


async def find_milestone_fuzzy(
    session: AsyncSession, user_id: int, title: str
) -> Milestone | None:
    res = await session.execute(
        select(Milestone).where(Milestone.user_id == user_id)
    )
    best, best_score = None, 0.0
    for m in res.scalars().all():
        sc = _similar(m.title, title)
        if sc > best_score:
            best, best_score = m, sc
    return best if best is not None and best_score >= 0.6 else None


async def set_milestone_status(
    session: AsyncSession, user_id: int, title: str, status: str
) -> Milestone | None:
    m = await find_milestone_fuzzy(session, user_id, title)
    if m is not None:
        m.status = status
        return m
    return None


# ─── Habits ──────────────────────────────────────────────────────────────────
async def list_active_habits(session: AsyncSession, user_id: int) -> list[Habit]:
    res = await session.execute(
        select(Habit)
        .where(Habit.user_id == user_id, Habit.active.is_(True))
        .order_by(Habit.schedule_time.nullslast(), Habit.id)
    )
    return list(res.scalars().all())


async def find_habit(
    session: AsyncSession, user_id: int, title: str
) -> Habit | None:
    res = await session.execute(
        select(Habit).where(
            Habit.user_id == user_id,
            func.lower(Habit.title) == title.strip().lower(),
        )
    )
    return res.scalar_one_or_none()


async def add_habit(
    session: AsyncSession,
    user_id: int,
    title: str,
    target_minutes: int | None = None,
    schedule_time: str | None = None,
) -> Habit | None:
    """Insert a habit unless one with the same title exists (then enrich it)."""
    existing = await find_habit(session, user_id, title)
    if existing is not None:
        if target_minutes and not existing.target_minutes:
            existing.target_minutes = target_minutes
        if schedule_time and not existing.schedule_time:
            existing.schedule_time = schedule_time
        if not existing.active:
            existing.active = True
        return None
    habit = Habit(
        user_id=user_id,
        title=title.strip(),
        target_minutes=target_minutes,
        schedule_time=schedule_time,
    )
    session.add(habit)
    await session.flush()
    return habit


async def log_habit(
    session: AsyncSession,
    user_id: int,
    habit_id: int,
    day: date,
    status: str = "done",
    minutes: int | None = None,
    note: str | None = None,
) -> HabitLog:
    """Idempotent per (habit, day): updates today's record if it exists."""
    res = await session.execute(
        select(HabitLog).where(
            HabitLog.user_id == user_id,
            HabitLog.habit_id == habit_id,
            HabitLog.log_date == day,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        row = HabitLog(
            user_id=user_id, habit_id=habit_id, log_date=day,
            status=status, minutes=minutes, note=note,
        )
        session.add(row)
        await session.flush()
    else:
        row.status = status
        if minutes is not None:
            row.minutes = minutes
        if note:
            row.note = note
    return row


async def habit_logs_for_day(
    session: AsyncSession, user_id: int, day: date
) -> dict[int, HabitLog]:
    res = await session.execute(
        select(HabitLog).where(
            HabitLog.user_id == user_id, HabitLog.log_date == day
        )
    )
    return {row.habit_id: row for row in res.scalars().all()}


async def habit_done_dates(
    session: AsyncSession, user_id: int, habit_id: int, since: date
) -> list[date]:
    res = await session.execute(
        select(HabitLog.log_date).where(
            HabitLog.user_id == user_id,
            HabitLog.habit_id == habit_id,
            HabitLog.status == "done",
            HabitLog.log_date >= since,
        )
    )
    return list(res.scalars().all())


# ─── Nudges (proactive messages) ─────────────────────────────────────────────
async def add_nudge(
    session: AsyncSession,
    user_id: int,
    kind: str,
    text: str,
    habit_id: int | None = None,
) -> Nudge:
    nudge = Nudge(user_id=user_id, kind=kind, text=text, habit_id=habit_id)
    session.add(nudge)
    await session.flush()
    return nudge


# ─── Metrics (countable things + gratification numbers) ──────────────────────
def _period_window(today: date, period: str) -> tuple[date, date]:
    """Inclusive [start, end] dates of the metric's current period (local)."""
    if period == "day":
        return today, today
    if period == "month":
        start = today.replace(day=1)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1)
        else:
            nxt = start.replace(month=start.month + 1)
        return start, nxt - timedelta(days=1)
    # default: week (Mon..Sun)
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


async def list_active_metrics(session: AsyncSession, user_id: int) -> list[Metric]:
    res = await session.execute(
        select(Metric)
        .where(Metric.user_id == user_id, Metric.active.is_(True))
        .order_by(Metric.id)
    )
    return list(res.scalars().all())


async def find_metric(
    session: AsyncSession, user_id: int, title: str
) -> Metric | None:
    res = await session.execute(
        select(Metric).where(Metric.user_id == user_id, Metric.active.is_(True))
    )
    best, best_score = None, 0.0
    for m in res.scalars().all():
        sc = _similar(m.title, title)
        if sc > best_score:
            best, best_score = m, sc
    return best if best is not None and best_score >= 0.7 else None


async def upsert_metric(
    session: AsyncSession, user_id: int, title: str,
    unit: str | None = None, kind: str | None = None,
    period: str | None = None, target: float | None = None,
) -> Metric:
    m = await find_metric(session, user_id, title)
    if m is None:
        m = Metric(
            user_id=user_id, title=title.strip(),
            unit=unit, kind=(kind or "counter"),
            period=(period or "week"), target=target,
        )
        session.add(m)
        await session.flush()
        return m
    if unit and not m.unit:
        m.unit = unit
    if kind:
        m.kind = kind
    if period:
        m.period = period
    if target is not None:
        m.target = target
    return m


async def add_metric_entry(
    session: AsyncSession, user_id: int, metric_id: int,
    amount: float, note: str | None = None,
) -> MetricEntry:
    e = MetricEntry(user_id=user_id, metric_id=metric_id, amount=amount, note=note)
    session.add(e)
    await session.flush()
    return e


async def _metric_sum(
    session: AsyncSession, metric_id: int, start: date, end: date, tz_name: str
) -> float:
    s_start, _ = _day_bounds(start, tz_name)
    _, e_end = _day_bounds(end, tz_name)
    res = await session.execute(
        select(func.coalesce(func.sum(MetricEntry.amount), 0.0)).where(
            MetricEntry.metric_id == metric_id,
            MetricEntry.created_at >= s_start,
            MetricEntry.created_at < e_end,
        )
    )
    return float(res.scalar() or 0.0)


async def metric_latest(session: AsyncSession, metric_id: int) -> list[MetricEntry]:
    res = await session.execute(
        select(MetricEntry)
        .where(MetricEntry.metric_id == metric_id)
        .order_by(MetricEntry.created_at.desc())
        .limit(2)
    )
    return list(res.scalars().all())


async def metric_progress(
    session: AsyncSession, metric: Metric, today: date, tz_name: str
) -> dict:
    """Single source of truth for a metric's live numbers + honest projection."""
    start, end = _period_window(today, metric.period)
    data: dict = {
        "title": metric.title, "unit": metric.unit or "",
        "kind": metric.kind, "period": metric.period, "target": metric.target,
    }
    if metric.kind == "gauge":
        rows = await metric_latest(session, metric.id)
        latest = rows[0].amount if rows else None
        prev = rows[1].amount if len(rows) > 1 else None
        data.update({
            "latest": latest,
            "delta": (latest - prev) if (latest is not None and prev is not None) else None,
        })
        return data

    today_sum = await _metric_sum(session, metric.id, today, today, tz_name)
    period_sum = await _metric_sum(session, metric.id, start, end, tz_name)
    days_total = (end - start).days + 1
    days_elapsed = max(1, (today - start).days + 1)
    projected = round(period_sum / days_elapsed * days_total) if period_sum else 0
    pct = None
    if metric.target:
        pct = int(round(period_sum / metric.target * 100))
    data.update({
        "today_sum": today_sum, "period_sum": period_sum,
        "days_total": days_total, "days_elapsed": days_elapsed,
        "projected": projected, "pct": pct,
        "period_start": start, "period_end": end,
    })
    return data


async def metric_daily_sums(
    session: AsyncSession, metric_id: int, start: date, end: date, tz_name: str
) -> list[float]:
    """Per-day totals from start..end inclusive (for report bar charts)."""
    out = []
    d = start
    while d <= end:
        out.append(await _metric_sum(session, metric_id, d, d, tz_name))
        d += timedelta(days=1)
    return out


async def user_message_hours(
    session: AsyncSession, user_id: int, tz_name: str, days: int = 14
) -> tuple[list[int], int, int]:
    """Per-local-hour counts of the user's own messages over `days`.
    Returns (counts[24], total, distinct_days)."""
    params = {"uid": user_id, "tz": tz_name, "days": days}
    rows = (await session.execute(text(
        "select extract(hour from (created_at at time zone :tz))::int as h, "
        "count(*) as n from messages "
        "where user_id = :uid and role = 'user' "
        "and created_at >= now() - make_interval(days => :days) "
        "group by 1"
    ), params)).all()
    counts = [0] * 24
    for r in rows:
        counts[int(r.h)] = int(r.n)

    agg = (await session.execute(text(
        "select count(distinct (created_at at time zone :tz)::date) as d, "
        "count(*) as total from messages "
        "where user_id = :uid and role = 'user' "
        "and created_at >= now() - make_interval(days => :days)"
    ), params)).first()
    return counts, int(agg.total or 0), int(agg.d or 0)


async def add_tool_event(
    session: AsyncSession, user_id: int, name: str, args: str | None, result: str | None
) -> None:
    session.add(ToolEvent(user_id=user_id, name=name, args=args, result=result))
    await session.flush()


async def nudge_sent_today(
    session: AsyncSession, user_id: int, kind: str, habit_id: int | None, day: date,
    tz_name: str,
) -> bool:
    start, end = _day_bounds(day, tz_name)
    stmt = select(func.count()).select_from(Nudge).where(
        Nudge.user_id == user_id,
        Nudge.kind == kind,
        Nudge.sent_at >= start,
        Nudge.sent_at < end,
    )
    if habit_id is not None:
        stmt = stmt.where(Nudge.habit_id == habit_id)
    res = await session.execute(stmt)
    return (res.scalar() or 0) > 0
