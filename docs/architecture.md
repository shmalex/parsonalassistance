# Архитектура в диаграммах

Диаграммы того, как устроен проект и как модули работают между собой.
Все схемы — в формате **Mermaid** (текст в markdown).

> Как смотреть: в VS Code — расширение «Markdown Preview Mermaid Support»
> (или встроенный предпросмотр, если поддерживает Mermaid); на GitHub
> рендерится само; либо вставь блок в https://mermaid.live.

---

## 1. Модули и их связи

Как компоненты приложения взаимодействуют друг с другом и с внешними сервисами.

```mermaid
flowchart TD
    U["Пользователь (Telegram)"]

    subgraph APP["Приложение — Python, aiogram 3"]
        MAIN["main.py — точка входа"]
        MW["Middleware — лог входящих"]
        H["bot/handlers.py — команды, голос, текст"]
        SCH["scheduler.py — тик раз в минуту"]
        OAI["services/openai_service.py — чат, инструменты, Whisper"]
        TOOLS["services/tools.py — инструменты (действия)"]
        BRF["briefing.py — сбор данных для картинок"]
        CH["services/charts.py — дашборд, отчёт, heatmap, календарь"]
        CAL["services/calendar_service.py — чтение календаря"]
        REPO["repository.py — доступ к данным (только INSERT/UPDATE)"]
    end

    DB[("PostgreSQL")]
    OPENAI["OpenAI API — GPT-4o + Whisper"]
    GCAL["Google Calendar API — только чтение"]

    U -->|"голос / текст"| MW --> H
    MAIN --> H
    MAIN --> SCH
    H --> OAI
    OAI -->|"вызовы инструментов"| TOOLS
    H --> BRF --> CH
    BRF --> CAL
    H --> REPO
    TOOLS --> REPO
    SCH --> OAI
    SCH --> CH
    SCH --> REPO
    SCH -->|"проактивные сообщения"| U
    H -->|"ответ / картинка"| U
    OAI --> OPENAI
    CAL --> GCAL
    REPO --> DB
```

---

## 2. Поток обработки сообщения

Что происходит, когда приходит голосовое или текст — от расшифровки до ответа,
сохранения данных и обновления памяти.

```mermaid
sequenceDiagram
    actor U as Пользователь
    participant B as Bot (aiogram)
    participant H as Обработчик
    participant W as Whisper
    participant G as GPT-4o (ментор)
    participant T as Инструменты
    participant R as Репозиторий
    participant DB as PostgreSQL

    U->>B: голосовое / текст
    B->>H: update
    opt голосовое
        H->>W: транскрибация (ru)
        W-->>H: текст
    end
    H->>R: лог сообщения + сбор контекста
    R->>DB: select / insert
    R-->>H: история, досье, цели, привычки, метрики, план
    H->>G: ответ с инструментами (история + контекст)
    loop пока модель вызывает инструменты
        G-->>H: tool_call (напр. log_metric)
        H->>T: run_tool(name, args)
        T->>R: запись (метрика/цель/привычка/фокус…)
        R->>DB: insert / update
        T-->>H: результат + живые числа
        H->>R: аудит в tool_events
        H-->>G: результат инструмента
    end
    G-->>H: финальный ответ
    H->>U: ответ (конкретная похвала по числам)
    H->>G: извлечение активности (gpt-4o-mini)
    H->>G: обновление досье (gpt-4o-mini)
    H->>R: сохранить
```

---

## 3. Проактивный движок — что решает один тик планировщика

Раз в минуту для каждого пользователя. **Не больше одного** проактивного
сообщения за тик — приоритет сверху вниз.

```mermaid
flowchart TD
    T["Тик: раз в 1 минуту"] --> L["для каждого пользователя"]
    L --> P{"на паузе?"}
    P -->|да| SKIP["пропустить"]
    P -->|нет| AH{"в активных часах?"}
    AH -->|нет| SKIP
    AH -->|да| CM{"есть просроченное<br/>обязательство (remind_once)?"}
    CM -->|да| S0["отправить обещанное —<br/>МИМО governor: обещание держим всегда"]
    CM -->|нет| GV{"governor: молчание<br/>пользователя?"}
    GV -->|"≥10 дней"| FW["однократное прощание,<br/>дальше тишина до ответа"]
    GV -->|"5—9 дней: был пинг < 48ч"| SKIP
    GV -->|"3—4 дня: был пинг < 24ч"| SKIP
    GV -->|"пропускает"| MB{"утро: дашборд ещё не слан?"}
    MB -->|да| S1["отправить дашборд"]
    MB -->|нет| WR{"воскресенье: пора отчёт?"}
    WR -->|да| S2["недельный отчёт"]
    WR -->|нет| EV{"вечер: пора разбор?"}
    EV -->|да| S3["вечерний разбор дня"]
    EV -->|нет| HB{"привычка просрочена в окне?"}
    HB -->|да| S4["напоминание о привычке"]
    HB -->|нет| CI{"интервал проверки прошёл?"}
    CI -->|да| S5["проверка: чем занят?"]
    CI -->|нет| SKIP
```

