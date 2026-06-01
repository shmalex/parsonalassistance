"""Read-only Google Calendar access.

Important: this module ONLY reads the calendar. It never creates, edits or
deletes events — your schedule stays intact.

Until you set up OAuth (credentials.json + token.json), every function returns
an empty result and the rest of the bot keeps working.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from app.config import get_settings

logger = logging.getLogger(__name__)

# Read-only scope — cannot modify the calendar even if asked to.
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def is_configured() -> bool:
    s = get_settings()
    return os.path.exists(s.google_token_file)


def _load_credentials():
    """Load stored OAuth creds, refreshing if needed. None if not set up."""
    s = get_settings()
    if not os.path.exists(s.google_token_file):
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_file(s.google_token_file, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(s.google_token_file, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        return creds
    except Exception:  # noqa: BLE001
        logger.exception("failed to load Google credentials")
        return None


def get_events_for_day(day: dt.date, tz_name: str) -> list[dict]:
    """Return today's events as [{summary, start, end}], or [] if unavailable.

    Synchronous (the google client is sync); call via run_in_executor.
    """
    creds = _load_credentials()
    if creds is None:
        return []
    try:
        from zoneinfo import ZoneInfo

        from googleapiclient.discovery import build

        tz = ZoneInfo(tz_name)
        start = dt.datetime.combine(day, dt.time.min, tzinfo=tz)
        end = start + dt.timedelta(days=1)

        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        result = (
            service.events()
            .list(
                calendarId=get_settings().google_calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = []
        for ev in result.get("items", []):
            events.append(
                {
                    "summary": ev.get("summary", "(без названия)"),
                    "start": ev.get("start", {}).get(
                        "dateTime", ev.get("start", {}).get("date", "")
                    ),
                    "end": ev.get("end", {}).get(
                        "dateTime", ev.get("end", {}).get("date", "")
                    ),
                }
            )
        return events
    except Exception:  # noqa: BLE001 — calendar must never crash the bot
        logger.exception("failed to read calendar events")
        return []


def format_events(events: list[dict]) -> str:
    if not events:
        return ""
    lines = []
    for ev in events:
        start = ev.get("start", "")
        # Show only HH:MM if it's a full datetime.
        if "T" in start:
            start = start[11:16]
        lines.append(f"• {start} {ev.get('summary', '')}".rstrip())
    return "\n".join(lines)
