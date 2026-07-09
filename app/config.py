"""Application settings, loaded from .env (never hard-coded).

The project's .env is treated as authoritative: it is loaded with override=True
so a stale ambient shell variable (e.g. an old OPENAI_API_KEY exported in your
profile) cannot silently shadow what you put in .env.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
# Load .env into the environment, overriding any pre-existing shell variables.
load_dotenv(ENV_FILE, override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Telegram ────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(default="")
    # Comma-separated numeric ids; empty = allow everyone.
    allowed_telegram_ids: str = Field(default="")
    # The bot's OWNER (operator). The owner may always see GLOBAL (all-users)
    # /cost spend. When unset, only a single-user allowlist auto-grants that lone
    # user (a multi-user allowlist needs an owner/viewer set explicitly).
    owner_telegram_id: int | None = Field(default=None)
    # Additional NON-owner Telegram ids (comma-separated) allowed to VIEW global
    # /cost spend — e.g. a trusted helper. They can see costs but aren't owners.
    cost_viewer_ids: str = Field(default="")

    # ─── OpenAI ──────────────────────────────────────────────────────────────
    openai_api_key: str = Field(default="")
    # Strong model for the mentor conversation + tool calls (better reasoning).
    openai_chat_model: str = Field(default="gpt-4o")
    # Cheap model for background utility calls (extraction, summary, nudges).
    openai_util_model: str = Field(default="gpt-4o-mini")
    openai_transcribe_model: str = Field(default="whisper-1")

    # ─── Database ────────────────────────────────────────────────────────────
    # Either a full async URL...
    database_url: str | None = Field(default=None)
    # ...or these five parts (used to build the URL when database_url is unset).
    postgres_user: str | None = None
    postgres_password: str | None = None
    postgres_db: str | None = None
    postgres_host: str | None = None
    postgres_port: int | None = None

    # ─── Behaviour ───────────────────────────────────────────────────────────
    checkin_interval_min: int = 30
    timezone: str = "Europe/Moscow"
    active_from: str = "09:00"
    active_to: str = "22:00"

    # ─── Localisation ──────────────────────────────────────────────────────────
    # Language of all texts & prompts: name of a file in locales/ (ru, en, …).
    locale: str = "ru"
    # Or an explicit path to a locale JSON (overrides `locale` if set).
    locale_file: str | None = None

    # ─── Google Calendar (optional) ──────────────────────────────────────────
    google_credentials_file: str = "credentials.json"
    google_token_file: str = "token.json"
    google_calendar_id: str = "primary"

    # ─── Logging ─────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @property
    def allowed_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.allowed_telegram_ids.split(","):
            part = part.strip()
            if part:
                try:
                    out.add(int(part))
                except ValueError:
                    pass
        return out

    @property
    def cost_viewers(self) -> set[int]:
        """Telegram ids allowed to see GLOBAL /cost: the owner + any explicit
        non-owner viewers."""
        out: set[int] = set()
        if self.owner_telegram_id:
            out.add(self.owner_telegram_id)
        for part in self.cost_viewer_ids.split(","):
            part = part.strip()
            if part:
                try:
                    out.add(int(part))
                except ValueError:
                    pass
        return out

    @property
    def sqlalchemy_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver)."""
        if self.database_url:
            url = self.database_url
            # Normalise to the async driver if a sync URL was supplied.
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        if not all([self.postgres_user, self.postgres_password, self.postgres_db,
                    self.postgres_host, self.postgres_port]):
            raise RuntimeError(
                "Database is not configured. Set DATABASE_URL or all of "
                "POSTGRES_USER/PASSWORD/DB/HOST/PORT in .env."
            )
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def db_label(self) -> str:
        """Safe-to-log description of the DB target (no password)."""
        if self.database_url:
            return "DATABASE_URL"
        return f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
