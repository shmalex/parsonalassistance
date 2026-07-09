"""Tools the mentor can call during a conversation to take real actions
(change settings, manage habits/goals/tasks, record mood). This is what makes
the bot *act* on what the user says instead of only talking about it.

Every tool is additive or a status change — none deletes data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import repository as repo
from app.db.models import User
from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

# OpenAI function-tool schemas.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_timezone",
            "description": "Установить часовой пояс пользователя (IANA), когда он "
            "называет город/страну/пояс. Например America/New_York, Europe/Moscow.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "IANA tz id"}
                },
                "required": ["timezone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_active_hours",
            "description": "Установить активные часы, в которые можно беспокоить "
            "(вне их бот молчит). Время в формате HH:MM, местное.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "HH:MM"},
                    "end": {"type": "string", "description": "HH:MM"},
                },
                "required": ["start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_checkin_interval",
            "description": "Как часто (в минутах) проверяться 'чем занят'.",
            "parameters": {
                "type": "object",
                "properties": {"minutes": {"type": "integer"}},
                "required": ["minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_checkins",
            "description": "Поставить проверки на паузу или снять с паузы.",
            "parameters": {
                "type": "object",
                "properties": {"paused": {"type": "boolean"}},
                "required": ["paused"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_habit",
            "description": "Добавить или обновить регулярную привычку (спорт, "
            "чтение и т.п.) с длительностью и временем напоминания.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "target_minutes": {"type": "integer"},
                    "time": {"type": "string", "description": "HH:MM, время напоминания"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_habit",
            "description": "Отметить привычку выполненной сегодня.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "minutes": {"type": "integer"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_goal",
            "description": "Добавить долгосрочную цель.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_goal_status",
            "description": "Изменить статус цели: done (выполнена) или paused.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": ["done", "paused", "active"]},
                },
                "required": ["title", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_goal_deadline",
            "description": "Установить дедлайн (дату) у цели. Дата в формате YYYY-MM-DD.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["title", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_goal_progress",
            "description": "Обновить прогресс цели в процентах (0..100). Вызывай ТОЛЬКО "
            "когда человек прямо говорит о готовности именно этой цели. НЕ вычисляй "
            "процент из посторонней активности (приседания, шаги, страницы и т.п.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "percent": {"type": "integer"},
                },
                "required": ["title", "percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_milestone",
            "description": "Добавить ВЕХУ (конкретный проверяемый шаг) к долгосрочной цели. "
            "Разбивай большие цели на вехи — прогресс цели считается из выполненных вех. "
            "Ставь done=true, если человек говорит, что этот шаг УЖЕ сделан.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "к какой цели"},
                    "title": {"type": "string", "description": "шаг/веха"},
                    "done": {"type": "boolean", "description": "уже выполнено"},
                },
                "required": ["goal", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_milestone",
            "description": "Отметить веху цели выполненной, когда человек сообщает о "
            "достижении («открыл счёт», «настроил Stripe», «запустил бету»). Прогресс "
            "цели обновится сам. Если подходящей вехи нет — сперва add_milestone(done=true).",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Добавить задачу на сегодня.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "time": {"type": "string", "description": "HH:MM, необязательно"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_metric",
            "description": "Записать выполненное количество чего-то счётного, что "
            "НАКАПЛИВАЕТСЯ (приседания, отжимания, выпитая вода, помытая посуда и "
            "т.п.). Вызывай каждый раз, когда человек сообщает, что сделал N чего-то. "
            "Возвращает актуальные числа (сегодня/за период/прогресс/прогноз) — "
            "используй их для конкретной, честной похвалы.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "что считаем, напр. 'приседания'"},
                    "amount": {"type": "number", "description": "сколько добавить сейчас"},
                    "unit": {"type": "string", "description": "единица, напр. 'повт', 'стакан', 'раз'"},
                },
                "required": ["title", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_metric_target",
            "description": "Задать цель по счётной метрике на период (день/неделя/месяц), "
            "напр. 1000 приседаний в неделю.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "target": {"type": "number"},
                    "period": {"type": "string", "enum": ["day", "week", "month"]},
                    "unit": {"type": "string"},
                },
                "required": ["title", "target", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_measurement",
            "description": "Записать ИЗМЕРЕНИЕ, где важно последнее значение, а не сумма "
            "(вес, рост, давление). Возвращает последнее значение и изменение.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "напр. 'вес'"},
                    "value": {"type": "number"},
                    "unit": {"type": "string", "description": "напр. 'кг'"},
                },
                "required": ["title", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Отметить задачу выполненной, когда человек говорит, что "
            "сделал её — в том числе вчерашнюю или просроченную («сделал уборку гаража»).",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_task",
            "description": "Отметить задачу как пропущенную/неактуальную, когда человек "
            "решает её не делать или она потеряла смысл. Триггеры: «забей на X», "
            "«отмени X», «убери X», «X больше не актуально», «не буду делать X». "
            "ОБЯЗАТЕЛЬНО вызови этот инструмент, прежде чем сказать, что убрал задачу.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reopen_task",
            "description": "Вернуть задачу в работу (статус todo) — когда человек говорит, "
            "что задача НЕ сделана, или просит снять отметку «выполнено»/«пропущено» "
            "(«это ещё не сделано», «не закрывай её», «верни задачу»).",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_task",
            "description": "Исправить формулировку существующей задачи (ослышался, опечатка, "
            "уточнение), НЕ создавая новую. Пример: «задача называется не так, должно быть …».",
            "parameters": {
                "type": "object",
                "properties": {
                    "old_title": {"type": "string", "description": "текущее (неверное) название"},
                    "new_title": {"type": "string", "description": "правильное название"},
                },
                "required": ["old_title", "new_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_day_focus",
            "description": "Зафиксировать ОДНУ главную вещь на сегодня (утреннее "
            "обязательство) и при желании короткое намерение. Вызывай, когда человек "
            "называет свой главный приоритет дня.",
            "parameters": {
                "type": "object",
                "properties": {
                    "main_thing": {"type": "string", "description": "одна главная задача дня"},
                    "intention": {"type": "string", "description": "короткое намерение, необязательно"},
                },
                "required": ["main_thing"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_day_review",
            "description": "Записать вечерний разбор дня: получилось ли главное, "
            "рефлексия, что помешало (если не вышло), уровень энергии 1..5. Вызывай "
            "во время вечернего разбора, опираясь на ответы человека.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": ["done", "partial", "missed"]},
                    "reflection": {"type": "string"},
                    "blocker": {"type": "string", "description": "что помешало, если не вышло"},
                    "energy": {"type": "integer", "description": "1..5"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_mood",
            "description": "Записать настроение по шкале 1..5 с короткой заметкой.",
            "parameters": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["score"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_week_theme",
            "description": "Задать главный фокус (тему) на эту неделю, когда человек "
            "формулирует, на чём хочет сосредоточиться («на этой неделе главное — …»).",
            "parameters": {
                "type": "object",
                "properties": {"theme": {"type": "string"}},
                "required": ["theme"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remind_once",
            "description": "Записать РАЗОВОЕ напоминание/обещание вернуться к теме — оно "
            "РЕАЛЬНО сработает в указанное время. ОБЯЗАТЕЛЬНОЕ правило: любое твоё "
            "обещание «напомню», «напишу позже», «вернусь к этому», «спрошу вечером» "
            "ДОЛЖНО сопровождаться вызовом этого инструмента — обещание без вызова "
            "означает, что напоминание НЕ придёт и ты обманешь человека. Укажи либо "
            "in_minutes (через сколько минут), либо time (HH:MM, местное; если уже "
            "прошло — завтра), при желании с date (YYYY-MM-DD). В text пиши готовую "
            "реплику-напоминание от своего лица.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "что написать человеку, когда время придёт",
                    },
                    "in_minutes": {
                        "type": "integer",
                        "description": "через сколько минут (например, 20)",
                    },
                    "time": {"type": "string", "description": "HH:MM, местное время"},
                    "date": {"type": "string", "description": "YYYY-MM-DD, необязательно"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_reminder",
            "description": "Отменить ранее поставленное разовое напоминание, когда человек "
            "говорит, что оно больше не нужно («не надо напоминать про X»).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "про что было напоминание"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deactivate_habit",
            "description": "Выключить привычку (перестать напоминать), когда человек просит "
            "убрать/отменить её («убери медитацию», «больше не напоминай про чтение», "
            "«эта привычка не актуальна»). ОБЯЗАТЕЛЬНО вызови, прежде чем говорить, что "
            "убрал привычку. Это выключение, история сохраняется.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect",
            "description": "Запустить разбор недели (само-анализ), когда человек просит "
            "подумать/проанализировать/подвести итоги недели: «вызови рефлексию», "
            "«подумай, как ты со мной работаешь», «разбери неделю». Бот сам пришлёт "
            "разбор с кнопкой применить — отдельный текст после этого не нужен.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_card",
            "description": "Прислать пользователю КАРТИНКУ-карточку, когда он просит её "
            "словами или голосом (слэш-команды голосом сказать нельзя). Виды: "
            "dashboard — карточка дня; report — недельный отчёт по числам; streak — "
            "серии привычек; calendar — обратный отсчёт до цели; rhythm — график "
            "активности. Примеры триггеров: «покажи дашборд», «дай отчёт», «как мои "
            "серии», «сколько осталось до цели», «когда я активен».",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["dashboard", "report", "streak", "calendar", "rhythm"],
                    },
                },
                "required": ["kind"],
            },
        },
    },
]


def _valid_hhmm(value: str) -> str | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    h, _, m = value.partition(":")
    if h.isdigit() and m.isdigit() and 0 <= int(h) <= 23 and 0 <= int(m) <= 59:
        return f"{int(h):02d}:{int(m):02d}"
    return None


def _today(tz_name: str):
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:  # noqa: BLE001
        return datetime.utcnow().date()


def _valid_date(value: str):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


def _num(x) -> str:
    """Format a float without a trailing .0 for whole numbers."""
    try:
        f = float(x)
    except Exception:  # noqa: BLE001
        return str(x)
    return str(int(f)) if f == int(f) else f"{f:.1f}"


_PERIOD_RU = {"day": "сегодня", "week": "за неделю", "month": "за месяц"}


def _format_counter_result(added: float, prog: dict) -> str:
    unit = (" " + prog["unit"]) if prog.get("unit") else ""
    parts = [f"ок: +{_num(added)}{unit} «{prog['title']}»"]
    parts.append(f"сегодня {_num(prog['today_sum'])}")
    if prog.get("target"):
        parts.append(
            f"{_PERIOD_RU.get(prog['period'], 'за период')} "
            f"{_num(prog['period_sum'])} из {_num(prog['target'])} ({prog['pct']}%)"
        )
        parts.append(f"прогноз ~{_num(prog['projected'])} к концу периода")
    else:
        parts.append(f"{_PERIOD_RU.get(prog['period'], 'за период')} {_num(prog['period_sum'])}")
    return "; ".join(parts)


async def run_tool(user_id: int, name: str, args: dict) -> str:
    """Execute a tool call. Returns a short confirmation for the model + logs."""
    sm = get_sessionmaker()
    try:
        async with sm() as s:
            user = await s.get(User, user_id)
            if user is None:
                return "ошибка: пользователь не найден"

            if name == "set_timezone":
                tz = (args.get("timezone") or "").strip()
                try:
                    ZoneInfo(tz)
                except (ZoneInfoNotFoundError, Exception):  # noqa: BLE001
                    return f"ошибка: неизвестный часовой пояс '{tz}'"
                user.timezone = tz
                await s.commit()
                return f"ок: часовой пояс {tz}"

            if name == "set_active_hours":
                start = _valid_hhmm(args.get("start", ""))
                end = _valid_hhmm(args.get("end", ""))
                if not (start and end):
                    return "ошибка: время должно быть в формате HH:MM"
                user.active_from, user.active_to = start, end
                await s.commit()
                return f"ок: активные часы {start}–{end}"

            if name == "set_checkin_interval":
                mins = args.get("minutes")
                if not isinstance(mins, int) or not (1 <= mins <= 1440):
                    return "ошибка: интервал 1..1440 минут"
                user.checkin_interval_min = mins
                await s.commit()
                return f"ок: проверки каждые {mins} мин"

            if name == "pause_checkins":
                user.paused = bool(args.get("paused"))
                await s.commit()
                return "ок: проверки на паузе" if user.paused else "ок: проверки включены"

            if name == "upsert_habit":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название привычки"
                mins = args.get("target_minutes")
                mins = mins if isinstance(mins, int) and mins > 0 else None
                t = _valid_hhmm(args.get("time", "")) if args.get("time") else None
                created = await repo.add_habit(s, user_id, title, mins, t)
                await s.commit()
                when = f" в {t}" if t else ""
                dur = f" ({mins} мин)" if mins else ""
                verb = "добавлена" if created else "обновлена"
                return f"ок: привычка «{title}»{dur}{when} {verb}"

            if name == "complete_habit":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название привычки"
                habit = await repo.find_habit(s, user_id, title)
                if habit is None:
                    habit = await repo.add_habit(s, user_id, title)
                mins = args.get("minutes")
                mins = mins if isinstance(mins, int) and mins > 0 else None
                await repo.log_habit(
                    s, user_id, habit.id, _today(user.timezone),
                    status="done", minutes=mins,
                )
                await s.commit()
                return f"ок: привычка «{title}» отмечена выполненной сегодня"

            if name == "add_goal":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название цели"
                created = await repo.add_goal(s, user_id, title)
                await s.commit()
                return f"ок: цель «{title}» добавлена" if created \
                    else f"ок: похожая цель уже есть («{title}»)"

            if name == "set_goal_status":
                title = (args.get("title") or "").strip()
                status = args.get("status", "")
                if status not in ("done", "paused", "active"):
                    return "ошибка: статус done|paused|active"
                ok = await repo.set_goal_status(s, user_id, title, status)
                await s.commit()
                return f"ок: цель «{title}» → {status}" if ok \
                    else f"ошибка: цель «{title}» не найдена"

            if name == "set_goal_deadline":
                title = (args.get("title") or "").strip()
                d = _valid_date(args.get("date", ""))
                if not title or d is None:
                    return "ошибка: нужна цель и дата YYYY-MM-DD"
                ok = await repo.set_goal_deadline(s, user_id, title, d)
                await s.commit()
                return f"ок: дедлайн цели «{title}» — {d.isoformat()}" if ok \
                    else f"ошибка: цель «{title}» не найдена"

            if name == "set_goal_progress":
                title = (args.get("title") or "").strip()
                pct = args.get("percent")
                if not title or not isinstance(pct, int):
                    return "ошибка: нужна цель и процент 0..100"
                ok = await repo.set_goal_progress(s, user_id, title, pct)
                await s.commit()
                return f"ок: прогресс цели «{title}» — {max(0, min(100, pct))}%" if ok \
                    else f"ошибка: цель «{title}» не найдена"

            if name == "add_milestone":
                goal_title = (args.get("goal") or "").strip()
                title = (args.get("title") or "").strip()
                if not goal_title or not title:
                    return "ошибка: нужны цель и название вехи"
                goal = await repo._find_goal_fuzzy(s, user_id, goal_title)
                if goal is None:
                    return f"не нашёл цель «{goal_title}»"
                m = await repo.add_milestone(s, user_id, goal.id, title, done=bool(args.get("done")))
                pct, dn, tot, gt = await repo._recompute_goal_cache(s, goal.id)
                await s.commit()
                if m is None:
                    return f"веха «{title}» уже есть у «{gt}» ({pct}%, {dn}/{tot})"
                return f"ок: веха «{title}» добавлена к «{gt}» — {pct}% ({dn}/{tot})"

            if name == "complete_milestone":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название вехи"
                m = await repo.set_milestone_status(s, user_id, title, "done")
                if m is None:
                    return f"не нашёл веху «{title}»"
                pct, dn, tot, gt = await repo._recompute_goal_cache(s, m.goal_id)
                await s.commit()
                return f"ок: веха «{m.title}» выполнена — «{gt}» теперь {pct}% ({dn}/{tot})"

            if name == "add_task":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название задачи"
                t = _valid_hhmm(args.get("time", "")) if args.get("time") else None
                await repo.add_task(s, user_id, title, _today(user.timezone), t)
                await s.commit()
                return f"ок: задача «{title}» на сегодня добавлена"

            if name == "log_metric":
                title = (args.get("title") or "").strip()
                amount = args.get("amount")
                if not title or not isinstance(amount, (int, float)):
                    return "ошибка: нужны название и количество"
                unit = args.get("unit")
                unit = unit if isinstance(unit, str) and unit.strip() else None
                metric = await repo.upsert_metric(s, user_id, title, unit=unit, kind="counter")
                await repo.add_metric_entry(s, user_id, metric.id, float(amount))
                today = _today(user.timezone)
                prog = await repo.metric_progress(s, metric, today, user.timezone)
                await s.commit()
                return _format_counter_result(float(amount), prog)

            if name == "set_metric_target":
                title = (args.get("title") or "").strip()
                target = args.get("target")
                period = args.get("period")
                if not title or not isinstance(target, (int, float)) or period not in ("day", "week", "month"):
                    return "ошибка: нужны название, число и период day|week|month"
                unit = args.get("unit")
                unit = unit if isinstance(unit, str) and unit.strip() else None
                await repo.upsert_metric(
                    s, user_id, title, unit=unit, kind="counter",
                    period=period, target=float(target),
                )
                await s.commit()
                return f"ок: цель «{title}» — {_num(target)} {_PERIOD_RU.get(period, period)}"

            if name == "record_measurement":
                title = (args.get("title") or "").strip()
                value = args.get("value")
                if not title or not isinstance(value, (int, float)):
                    return "ошибка: нужны название и значение"
                unit = args.get("unit")
                unit = unit if isinstance(unit, str) and unit.strip() else None
                metric = await repo.upsert_metric(s, user_id, title, unit=unit, kind="gauge")
                await repo.add_metric_entry(s, user_id, metric.id, float(value))
                prog = await repo.metric_progress(s, metric, _today(user.timezone), user.timezone)
                await s.commit()
                u = (" " + unit) if unit else ""
                delta = prog.get("delta")
                tail = ""
                if delta is not None and delta != 0:
                    tail = f" ({'+' if delta > 0 else ''}{_num(delta)} с прошлого раза)"
                return f"ок: {title} {_num(value)}{u}{tail}"

            if name in ("complete_task", "skip_task"):
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название задачи"
                new_status = "done" if name == "complete_task" else "skipped"
                matched = await repo.set_task_status(s, user_id, title, new_status)
                await s.commit()
                if not matched:
                    return f"не нашёл открытую задачу «{title}»"
                word = "выполнена" if new_status == "done" else "пропущена"
                return f"ок: задача «{matched}» {word}"

            if name == "reopen_task":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название задачи"
                matched = await repo.reopen_task(s, user_id, title)
                await s.commit()
                return (f"ок: задача «{matched}» снова в работе (todo)" if matched
                        else f"не нашёл задачу «{title}»")

            if name == "rename_task":
                old = (args.get("old_title") or "").strip()
                new = (args.get("new_title") or "").strip()
                if not old or not new:
                    return "ошибка: нужны старое и новое название задачи"
                matched = await repo.rename_task(s, user_id, old, new)
                await s.commit()
                return (f"ок: переименовал задачу в «{matched}»" if matched
                        else f"не нашёл задачу «{old}»")

            if name == "set_week_theme":
                theme = (args.get("theme") or "").strip()
                if not theme:
                    return "ошибка: нужна формулировка темы недели"
                await repo.set_week_theme(s, user_id, theme, _today(user.timezone))
                await s.commit()
                return f"ок: тема недели — «{theme}»"

            if name == "set_day_focus":
                main = (args.get("main_thing") or "").strip()
                if not main:
                    return "ошибка: нужна главная вещь дня"
                intention = args.get("intention")
                intention = intention if isinstance(intention, str) and intention.strip() else None
                await repo.set_day_focus(s, user_id, _today(user.timezone), main, intention)
                await s.commit()
                return f"ок: главная вещь дня — «{main}»"

            if name == "log_day_review":
                outcome = args.get("outcome")
                outcome = outcome if outcome in ("done", "partial", "missed") else None
                reflection = args.get("reflection")
                reflection = reflection if isinstance(reflection, str) and reflection.strip() else None
                blocker = args.get("blocker")
                blocker = blocker if isinstance(blocker, str) and blocker.strip() else None
                energy = args.get("energy")
                energy = energy if isinstance(energy, int) and 1 <= energy <= 5 else None
                await repo.log_day_review(
                    s, user_id, _today(user.timezone),
                    outcome=outcome, reflection=reflection, blocker=blocker, energy=energy,
                )
                await s.commit()
                bits = [b for b in [outcome, f"энергия {energy}/5" if energy else None] if b]
                return "ок: разбор дня записан" + (f" ({', '.join(bits)})" if bits else "")

            if name == "remind_once":
                text = (args.get("text") or "").strip()
                if not text:
                    return "ошибка: нужен текст напоминания"
                try:
                    tz = ZoneInfo(user.timezone)
                except Exception:  # noqa: BLE001
                    tz = ZoneInfo("UTC")
                now_local = datetime.now(tz)
                due_local = None
                mins = args.get("in_minutes")
                if isinstance(mins, int) and mins > 0:
                    due_local = now_local + timedelta(minutes=mins)
                else:
                    t = _valid_hhmm(args.get("time", "")) if args.get("time") else None
                    d = _valid_date(args.get("date", "")) if args.get("date") else None
                    if t is None and d is None:
                        return ("ошибка: укажи in_minutes ИЛИ time (HH:MM), "
                                "иначе я не знаю, когда напомнить")
                    hh, mm = (int(x) for x in (t or "09:00").split(":"))
                    day = d or now_local.date()
                    due_local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
                    # "напомни в 18:30" after 18:30 means tomorrow, not the past.
                    if d is None and due_local <= now_local:
                        due_local += timedelta(days=1)
                if due_local <= now_local - timedelta(minutes=1):
                    return "ошибка: это время уже прошло"
                due_utc = due_local.astimezone(ZoneInfo("UTC"))
                await repo.add_commitment(s, user_id, text, due_utc)
                await s.commit()
                return (f"ок: напоминание поставлено на "
                        f"{due_local.strftime('%d.%m %H:%M')} (местное) — придёт")

            if name == "cancel_reminder":
                text = (args.get("text") or "").strip()
                if not text:
                    return "ошибка: скажи, какое напоминание отменить"
                cancelled = await repo.cancel_commitment(s, user_id, text)
                await s.commit()
                return (f"ок: напоминание «{cancelled}» отменено" if cancelled
                        else "не нашёл такого активного напоминания")

            if name == "deactivate_habit":
                title = (args.get("title") or "").strip()
                if not title:
                    return "ошибка: нужно название привычки"
                matched = await repo.deactivate_habit(s, user_id, title)
                await s.commit()
                return (f"ок: привычка «{matched}» выключена — больше не напоминаю"
                        if matched else f"не нашёл активную привычку «{title}»")

            if name == "record_mood":
                score = args.get("score")
                if not isinstance(score, int) or not (1 <= score <= 5):
                    return "ошибка: настроение 1..5"
                note = args.get("note")
                note = note if isinstance(note, str) and note.strip() else None
                await repo.add_mood(s, user_id, score, note=note)
                await s.commit()
                return f"ок: настроение {score}/5 записано"

            return f"ошибка: неизвестный инструмент {name}"
    except Exception:  # noqa: BLE001 — a tool failure must not crash the chat
        logger.exception("tool %s failed", name)
        return f"ошибка при выполнении {name}"
