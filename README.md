# Personal Assistant (Telegram + OpenAI)

Личный ассистент-наставник в Telegram. Принимает **голосовые на русском**,
расшифровывает их (OpenAI Whisper), отвечает как наставник (GPT), периодически
спрашивает «чем занят?», помогает планировать день и следит за настроением.
Всё сохраняется в **Postgres**. Может читать **Google Calendar** (только чтение).

> Принцип проекта: **данные и файлы только добавляются**. Бот никогда не удаляет
> строки в БД и не меняет/не удаляет события в календаре. См. `CLAUDE.md`.

## Возможности (v1)
- 🎙️ Голосовые и текст на русском → расшифровка и ответ.
- 🧭 Наставник: держит в фокусе, задаёт короткие вопросы, помогает с планом.
- ⏰ Авто-проверки каждые N минут («Чем занимаешься?») в активные часы.
- 🗃️ Хранение в Postgres: сообщения, проверки, активности, настроение, задачи.
- 📅 Google Calendar (read-only) — опционально, подключается позже.

## Стек
Python 3.12 · aiogram 3 · OpenAI (Whisper + GPT) · SQLAlchemy 2 (async) +
asyncpg · APScheduler · Google Calendar API (read-only).

## Быстрый старт

```bash
# 1) Зависимости уже стоят в .venv (Python 3.12). Если нужно пересоздать:
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2) Заполни .env (уже создан). Нужны минимум:
#    TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, и доступ к Postgres
#    (POSTGRES_USER/PASSWORD/DB/HOST/PORT или DATABASE_URL).

# 3) Запуск
./run.sh
#   или
.venv/bin/python -m app.main
```

При старте бот сам создаёт недостающие таблицы (только CREATE, ничего не
удаляя). В Telegram отправь `/start`.

## Команды бота
| Команда | Что делает |
|---|---|
| `/start` | регистрация и приветствие |
| `/plan` | помочь спланировать день |
| `/status` | сводка за сегодня (план, активности, настроение) |
| `/mood 4 заметка` | отметить настроение 1–5 |
| `/interval 15` | как часто проверяться (1–1440 мин) |
| `/pause` / `/resume` | выключить / включить авто-проверки |
| `/help` | помощь |

## Настройки (`.env`)
- `CHECKIN_INTERVAL_MIN` — частота проверок (по умолчанию 30; ты просил 5 —
  поставь `5`, либо в чате `/interval 5`).
- `TIMEZONE`, `ACTIVE_FROM`, `ACTIVE_TO` — часовой пояс и активные часы.
- `OPENAI_CHAT_MODEL` (`gpt-4o-mini` по умолчанию), `OPENAI_TRANSCRIBE_MODEL`
  (`whisper-1`).
- `ALLOWED_TELEGRAM_IDS` — ограничить бота своими Telegram id (через запятую).

## Подключение Google Calendar (когда будешь готов)
1. В Google Cloud Console создай OAuth client типа **Desktop app**.
2. Скачай JSON, сохрани как `credentials.json` в этой папке.
3. Запусти один раз:
   ```bash
   .venv/bin/python scripts/google_auth.py
   ```
   Откроется браузер, дай доступ. Появится `token.json` — календарь подключён
   (только чтение). До этого бот работает без календаря.

## Структура
```
app/
  config.py            настройки из .env
  main.py              точка входа (бот + БД + планировщик)
  logging_setup.py
  prompts.py           русские промпты наставника + извлечение данных
  db/
    models.py          таблицы (append-only)
    session.py         движок/сессии, init_db (только CREATE)
  repository.py        доступ к данным (только INSERT/UPDATE)
  services/
    openai_service.py  Whisper + GPT + извлечение сигналов
    calendar_service.py Google Calendar (только чтение)
  bot/
    handlers.py        команды, голос, текст
    text.py            русские строки интерфейса
  scheduler.py         периодические проверки
scripts/
  google_auth.py       разовая авторизация календаря
run.sh                 запуск
```

## Заметки
- Группировка «за сегодня» в v1 считается по UTC-границам суток — возможна
  небольшая неточность около полуночи. Будет уточнено в следующей версии.
- Миграций пока нет (используется безопасный `create_all`). Alembic — следующий
  шаг, без удаления данных.
