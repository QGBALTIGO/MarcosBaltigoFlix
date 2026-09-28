from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


MILESTONES = (10, 25, 50, 75, 90, 95, 99, 100)


def milestone_for(percent: float) -> int:
    reached = [m for m in MILESTONES if percent >= m]
    return max(reached, default=0)


@dataclass(slots=True)
class LiveMessage:
    chat_id: int
    message_id: int
    scope: str
    alerts: bool
    last_milestone: int


class Storage:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS live_messages (
                    chat_id INTEGER PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'br',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    alerts INTEGER NOT NULL DEFAULT 1,
                    last_milestone INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Migração idempotente para instalações criadas por versões anteriores.
            for sql in (
                "ALTER TABLE live_messages ADD COLUMN alerts INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE live_messages ADD COLUMN last_milestone INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    await db.execute(sql)
                except aiosqlite.OperationalError:
                    pass
            await db.commit()

    async def upsert_live(self, chat_id: int, message_id: int, scope: str, current_percent: float = 0) -> None:
        current_milestone = milestone_for(current_percent)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO live_messages(chat_id, message_id, scope, enabled, alerts, last_milestone)
                VALUES (?, ?, ?, 1, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    message_id=excluded.message_id,
                    scope=excluded.scope,
                    enabled=1,
                    alerts=1,
                    last_milestone=excluded.last_milestone,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (chat_id, message_id, scope.lower(), current_milestone),
            )
            await db.commit()

    async def disable_live(self, chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE live_messages SET enabled=0, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
                (chat_id,),
            )
            await db.commit()

    async def set_alerts(self, chat_id: int, enabled: bool) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE live_messages SET alerts=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
                (1 if enabled else 0, chat_id),
            )
            await db.commit()

    async def mark_milestone(self, chat_id: int, milestone: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE live_messages SET last_milestone=?, updated_at=CURRENT_TIMESTAMP WHERE chat_id=?",
                (milestone, chat_id),
            )
            await db.commit()

    async def delete_live(self, chat_id: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM live_messages WHERE chat_id=?", (chat_id,))
            await db.commit()

    async def list_live(self) -> list[LiveMessage]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT chat_id, message_id, scope, alerts, last_milestone FROM live_messages WHERE enabled=1"
            )
            rows = await cursor.fetchall()
            return [
                LiveMessage(
                    int(r["chat_id"]),
                    int(r["message_id"]),
                    str(r["scope"]),
                    bool(r["alerts"]),
                    int(r["last_milestone"]),
                )
                for r in rows
            ]
