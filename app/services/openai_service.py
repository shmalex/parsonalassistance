"""Thin async wrapper around the OpenAI API: voice transcription + mentor chat +
best-effort structured extraction.
"""
from __future__ import annotations

import io
import json
import logging

from openai import AsyncOpenAI

from app.config import get_settings
from app.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    MENTOR_SYSTEM_PROMPT,
    REFLECTION_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    build_checkin_user_prompt,
    build_habit_nudge_prompt,
    build_review_prompt,
)
from app.services.tools import TOOLS

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _client_instance() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=get_settings().openai_api_key)
    return _client


async def transcribe_voice(audio_bytes: bytes, filename: str = "voice.oga") -> str:
    """Transcribe a Telegram voice note (OGG/Opus) with Whisper. Russian."""
    settings = get_settings()
    buf = io.BytesIO(audio_bytes)
    buf.name = filename  # the SDK uses the name to infer the format
    resp = await _client_instance().audio.transcriptions.create(
        model=settings.openai_transcribe_model,
        file=buf,
        language="ru",
    )
    return (resp.text or "").strip()


async def mentor_reply(
    history: list[dict[str, str]], context_block: str | None = None
) -> str:
    """history is a list of {"role": ..., "content": ...} in chronological order."""
    settings = get_settings()
    system = MENTOR_SYSTEM_PROMPT
    if context_block:
        system += "\n\n# Текущий контекст\n" + context_block
    messages = [{"role": "system", "content": system}, *history]
    resp = await _client_instance().chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        temperature=0.6,
        max_tokens=400,
    )
    return (resp.choices[0].message.content or "").strip()


async def mentor_reply_with_tools(
    history: list[dict[str, str]],
    context_block: str | None,
    executor,
    max_rounds: int = 4,
) -> str:
    """Mentor reply with tool calling. `executor(name, args) -> str` runs a tool
    and returns its result text. Loops until the model produces a final answer.
    """
    settings = get_settings()
    system = MENTOR_SYSTEM_PROMPT
    if context_block:
        system += "\n\n# Текущий контекст\n" + context_block
    messages: list = [{"role": "system", "content": system}, *history]
    client = _client_instance()

    for _ in range(max_rounds):
        resp = await client.chat.completions.create(
            model=settings.openai_chat_model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.6,
            max_tokens=500,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip()

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            result = await executor(tc.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    # Ran out of tool rounds — get a final text answer without tools.
    resp = await client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=messages,
        temperature=0.6,
        max_tokens=400,
    )
    return (resp.choices[0].message.content or "").strip()


async def generate_checkin_text(context_hint: str | None = None) -> str:
    settings = get_settings()
    try:
        resp = await _client_instance().chat.completions.create(
            model=settings.openai_util_model,
            messages=[
                {"role": "system", "content": MENTOR_SYSTEM_PROMPT},
                {"role": "user", "content": build_checkin_user_prompt(context_hint)},
            ],
            temperature=0.9,
            max_tokens=120,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001 — never let a check-in fail on the API
        logger.exception("check-in text generation failed; using fallback")
    return "Чем сейчас занимаешься? На чём сфокусирован?"


async def generate_habit_nudge_text(
    habit_title: str, target_minutes: int | None, context_hint: str | None = None
) -> str:
    settings = get_settings()
    try:
        resp = await _client_instance().chat.completions.create(
            model=settings.openai_util_model,
            messages=[
                {"role": "system", "content": MENTOR_SYSTEM_PROMPT},
                {"role": "user", "content": build_habit_nudge_prompt(
                    habit_title, target_minutes, context_hint)},
            ],
            temperature=0.9,
            max_tokens=120,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001
        logger.exception("habit nudge text generation failed; using fallback")
    return f"Пора уделить время привычке «{habit_title}». Начнём прямо сейчас? 💪"


async def generate_review_text(
    main_thing: str | None, undone_habits: list[str]
) -> str:
    settings = get_settings()
    try:
        resp = await _client_instance().chat.completions.create(
            model=settings.openai_util_model,
            messages=[
                {"role": "system", "content": MENTOR_SYSTEM_PROMPT},
                {"role": "user", "content": build_review_prompt(main_thing, undone_habits)},
            ],
            temperature=0.8,
            max_tokens=140,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001
        logger.exception("review text generation failed; using fallback")
    return "Давай подведём день. Что сегодня получилось, а что нет — и почему?"


async def update_profile_summary(current_summary: str, recent_text: str) -> str:
    """Return an updated rolling profile summary. Empty string on failure."""
    settings = get_settings()
    try:
        user_content = (
            f"Текущее досье:\n{current_summary or '(пусто)'}\n\n"
            f"Недавние реплики:\n{recent_text}"
        )
        resp = await _client_instance().chat.completions.create(
            model=settings.openai_util_model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001
        logger.exception("profile summary update failed")
        return ""


async def reflect(evidence: str, current_playbook: str, current_theme: str | None) -> dict:
    """Weekly self-analysis. Returns {retrospective, playbook, week_theme} or {}.
    Uses the strong chat model (reasoning matters; runs ~weekly)."""
    settings = get_settings()
    try:
        user_content = (
            f"Текущий плейбук:\n{current_playbook or '(пусто)'}\n\n"
            f"Тема недели: {current_theme or 'не задана'}\n\n"
            f"Данные за неделю:\n{evidence}"
        )
        resp = await _client_instance().chat.completions.create(
            model=settings.openai_chat_model,
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — reflection must never crash the bot
        logger.exception("reflection failed")
        return {}


async def extract_signals(text: str) -> dict:
    """Best-effort structured extraction. Returns {} on any problem."""
    if not text or not text.strip():
        return {}
    settings = get_settings()
    try:
        resp = await _client_instance().chat.completions.create(
            model=settings.openai_util_model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — extraction is optional
        logger.exception("signal extraction failed; ignoring")
        return {}
