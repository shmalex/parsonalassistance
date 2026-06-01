"""Localisation loader.

All user-facing texts and LLM prompts live in JSON files under `locales/`.
`ru.json` is the canonical base. Choose a language with `LOCALE` in .env (e.g.
LOCALE=en → locales/en.json) or point `LOCALE_FILE` at an explicit path. Any keys
missing from the chosen locale fall back to ru, so partial translations are fine.

To add a language: copy `locales/ru.json` → `locales/<code>.json`, translate the
values (keep placeholders like {n}, {hint}, {habit_title} intact), set LOCALE=<code>.
"""
from __future__ import annotations

import functools
import json
import logging
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)

LOCALES_DIR = PROJECT_ROOT / "locales"
BASE_LOCALE = "ru"


def _deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _resolve_path() -> Path | None:
    """Path of the locale to overlay on top of ru (None = ru only)."""
    settings = get_settings()
    if settings.locale_file:
        p = Path(settings.locale_file)
        return p if p.is_absolute() else PROJECT_ROOT / settings.locale_file
    if settings.locale and settings.locale != BASE_LOCALE:
        return LOCALES_DIR / f"{settings.locale}.json"
    return None


@functools.lru_cache(maxsize=1)
def load_locale() -> dict:
    data = json.loads((LOCALES_DIR / f"{BASE_LOCALE}.json").read_text(encoding="utf-8"))
    path = _resolve_path()
    if path is not None:
        if path.exists():
            try:
                _deep_merge(data, json.loads(path.read_text(encoding="utf-8")))
                logger.info("Locale loaded: %s (over %s)", path.name, BASE_LOCALE)
            except Exception:  # noqa: BLE001 — bad locale file must not crash the bot
                logger.exception("failed to load locale %s; using %s", path, BASE_LOCALE)
        else:
            logger.warning("Locale file %s not found; using %s", path, BASE_LOCALE)
    return data


_LOCALE = load_locale()
prompts: dict = _LOCALE.get("prompts", {})
ui: dict = _LOCALE.get("ui", {})
