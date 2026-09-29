import asyncio
from types import SimpleNamespace

from app.bot import ElectionBot, STATE_OFFICE_LABELS
from app.config import Settings
from app.models import Candidate, ElectionResult


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


def result(scope: str, office: str) -> ElectionResult:
    return ElectionResult(
        scope=scope,
        election_code=6259,
        round=1,
        phase="pre_election",
        generated_date="",
        generated_time="",
        totalization_date="",
        totalization_time="",
        generation_id=f"pre-election:{office}:{scope}",
        disclosure_enabled=False,
        final_totalization=False,
        progress_status="Aguardando início da apuração oficial",
        mathematically_defined="",
        no_elected_assignment=False,
        no_elected_reasons=[],
        sections_total=0,
        sections_counted=0,
        sections_pending=0,
        sections_counted_pct=0.0,
        electorate_total=0,
        turnout=0,
        turnout_pct=0.0,
        abstention=0,
        abstention_pct=0.0,
        total_votes=0,
        valid_votes=0,
        blank_votes=0,
        null_votes=0,
        void_votes=0,
        void_sub_judice_votes=0,
        candidates=[
            Candidate(
                number=1313,
                sequence=1,
                candidate_id="1",
                name="CANDIDATO TESTE",
                ballot_name="CANDIDATO TESTE",
                party="PT",
                party_name="PARTIDO TESTE",
                votes=0,
                percentage=0.0,
                percentage_exact=0.0,
                vote_destination="",
                official_status="",
                elected_flag=False,
                vice_name="",
                vice_party="",
            )
        ],
    )


class ResultStub:
    async def fetch(self, scope, *, office="presidente", force=False):
        return result(scope, office), False


class QueryStub:
    def __init__(self, data: str):
        self.data = data
        self.inline_message_id = None
        self.message = SimpleNamespace(
            chat_id=123,
            message_id=456,
            chat=SimpleNamespace(type="private"),
        )
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


def make_bot() -> ElectionBot:
    return ElectionBot(settings(), ResultStub(), SimpleNamespace(), SimpleNamespace())


def test_state_runtime_methods_exist_on_election_bot():
    for name in (
        "_state_office_rich",
        "_state_page_count",
        "_state_fallback_text",
        "_send_state_office_rich",
    ):
        assert hasattr(ElectionBot, name), name


def test_state_open_callbacks_route_all_108_state_office_combinations():
    async def scenario():
        bot = make_bot()

        async def guard(update, context):
            return True

        calls = []

        async def capture(message, *, scope, office, page=0, edit=False, force=False):
            calls.append((scope, office, page, edit, force))

        bot._guard_required_channel = guard
        bot._send_state_office_rich = capture

        scopes = (
            "ac","al","ap","am","ba","ce","df","es","go","ma","mt","ms","mg","pa",
            "pb","pr","pe","pi","rj","rn","rs","ro","rr","sc","sp","se","to"
        )
        for scope in scopes:
            for office in STATE_OFFICE_LABELS:
                query = QueryStub(f"state:open:{scope}:{office}:0")
                update = SimpleNamespace(callback_query=query)
                await bot.callback(update, SimpleNamespace())
                assert calls[-1] == (scope, office, 0, True, False)
                assert len(query.answers) == 1

        assert len(calls) == 27 * 4

    asyncio.run(scenario())


def test_real_state_sender_fetches_expected_scope_office(monkeypatch):
    async def scenario():
        bot = make_bot()
        captured = {}

        async def fake_edit_rich_html(**kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr("app.bot.edit_rich_html", fake_edit_rich_html)

        message = SimpleNamespace(
            chat_id=123,
            message_id=456,
            chat=SimpleNamespace(type="private"),
        )
        await bot._send_state_office_rich(
            message,
            scope="sp",
            office="federal",
            page=0,
            edit=True,
        )

        assert captured["chat_id"] == 123
        assert captured["message_id"] == 456
        assert "Deputado Federal" in captured["rich_html"]
        assert "São Paulo" in captured["rich_html"]
        assert "CANDIDATO TESTE" in captured["rich_html"]

    asyncio.run(scenario())


def test_state_open_callback_edits_inline_message(monkeypatch):
    async def scenario():
        bot = make_bot()

        async def guard(update, context):
            return True

        captured = {}

        async def fake_edit_rich_html(**kwargs):
            captured.update(kwargs)
            return True

        bot._guard_required_channel = guard
        monkeypatch.setattr("app.bot.edit_rich_html", fake_edit_rich_html)

        query = QueryStub("state:open:sp:governador:0")
        query.inline_message_id = "inline-123"
        query.message = None
        update = SimpleNamespace(callback_query=query)

        await bot.callback(update, SimpleNamespace())

        assert captured["inline_message_id"] == "inline-123"
        assert "Governador" in captured["rich_html"]
        assert "São Paulo" in captured["rich_html"]
        assert len(query.answers) == 1

    asyncio.run(scenario())


def test_state_back_callback_restores_inline_office_menu(monkeypatch):
    async def scenario():
        bot = make_bot()
        captured = {}

        async def fake_edit_rich_html(**kwargs):
            captured.update(kwargs)
            return True

        monkeypatch.setattr("app.bot.edit_rich_html", fake_edit_rich_html)

        query = QueryStub("state:sp")
        query.inline_message_id = "inline-456"
        query.message = None
        update = SimpleNamespace(callback_query=query)

        await bot.callback(update, SimpleNamespace())

        assert captured["inline_message_id"] == "inline-456"
        assert captured["rich_html"].count('data="state:open:sp:') == 4
        assert len(query.answers) == 1

    asyncio.run(scenario())
