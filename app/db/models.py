"""SQLAlchemy models.

Design rule for this whole project: data is append-only. We never DROP tables
and never DELETE rows. "Removing" something is done with a status flag, not a
DELETE. See CLAUDE.md.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    # Telegram numeric user id is the primary key.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    checkin_interval_min: Mapped[int] = mapped_column(Integer, default=30)
    active_from: Mapped[str] = mapped_column(String(5), default="09:00")
    active_to: Mapped[str] = mapped_column(String(5), default="22:00")
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set when Telegram says the user blocked the bot — stop proactive sends.
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)

    # Bookkeeping for the check-in scheduler.
    last_checkin_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_interaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="user")


class Message(Base):
    """Append-only log of everything said, in both directions."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # role: "user" | "assistant" | "system"
    role: Mapped[str] = mapped_column(String(16))
    # kind: "text" | "voice" | "checkin" | "system"
    kind: Mapped[str] = mapped_column(String(16), default="text")
    content: Mapped[str] = mapped_column(Text, default="")
    # For voice: the Telegram file id + the raw transcription we produced.
    voice_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transcription: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped["User"] = relationship(back_populates="messages")


class CheckIn(Base):
    """A scheduled "what are you doing right now?" ping and its answer."""

    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    asked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Activity(Base):
    """What the user reported doing, captured from the conversation."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 1..5 self-reported focus, optional.
    focus_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Mood(Base):
    __tablename__ = "moods"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # 1..5 scale.
    score: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Profile(Base):
    """Long-term memory about the user. One row per user (1:1)."""

    __tablename__ = "profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    # Durable facts, one per line (age, role, working style, preferences).
    about: Mapped[str] = mapped_column(Text, default="")
    # Rolling AI-maintained summary of who the user is and their current focus.
    summary: Mapped[str] = mapped_column(Text, default="")
    # Learned, bounded "how to work with THIS person" notes (the adaptation layer —
    # never the core/safety prompt). Maintained by the weekly reflection.
    playbook: Mapped[str] = mapped_column(Text, default="")
    # Current week's focus + when it was set.
    week_theme: Mapped[str | None] = mapped_column(Text, nullable=True)
    week_started: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Reflection(Base):
    """A weekly self-analysis: what worked, the proposed playbook + next theme.
    History/audit of the bot's self-improvement (versioned, revertable)."""

    __tablename__ = "reflections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    stats: Mapped[str | None] = mapped_column(Text, nullable=True)        # evidence snapshot
    retrospective: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_playbook: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_theme: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Goal(Base):
    """A longer-term goal (e.g. launch a startup)."""

    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "active" | "done" | "paused"  (status change, never delete)
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Optional deadline + self-reported progress, powering the countdown/% visuals.
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0..100
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Habit(Base):
    """A recurring daily practice the user wants to keep (sport, reading)."""

    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Preferred local time "HH:MM" for a dedicated reminder; null = no fixed time.
    schedule_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class HabitLog(Base):
    """A daily completion record for a habit."""

    __tablename__ = "habit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    # "done" | "skipped" | "partial"
    status: Mapped[str] = mapped_column(String(16), default="done")
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Nudge(Base):
    """A proactive message the bot sent on its own initiative (for dedupe/log)."""

    __tablename__ = "nudges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # "checkin" | "habit" | "review"
    kind: Mapped[str] = mapped_column(String(16))
    habit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class DayLog(Base):
    """One row per day: the morning commitment and the evening review.

    This is the loop-closing record: what was promised vs what happened and WHY
    — the raw material for modelling the user's "waves" and for learning what
    actually works.
    """

    __tablename__ = "day_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    # Morning commitment.
    main_thing: Mapped[str | None] = mapped_column(Text, nullable=True)
    intention: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Evening review.
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)  # done|partial|missed
    reflection: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocker: Mapped[str | None] = mapped_column(Text, nullable=True)
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Metric(Base):
    """A countable thing the user tracks for numbers + gratification.

    kind="counter": accumulates over a period (приседания → 1000/неделю).
    kind="gauge":   a measurement where the latest value matters (вес).
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="counter")  # counter|gauge
    period: Mapped[str] = mapped_column(String(8), default="week")    # day|week|month
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MetricEntry(Base):
    """An append-only logged amount for a metric (e.g. +20 приседаний)."""

    __tablename__ = "metric_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    metric_id: Mapped[int] = mapped_column(ForeignKey("metrics.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ToolEvent(Base):
    """Audit log of every tool the mentor invoked — queryable for diagnostics."""

    __tablename__ = "tool_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    args: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON string
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Task(Base):
    """A planned item for a given day."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(Text)
    # "todo" | "doing" | "done" | "skipped"  (we change status, never delete)
    status: Mapped[str] = mapped_column(String(16), default="todo")
    planned_time: Mapped[str | None] = mapped_column(String(16), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
