# Project guide for Claude

Personal assistant Telegram bot. Russian voice mentor that keeps the user
focused, plans the day, tracks activities/mood, stores everything in Postgres,
and (optionally) reads Google Calendar.

> **Vision & conceptual critique:** `docs/concept.md` (in Russian) — what the
> system is missing conceptually (tracking ≠ behaviour change). Read it before
> adding features; the user endorsed it as the north star.
> **Architecture diagrams:** `docs/architecture.md` (Russian, Mermaid) — module
> map, message-processing sequence, proactive-tick decision, ER model, state
> diagrams. Update it when modules/tables change.
> **Narrative vision (non-technical):** `docs/thinking-bot.md` (Russian) — the
> "bot that thinks" story: bot's own task-journal (snooze-until-morning), an
> affordable cheap-model "inner voice" for judgment, strong model for speaking,
> hard facts computed in code (think vs compute) — "care but verify".

## Core product principle (the "nanny")
The bot is a **nanny**: it **cares, but verifies** (доверяй, но проверяй), and the
**user stays responsible** for their own actions. Practically: don't rubber-stamp
"I did X" — gently verify (what/how much/how) on meaningful claims, still record it,
keep the user accountable to what they committed; celebrate only real, verified
wins. This is the lens for every behavioural feature.

## Non-negotiable safety rules
1. **Never delete data.** No `DELETE`, no `DROP`, no destructive migrations.
   "Removing" is a status change (e.g. task `status="skipped"`), never a delete.
   `repository.py` intentionally has no delete helper.
2. **Never delete files.** Be additive. If a destructive or unusual filesystem
   action seems necessary, stop and ask the user first.
3. **Stay inside this folder** (`/home/shmalex/cheker/parsonalassistance`).
   Anything unusual outside it requires asking the user.
4. **Google Calendar is read-only** (`calendar.readonly` scope). The bot must
   never create, edit or delete calendar events.
5. **Secrets live in `.env`** (gitignored), never hard-coded. `.env` is loaded
   with `override=True`, so it is authoritative over ambient shell variables.

## Architecture
- `app/main.py` — entry point: init DB → start scheduler → poll Telegram.
- `app/config.py` — pydantic-settings; builds the async DB URL from `POSTGRES_*`
  or `DATABASE_URL`.
- `app/db/` — SQLAlchemy 2 async models + `init_db()` (CREATE only).
- `app/repository.py` — append-only data access (INSERT/UPDATE only).
- `app/services/openai_service.py` — Whisper transcription, GPT mentor chat,
  best-effort JSON signal extraction (all failures are swallowed, never crash).
- `app/services/calendar_service.py` — read-only Google Calendar, degrades to []
  when not configured.
- `app/bot/handlers.py` — aiogram 3 handlers (voice, text, commands).
- `app/scheduler.py` — 1-minute tick deciding per-user when to check in.

## Conventions
- Python 3.12, async throughout. Run via `.venv`.
- All user-facing text is Russian (`app/bot/text.py`, `app/prompts.py`).
- LLM/extraction code must be resilient: wrap external calls so a failure never
  breaks the chat or the scheduler.
- Verify changes with: `.venv/bin/python -m compileall -q app` and the import
  smoke test, before claiming done.

## Run
```bash
./run.sh          # or: .venv/bin/python -m app.main
```

## Known v1 limitations (improve later, non-destructively)
- "Today" buckets for activities/moods use UTC day bounds (minor edge near
  midnight). Tasks use a real `plan_date`.
- No Alembic yet; `create_all` is safe/additive. Add Alembic without dropping.
