from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from app.bot import ElectionBot
from app.channel_posts import publish_president_channel_update
from app.config import Settings
from app.exterior import (
    format_exterior_channel_message,
    parse_exame_exterior,
    parse_poder360_exterior,
)
from app.models import Candidate, ElectionResult
from app.monitor import ElectionMonitorState, monitor_loop, president_poll_interval
from app.tse import TSEClient


def settings() -> Settings:
    return Settings(
        telegram_bot_token="token",
        election_mode="official",
        tse_base_url="https://resultados.tse.jus.br",
        tse_environment="oficial",
        tse_election_code=6257,
        tse_state_election_code=6259,
        tse_cycle="ele2026",
        tse_president_cargo="0001",
        poll_seconds=10,
        request_timeout=8,
        database_path=":memory:",
        admin_ids=set(),
        port=8000,
        webapp_url="https://example.test",
        channel_id="@ResultadoEleicoes",
        president_poll_seconds=2.0,
    )


def result() -> ElectionResult:
    return ElectionResult(
        scope="br",
        election_code=6257,
        round=1,
        phase="oficial",
        generated_date="04/10/2026",
        generated_time="17:00:02",
        totalization_date="04/10/2026",
        totalization_time="17:00:02",
        generation_id="g1",
        disclosure_enabled=True,
        final_totalization=False,
        progress_status="em andamento",
        mathematically_defined="",
        no_elected_assignment=False,
        no_elected_reasons=[],
        sections_total=100,
        sections_counted=1,
        sections_pending=99,
        sections_counted_pct=1.0,
        electorate_total=1000,
        turnout=10,
        turnout_pct=1.0,
        abstention=0,
        abstention_pct=0.0,
        total_votes=10,
        valid_votes=10,
        blank_votes=0,
        null_votes=0,
        void_votes=0,
        void_sub_judice_votes=0,
        candidates=[
            Candidate(
                number=13,
                sequence=1,
                candidate_id="1",
                name="CANDIDATO A",
                ballot_name="CANDIDATO A",
                party="P13",
                party_name="PARTIDO",
                votes=10,
                percentage=100.0,
                percentage_exact=100.0,
                vote_destination="",
                official_status="",
                elected_flag=False,
                vice_name="",
                vice_party="",
            )
        ],
    )


def test_president_poll_is_slow_before_release_and_fast_after_17h():
    tz = ZoneInfo("America/Sao_Paulo")
    before = datetime(2026, 10, 4, 16, 59, 30, tzinfo=tz)
    after = datetime(2026, 10, 4, 17, 0, 1, tzinfo=tz)

    assert president_poll_interval(settings(), before) == 60.0
    assert president_poll_interval(settings(), after) == 2.0


def test_monitor_prioritizes_president_and_updates_channel_on_change(monkeypatch):
    class ResultStub:
        async def fetch(self, scope, *, office="presidente", force=False):
            assert scope == "br"
            assert office == "presidente"
            return result(), True

    class StorageStub:
        async def list_live(self):
            return []

    class BotStub:
        def __init__(self, stop_event):
            self.stop_event = stop_event
            self.refreshed = 0
            self.channel_updates = 0

        async def refresh_live_messages(self, scope, value):
            assert scope == "br"
            self.refreshed += 1

        async def publish_president_channel_update(self, value):
            self.channel_updates += 1
            self.stop_event.set()
            return True

        async def check_channel_ready(self, *, force=False):
            return True

    monkeypatch.setattr(
        "app.monitor.publish_president_channel_update",
        BotStub.publish_president_channel_update,
    )

    async def scenario():
        stop = asyncio.Event()
        state = ElectionMonitorState()
        bot = BotStub(stop)
        await monitor_loop(
            settings(),
            ResultStub(),
            StorageStub(),
            bot,
            stop,
            state,
        )
        assert bot.refreshed == 1
        assert bot.channel_updates == 1
        assert state.last_generation_id == "g1"
        assert state.consecutive_failures == 0
        assert state.last_president_latency_ms is not None

    asyncio.run(scenario())


