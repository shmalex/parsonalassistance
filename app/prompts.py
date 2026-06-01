"""Mentor persona + structured-extraction prompts.

The actual text lives in the active locale file (locales/<lang>.json, see
app/i18n.py). This module only exposes the prompts and the small builders that
assemble templated fragments — so adding a language never touches code.
"""
from __future__ import annotations

from app.i18n import prompts as _P

MENTOR_SYSTEM_PROMPT = _P["mentor_system"]
EXTRACTION_SYSTEM_PROMPT = _P["extraction_system"]
SUMMARY_SYSTEM_PROMPT = _P["summary_system"]
REFLECTION_SYSTEM_PROMPT = _P["reflection_system"]


def build_checkin_user_prompt(context_hint: str | None) -> str:
    """Instruction used to generate the periodic check-in question text."""
    base = _P["checkin_base"]
    if context_hint:
        base += _P["checkin_context_suffix"].format(hint=context_hint)
    return base


def build_review_prompt(main_thing: str | None, undone_habits: list[str]) -> str:
    """Instruction to open the evening review."""
    if main_thing:
        base = _P["review_with_main"].format(main_thing=main_thing)
    else:
        base = _P["review_no_main"]
    if undone_habits:
        base += _P["review_undone_suffix"].format(habits=", ".join(undone_habits))
    base += _P["review_tail"]
    return base


def build_habit_nudge_prompt(habit_title: str, target_minutes: int | None,
                             context_hint: str | None) -> str:
    dur = _P["habit_nudge_dur"].format(minutes=target_minutes) if target_minutes else ""
    base = _P["habit_nudge_base"].format(habit_title=habit_title, dur=dur)
    if context_hint:
        base += _P["habit_nudge_context_suffix"].format(hint=context_hint)
    return base
