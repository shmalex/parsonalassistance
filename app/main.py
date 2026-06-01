"""Entry point: wires up the bot, the database and the check-in scheduler."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.bot.handlers import router
from app.config import get_settings
from app.db.session import dispose_db, init_db
from app.logging_setup import setup_logging
from app.scheduler import build_scheduler
from app.services import calendar_service

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.telegram_bot_token or "put-your" in settings.telegram_bot_token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set in .env. Add your @BotFather token first."
        )
    if not settings.openai_api_key or "put-your" in settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set in .env.")

    logger.info("Connecting to database: %s", settings.db_label)
    await init_db()

    cal = "enabled" if calendar_service.is_configured() else "disabled (no token.json)"
    logger.info("Google Calendar: %s", cal)
    logger.info(
        "Check-in default: every %s min, active %s–%s, tz %s",
        settings.checkin_interval_min, settings.active_from,
        settings.active_to, settings.timezone,
    )

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = build_scheduler(bot, settings.timezone)
    scheduler.start()
    logger.info("Scheduler started. Bot is polling. Press Ctrl+C to stop.")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await dispose_db()
        logger.info("Stopped cleanly.")


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            raise
        logger.info("Bye.")


if __name__ == "__main__":
    main()