def test_tse_retries_transient_500_then_succeeds():
    class ClientStub:
        def __init__(self):
            self.calls = 0

        async def get(self, url, headers=None):
            self.calls += 1
            request = httpx.Request("GET", url)
            if self.calls == 1:
                return httpx.Response(503, request=request)
            return httpx.Response(200, request=request, json={"ok": True})

        async def aclose(self):
            pass

    async def scenario():
        tse = TSEClient(settings())
        await tse.client.aclose()
        stub = ClientStub()
        tse.client = stub
        response = await tse._get_with_retry("https://example.test/result.json")
        assert response.status_code == 200
        assert stub.calls == 2
        assert tse.last_latency_ms is not None

    asyncio.run(scenario())


def test_poder360_exterior_parser_structures_candidate_rows():
    html = """
    <article>
      <h2>Nova Zelândia</h2>
      <ul>
        <li>Lula (PT) – 234 votos (70,48%);</li>
        <li>Flávio (PL) – 64 votos (19,28%);</li>
      </ul>
      <h2>Cingapura</h2>
      <ul>
        <li>Lula – 190 votos (54,60%);</li>
        <li>Flávio – 92 votos (26,44%);</li>
      </ul>
    </article>
    """
    items = parse_poder360_exterior(html, "https://source.test")
    assert [item.country for item in items] == ["Nova Zelândia", "Singapura"]
    assert items[0].candidates[0].votes == 234
    assert items[0].candidates[0].percentage == 70.48
    message = format_exterior_channel_message(items[0])
    assert "não é a totalização oficial" in message
    assert "<b>Fonte do levantamento:</b>" in message
    assert "https://source.test" not in message
    assert "http://" not in message
    assert "https://" not in message


def test_exame_parser_adds_country_missing_from_primary_source():
    html = """
    <ul>
      <li>Japão: Flávio somou 24.601 votos nos boletins. Lula recebeu 5.234 votos.</li>
      <li>Indonésia: Flávio somou 19 votos. Lula recebeu oito votos.</li>
      <li>Coreia do Sul: o boletim registra 123 votos para Lula. Flávio recebeu 72 votos.</li>
    </ul>
    """
    items = parse_exame_exterior(html, "https://exame.test")
    by_country = {item.country: item for item in items}
    assert set(by_country) == {"Japão", "Indonésia", "Coreia do Sul"}
    assert [c.votes for c in by_country["Japão"].candidates[:2]] == [24601, 5234]
    assert sorted(c.votes for c in by_country["Indonésia"].candidates) == [8, 19]
    assert sorted(c.votes for c in by_country["Coreia do Sul"].candidates) == [72, 123]



