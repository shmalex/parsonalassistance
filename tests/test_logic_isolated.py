"""Isolated logic test for the new commitments journal + silence governor.

Runs against an in-memory SQLite DB (never touches prod Postgres). Monkeypatches
get_sessionmaker so repo/scheduler/tools code runs unmodified.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/shmalex/cheker/parsonalassistance")

from sqlalchemy import BigInteger, Integer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import session as db_session
from app.db.models import Base, User
from app import repository as repo

# SQLite autoincrements only INTEGER PRIMARY KEY, not BIGINT. Prod (Postgres)
# uses BIGSERIAL and is unaffected; here we swap the type for the test run only.
for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if _col.primary_key and _col.autoincrement is True \
                and isinstance(_col.type, BigInteger):
            _col.type = Integer()


async def main():
    eng = create_async_engine("sqlite+aiosqlite://")
    try:
        await _run(eng)
    finally:
        await eng.dispose()  # otherwise the aiosqlite thread keeps the process alive


async def _run(eng):
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    db_session.get_sessionmaker = lambda: sm  # patch the factory

    now = datetime.now(timezone.utc)

    # ── users with different silence ages ────────────────────────────────────
    async with sm() as s:
        for uid, days in [(1, 0), (2, 3), (3, 5), (4, 10)]:
            s.add(User(id=uid, timezone="Europe/Moscow",
                       last_interaction_at=now - timedelta(days=days)))
        await s.commit()

    # ── commitments: add → not due → due → sent ─────────────────────────────
    async with sm() as s:
        await repo.add_commitment(s, 1, "Напоминаю: выключи свет!",
                                  now + timedelta(minutes=20))
        await repo.add_commitment(s, 1, "Как прошло демо?", now - timedelta(minutes=1))
        await s.commit()
    async with sm() as s:
        due = await repo.due_commitments(s, 1, now)
        assert [c.text for c in due] == ["Как прошло демо?"], due
        pending = await repo.pending_commitments(s, 1)
        assert len(pending) == 2
        await repo.mark_commitment_sent(s, due[0])
        await s.commit()
    async with sm() as s:
        assert await repo.due_commitments(s, 1, now) == []
        cancelled = await repo.cancel_commitment(s, 1, "про свет")
        assert cancelled == "Напоминаю: выключи свет!", cancelled
        assert await repo.pending_commitments(s, 1) == []
        assert await repo.cancel_commitment(s, 1, "несуществующее") is None
        await s.commit()
    print("✓ журнал обязательств: add / due / sent / cancel")

    # ── phantom-answer window ────────────────────────────────────────────────
    async with sm() as s:
        old = await repo.create_checkin(s, 2, "Чем занят?")
        old.asked_at = now - timedelta(days=2)
        await s.commit()
    async with sm() as s:
        cutoff = now - timedelta(minutes=90)
        assert await repo.latest_unanswered_checkin(s, 2, asked_after=cutoff) is None
        assert (await repo.latest_unanswered_checkin(s, 2)) is not None  # legacy path
    print("✓ окно ответа на чекин: старый чекин не «отвечается» новым сообщением")

    # ── governor decision matrix ─────────────────────────────────────────────
    sent_messages: list[tuple[int, str]] = []

    class FakeBot:
        async def send_message(self, uid, text):
            sent_messages.append((uid, text))

    import app.scheduler as sched
    sched.get_sessionmaker = lambda: sm  # scheduler imported its own reference

    async def gate(uid):
        async with sm() as s:
            u = await s.get(User, uid)
        return await sched._governor_allows(FakeBot(), u, now)

    assert await gate(1) is True                       # active user → normal
    # 3 days silent, no nudges yet → allowed (first of the day)
    assert await gate(2) is True
    async with sm() as s:                              # nudge 2h ago → blocked
        n = await repo.add_nudge(s, 2, "checkin", "joke")
        n.sent_at = now - timedelta(hours=2)
        await s.commit()
    assert await gate(2) is False
    async with sm() as s:                              # nudge 25h ago → allowed
        from app.db.models import Nudge
        from sqlalchemy import update
        await s.execute(update(Nudge).where(Nudge.user_id == 2)
                        .values(sent_at=now - timedelta(hours=25)))
        await s.commit()
    assert await gate(2) is True
    # 5 days silent: 25h-old nudge NOT enough (needs 48h)
    async with sm() as s:
        n = await repo.add_nudge(s, 3, "briefing", "morning")
        n.sent_at = now - timedelta(hours=25)
        await s.commit()
    assert await gate(3) is False
    async with sm() as s:
        from app.db.models import Nudge
        from sqlalchemy import update
        await s.execute(update(Nudge).where(Nudge.user_id == 3)
                        .values(sent_at=now - timedelta(hours=49)))
        await s.commit()
    assert await gate(3) is True
    # 10 days silent → farewell once, then permanent silence
    assert await gate(4) is False
    assert len(sent_messages) == 1 and sent_messages[0][0] == 4
    assert "не буду писать первым" in sent_messages[0][1]
    assert await gate(4) is False                      # second call: no repeat
    assert len(sent_messages) == 1
    print("✓ governor: 0д=норм, 3д=1/сутки, 5д=1/2суток, 10д=прощание однократно")

    # ── commitments fire for a silent user (bypass governor) ────────────────
    async with sm() as s:
        await repo.add_commitment(s, 4, "Ты просил напомнить про резюме.",
                                  now - timedelta(minutes=5))
        await s.commit()
    fired = await sched._send_due_commitments(FakeBot(),
        await (lambda: sm())().__aenter__() and None or None, now) \
        if False else None
    # call properly:
    async with sm() as s:
        u4 = await s.get(User, 4)
    fired = await sched._send_due_commitments(FakeBot(), u4, now)
    assert fired is True
    assert sent_messages[-1] == (4, "Ты просил напомнить про резюме.")
    async with sm() as s:
        assert await repo.pending_commitments(s, 4) == []
    print("✓ обязательство сработало даже у молчащего пользователя (мимо governor)")

    # ── deactivate_habit ─────────────────────────────────────────────────────
    async with sm() as s:
        await repo.add_habit(s, 1, "медитация", 10, "20:00")
        await s.commit()
    async with sm() as s:
        assert await repo.deactivate_habit(s, 1, "Медитация") == "медитация"
        await s.commit()
    async with sm() as s:
        assert await repo.list_active_habits(s, 1) == []
        assert await repo.deactivate_habit(s, 1, "медитация") is None  # already off
    print("✓ deactivate_habit: выключает и не находит уже выключенную")

    print("\nALL LOGIC TESTS PASSED")


def test_logic_isolated():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
