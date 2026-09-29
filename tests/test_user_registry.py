import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.bot import ElectionBot
from app.config import Settings
from app.storage import Storage


def settings() -> Settings:
    return Settings(
        telegram_bot_token="test-token",
        election_mode="official",
        tse_base_url="https://resultados.tse.jus.br",
        tse_environment="oficial",
        tse_election_code=6257,
        tse_state_election_code=6259,
        tse_cycle="ele2026",
        tse_president_cargo="0001",
        poll_seconds=20,
        request_timeout=10,
        database_path=":memory:",
        admin_ids=set(),
        port=8000,
        webapp_url="https://example.test/app",
        channel_id="",
    )


def test_storage_records_unique_user_ids_and_updates_interactions(tmp_path: Path):
    async def scenario():
        db_path = tmp_path / "users.sqlite3"
        storage = Storage(str(db_path))
        await storage.init()

        await storage.record_user(1001)
        await storage.record_user(1002)
        await storage.record_user(1001)

        assert await storage.count_users() == 2
        assert await storage.list_user_ids() == [1001, 1002]

    asyncio.run(scenario())


def test_bot_record_user_saves_only_telegram_user_id():
    class StorageStub:
        def __init__(self):
            self.ids = []

        async def record_user(self, user_id):
            self.ids.append(user_id)

    async def scenario():
        storage = StorageStub()
        bot = ElectionBot(
            settings(),
            SimpleNamespace(),
            storage,
            SimpleNamespace(),
        )
        update = SimpleNamespace(effective_user=SimpleNamespace(id=778899))
        await bot._record_user(update)
        assert storage.ids == [778899]

    asyncio.run(scenario())


def test_bot_record_user_ignores_updates_without_user():
    class StorageStub:
        async def record_user(self, user_id):
            raise AssertionError("should not be called")

    async def scenario():
        bot = ElectionBot(
            settings(),
            SimpleNamespace(),
            StorageStub(),
            SimpleNamespace(),
        )
        update = SimpleNamespace(effective_user=None)
        await bot._record_user(update)

    asyncio.run(scenario())