def test_channel_readiness_requires_post_and_edit_permissions():
    class TelegramStub:
        async def get_chat(self, chat_id):
            return SimpleNamespace(id=-100123)

        async def get_me(self):
            return SimpleNamespace(id=999)

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(
                status="administrator",
                can_post_messages=True,
                can_edit_messages=False,
            )

    async def scenario():
        bot = ElectionBot(
            settings(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        bot.application = SimpleNamespace(bot=TelegramStub())
        ready = await bot.check_channel_ready(force=True)
        assert ready is False
        assert "publicar e editar" in bot.channel_error
        assert bot.channel_chat_id == -100123

    asyncio.run(scenario())


def test_channel_readiness_accepts_admin_with_post_and_edit():
    class TelegramStub:
        async def get_chat(self, chat_id):
            return SimpleNamespace(id=-100123)

        async def get_me(self):
            return SimpleNamespace(id=999)

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(
                status="administrator",
                can_post_messages=True,
                can_edit_messages=True,
            )

    async def scenario():
        bot = ElectionBot(
            settings(),
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
        )
        bot.application = SimpleNamespace(bot=TelegramStub())
        assert await bot.check_channel_ready(force=True) is True
        assert bot.channel_error == ""

    asyncio.run(scenario())



def test_tse_exterior_president_url_uses_zz_scope():
    tse = TSEClient(settings())
    try:
        url = tse.result_url("zz", office="presidente")
        assert url.endswith("/dados/zz/zz-c0001-e006257-u.json")
    finally:
        asyncio.run(tse.close())



def test_monitor_detects_vote_change_even_when_shared_client_says_unchanged(monkeypatch):
    class ResultStub:
        def __init__(self):
            self.calls = 0

        async def fetch(self, scope, *, office="presidente", force=False):
            self.calls += 1
            current = result()
            if self.calls >= 2:
                current.total_votes = 11
                current.valid_votes = 11
                current.candidates[0].votes = 11
            # Simulates the WebApp having consumed the shared TSE client's
            # internal changed flag before the monitor polls.
            return current, False

    class StorageStub:
        async def list_live(self):
            return []

    class BotStub:
        def __init__(self, stop_event):
            self.stop_event = stop_event
            self.refreshed = 0
            self.channel_updates = 0

        async def refresh_live_messages(self, scope, value):
            self.refreshed += 1

        async def publish_president_channel_update(self, value):
            self.channel_updates += 1
            if self.channel_updates >= 2:
                self.stop_event.set()
            return True

        async def check_channel_ready(self, *, force=False):
            return True

    monkeypatch.setattr(
        "app.monitor.publish_president_channel_update",
        BotStub.publish_president_channel_update,
    )
    monkeypatch.setattr("app.monitor.president_poll_interval", lambda _settings: 0.01)

    async def scenario():
        cfg = replace(settings(), president_poll_seconds=0.01)
        stop = asyncio.Event()
        bot = BotStub(stop)
        state = ElectionMonitorState()
        await asyncio.wait_for(
            monitor_loop(
                cfg,
                ResultStub(),
                StorageStub(),
                bot,
                stop,
                state,
            ),
            timeout=1.0,
        )
        assert bot.refreshed == 2
        assert bot.channel_updates == 2
        assert state.last_president_change_at is not None
        assert state.last_signature is not None

    asyncio.run(scenario())



def test_tse_rejects_stale_totalization_regression():
    old = result()
    old.sections_counted = 22
    old.sections_counted_pct = 21.96
    old.total_votes = 26_377_493

    stale = result()
    stale.sections_counted = 20
    stale.sections_counted_pct = 19.54
    stale.total_votes = 23_473_571

    newer = result()
    newer.sections_counted = 24
    newer.sections_counted_pct = 23.10
    newer.total_votes = 27_000_000

    assert TSEClient._is_stale_regression(stale, old) is True
    assert TSEClient._is_stale_regression(newer, old) is False



def test_channel_publication_deduplicates_same_tse_generation(monkeypatch):
    class StorageStub:
        def __init__(self):
            self.state = {}

        async def get_state(self, key):
            return self.state.get(key)

        async def set_state(self, key, value):
            self.state[key] = value

    class TelegramStub:
        async def get_chat(self, chat_id):
            return SimpleNamespace(id=-100123)

        async def get_me(self):
            return SimpleNamespace(id=999)

        async def get_chat_member(self, chat_id, user_id):
            return SimpleNamespace(
                status="administrator",
                can_post_messages=True,
                can_edit_messages=True,
            )

    async def scenario():
        storage = StorageStub()
        bot = ElectionBot(settings(), SimpleNamespace(), storage, SimpleNamespace())
        bot.application = SimpleNamespace(bot=TelegramStub())
        sent = []

        async def fake_send_rich_html(**kwargs):
            assert "<tg-button" not in kwargs["rich_html"]
            assert "reply_markup" not in kwargs
            sent.append(kwargs["rich_html"])
            return {"message_id": 1000 + len(sent)}

        monkeypatch.setattr("app.channel_posts.send_rich_html", fake_send_rich_html)

        first = result()
        assert await publish_president_channel_update(bot, first) is True
        assert await publish_president_channel_update(bot, first) is True
        assert len(sent) == 1

        second = result()
        second.generation_id = "g2"
        second.sections_counted = 2
        second.sections_counted_pct = 2.0
        second.total_votes = 20
        second.valid_votes = 20
        second.candidates[0].votes = 20
        assert await publish_president_channel_update(bot, second) is True
        assert len(sent) == 2
        assert storage.state["channel:president:last_generation_id"] == "g2"

    asyncio.run(scenario())
