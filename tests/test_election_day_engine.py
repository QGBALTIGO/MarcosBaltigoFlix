from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from app.bot import ElectionBot
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


def test_monitor_prioritizes_president_and_updates_channel_on_change():
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

        async def ensure_president_channel_message(self, value, *, changed=False):
            assert changed is True
            self.channel_updates += 1
            self.stop_event.set()
            return True

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
    assert "não é a totalização oficial" in format_exterior_channel_message(items[0])


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
