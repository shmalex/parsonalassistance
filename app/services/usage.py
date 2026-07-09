"""Per-call OpenAI usage + cost accounting (best-effort).

Records ONE row per OpenAI API call (tokens + USD cost), attributing it to the
current user via a ``contextvars.ContextVar`` so we don't have to thread
``user_id`` through every function in ``openai_service``.

Set the user once at an entry point:
  * handlers — an outer aiogram middleware (covers all message/callback paths);
  * scheduler / reflection — ``with usage.attribute(user.id): ...``.

Every write here is fully wrapped: a failure in accounting can NEVER break the
chat or the scheduler tick. We open our OWN short-lived session (never the
caller's), so a recording error can't poison the user-facing transaction.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging

from app import pricing
from app import repository as repo
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

_current_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "usage_user_id", default=None
)


def set_user(uid: int | None):
    """Set the current user for attribution. Returns a token for ``reset_user``."""
    return _current_user_id.set(uid)


def reset_user(token) -> None:
    try:
        _current_user_id.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current_user_id() -> int | None:
    return _current_user_id.get()


@contextlib.contextmanager
def attribute(uid: int | None):
    """Attribute every OpenAI call made inside this block to ``uid``."""
    token = _current_user_id.set(uid)
    try:
        yield
    finally:
        reset_user(token)


def _usage_ints(usage_obj) -> tuple[int, int, int, int]:
    """(prompt, completion, total, cached) read defensively from an SDK usage obj.

    Handles ``usage`` being None, missing fields, and ``prompt_tokens_details``
    being absent on older models/SDKs.
    """
    if usage_obj is None:
        return 0, 0, 0, 0
    prompt = getattr(usage_obj, "prompt_tokens", 0) or 0
    completion = getattr(usage_obj, "completion_tokens", 0) or 0
    total = getattr(usage_obj, "total_tokens", 0) or (prompt + completion)
    details = getattr(usage_obj, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    return int(prompt), int(completion), int(total), int(cached)


async def record_chat(
    *, purpose: str, model: str, usage_obj=None,
    ok: bool = True, error: str | None = None, rounds: int | None = None,
) -> None:
    """Record one ``chat.completions`` call. Best-effort — never raises."""
    try:
        prompt, completion, total, cached = _usage_ints(usage_obj)
        cost, ver = pricing.chat_cost_usd(model, prompt, cached, completion)
        await _write(
            purpose=purpose, model=model, api="chat",
            prompt_tokens=prompt, completion_tokens=completion,
            total_tokens=total, cached_tokens=cached, audio_seconds=None,
            cost_usd=cost, price_version=ver, ok=ok, error=error, rounds=rounds,
        )
    except Exception:  # noqa: BLE001 — accounting must never break the caller
        logger.exception("usage.record_chat failed (ignored)")


async def record_transcription(
    *, purpose: str, model: str, audio_seconds: int | None,
    ok: bool = True, error: str | None = None,
) -> None:
    """Record one ``audio.transcriptions`` call (priced per minute)."""
    try:
        cost, ver = pricing.audio_cost_usd(model, audio_seconds)
        if ok and not audio_seconds and error is None:
            # Visibly unpriced rather than silently free (e.g. duration missing).
            error = "no_duration"
        await _write(
            purpose=purpose, model=model, api="transcription",
            prompt_tokens=0, completion_tokens=0, total_tokens=0, cached_tokens=0,
            audio_seconds=audio_seconds, cost_usd=cost, price_version=ver,
            ok=ok, error=error, rounds=None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("usage.record_transcription failed (ignored)")


async def _write(**fields) -> None:
    uid = current_user_id()
    sm = get_sessionmaker()
    try:
        async with sm() as session:
            await repo.record_openai_usage(session, user_id=uid, **fields)
            await session.commit()
    except Exception:  # noqa: BLE001
        # Never lose a cost row to an attribution/FK problem (e.g. the user's row
        # doesn't exist yet): record the call UNATTRIBUTED rather than dropping it.
        if uid is None:
            raise  # nothing more to try — let record_*'s guard log it
        async with sm() as session:
            await repo.record_openai_usage(session, user_id=None, **fields)
            await session.commit()
