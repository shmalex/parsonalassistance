"""User-facing strings, sourced from the active locale (locales/<lang>.json,
see app/i18n.py). Names are kept stable so call sites don't change.
Strings with placeholders ({n}, {score}, {note}) are .format()-ed at call sites.
"""
from app.i18n import ui as _UI

START = _UI["start"]
HELP = _UI["help"]
NOT_ALLOWED = _UI["not_allowed"]
PAUSED = _UI["paused"]
RESUMED = _UI["resumed"]
INTERVAL_USAGE = _UI["interval_usage"]
INTERVAL_SET = _UI["interval_set"]
MOOD_USAGE = _UI["mood_usage"]
MOOD_SAVED = _UI["mood_saved"]
PROCESSING_VOICE = _UI["processing_voice"]
ERROR_GENERIC = _UI["error_generic"]
STATUS_EMPTY = _UI["status_empty"]
REFLECT_NO_DATA = _UI["reflect_no_data"]
REFLECT_KEPT = _UI["reflect_kept"]
REFLECT_APPLIED = _UI["reflect_applied"]