**Тихий governor.** Чем дольше человек молчит, тем реже бот пишет первым:
до 3 дней — обычный ритм; 3—4 дня — не чаще 1 сообщения в сутки; 5—9 дней —
не чаще 1 сообщения в двое суток; с 10 дней — одно честное прощание
(«не буду писать первым, напиши когда захочешь») и полная тишина до ответа.
Любое входящее сообщение пользователя сбрасывает счётчик. Исключение —
обязательства из `remind_once`: то, что бот пообещал, он присылает всегда.

---

## 4. Модель данных

Таблицы и связи. Всё append-only: строки не удаляются, «удаление» — это смена
статуса.

```mermaid
erDiagram
    USERS ||--|| PROFILES : "досье"
    USERS ||--o{ MESSAGES : "1—N"
    USERS ||--o{ ACTIVITIES : "1—N"
    USERS ||--o{ MOODS : "1—N"
    USERS ||--o{ TASKS : "1—N"
    USERS ||--o{ GOALS : "1—N"
    USERS ||--o{ HABITS : "1—N"
    HABITS ||--o{ HABIT_LOGS : "1—N"
    USERS ||--o{ METRICS : "1—N"
    METRICS ||--o{ METRIC_ENTRIES : "1—N"
    USERS ||--o{ DAY_LOGS : "1—N"
    USERS ||--o{ CHECKINS : "1—N"
    USERS ||--o{ NUDGES : "1—N"
    USERS ||--o{ TOOL_EVENTS : "1—N"

    USERS {
        bigint id PK
        string timezone
        int checkin_interval_min
        string active_from
        string active_to
        bool paused
    }
    PROFILES {
        bigint user_id PK
        text about
        text summary "сверяемое досье"
    }
    GOALS {
        text title
        string status "active|done|paused"
        date target_date
        int progress "0..100"
    }
    HABITS {
        string title
        int target_minutes
        string schedule_time "HH:MM"
    }
    HABIT_LOGS {
        date log_date
        string status "done|skipped"
    }
    METRICS {
        string title
        string kind "counter|gauge"
        string period "day|week|month"
        float target
    }
    METRIC_ENTRIES {
        float amount
        datetime created_at
    }
    DAY_LOGS {
        date plan_date
        text main_thing "одна главная вещь"
        string outcome "done|partial|missed"
        text blocker "почему не вышло"
        int energy "1..5"
    }
    TOOL_EVENTS {
        string name
        text args
        text result
    }
```

---

## 5. Диаграммы состояний

### Ритуал дня (петля «обязательство → разбор»)

```mermaid
stateDiagram-v2
    state "Нет фокуса" as S0
    state "Зафиксирован" as S1
    state "Разобран" as S2
    [*] --> S0
    S0 --> S1 : set_day_focus (утро)
    S1 --> S2 : log_day_review (вечер)
    S2 --> [*]
```

### Жизненный цикл задачи

```mermaid
stateDiagram-v2
    state "к выполнению (todo)" as todo
    state "в работе (doing)" as doing
    state "сделано (done)" as done
    state "пропущено (skipped)" as skipped
    [*] --> todo : создана
    todo --> doing : начал
    doing --> done : завершил
    todo --> skipped : пропустил
    doing --> skipped : бросил
    done --> [*]
    skipped --> [*]
```

### Жизненный цикл цели

```mermaid
stateDiagram-v2
    state "активна (active)" as A
    state "выполнена (done)" as D
    state "отложена (paused)" as P
    [*] --> A
    A --> D : set_goal_status done
    A --> P : set_goal_status paused
    P --> A : вернулись к цели
    D --> [*]
```

---

## 6. Как «думает» наставник (петля инструментов)

Модель сама решает, какие действия выполнить, и подтверждает их человеку.

```mermaid
flowchart LR
    IN["сообщение + контекст<br/>(досье, цели, привычки, метрики)"] --> M["GPT-4o"]
    M -->|"нужно действие"| TC["tool_call"]
    TC --> EX["run_tool: запись в БД<br/>+ возврат живых чисел"]
    EX --> M
    M -->|"действий больше нет"| OUT["финальный ответ<br/>(похвала/совет по реальным числам)"]
```

> Связанные документы: концепция и слабые места — `docs/concept.md`;
> правила и соглашения — `CLAUDE.md`.
