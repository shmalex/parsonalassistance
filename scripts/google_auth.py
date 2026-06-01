"""One-time Google Calendar authorization (READ-ONLY).

Run this only when you're ready to connect your calendar:

    .venv/bin/python scripts/google_auth.py

Prerequisites:
  1. In Google Cloud Console create an OAuth client of type "Desktop app".
  2. Download its JSON and save it as  credentials.json  in this folder.
  3. Run this script. A browser window opens; approve read-only calendar access.
  4. It writes  token.json . The bot then reads your calendar automatically.

This requests calendar.readonly scope, so the bot can never modify or delete
your events.
"""
from __future__ import annotations

import os
import sys

# Make "app" importable when run from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402
from app.services.calendar_service import SCOPES  # noqa: E402


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    settings = get_settings()
    creds_file = settings.google_credentials_file
    token_file = settings.google_token_file

    if not os.path.exists(creds_file):
        print(f"❌ Не найден {creds_file}.")
        print("   Скачай OAuth client (Desktop app) из Google Cloud Console и")
        print(f"   сохрани как {creds_file} в папке проекта.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(token_file, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    print(f"✅ Готово. Создан {token_file}. Календарь подключён (только чтение).")


if __name__ == "__main__":
    main()
