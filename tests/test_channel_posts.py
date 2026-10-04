from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.bot import ElectionBot
from app.channel_posts import (
    build_president_channel_rich_html,
    publish_president_channel_update,
)
from app.monitor import ElectionMonitorState, monitor_loop
from app.telegram_rich import RichMessageError, build_president_result_rich_html
from tests.test_election_day_engine import result, settings


@pytest.mark.parametrize("phase", ["oficial", "pre_election"])
def test_channel_preserves_every_result_field_but_has_no_controls(phase):
    current = result()
    current.phase = phase
    if phase == "pre_election":
        current.total_votes = current.valid_votes = 0
        current.sections_counted = current.sections_counted_pct = 0
        for candidate in current.candidates:
            candidate.votes = candidate.percentage = 0

    shared = build_president_result_rich_html(
        current, shared=True, panel_url="https://example.test/painel"
    )
    bulletin = build_president_channel_rich_html(current)
    # Keep the full data portion exactly unchanged, excluding only the controls.
    assert bulletin == shared.split("<tg-button-row", 1)[0]
    assert bulletin.count("<table ") == 2
    assert "Resultado presidencial" in bulletin
    assert "Totalização" in bulletin
    assert "Fonte: TSE" in bulletin
    for forbidden in (
        "<tg-button", "callback_data", "switch_inline_query", "reply_markup",
        "Atualizar resultado", "Painel ao vivo", "Compartilhar", "Voltar",
    ):
        assert forbidden not in bulletin


def test_inline_and_private_bot_keep_their_controls():
    current = result()
    shared = build_president_result_rich_html(
        current, shared=True, panel_url="https://example.test/painel"
    )
    private = build_president_result_rich_html(
        current, shared=False, panel_url="https://example.test/painel"
    )
    assert shared.count("<tg-button ") == 2
    assert "president:refresh:br" in shared
    assert "Painel ao vivo" in shared
    assert private.count("<tg-button ") == 4
    assert "switch_inline_query_chosen_chat" in private
    assert "start:home" in private
    assert "<tg-button" not in build_president_channel_rich_html(current)


def test_channel_escapes_names_without_injecting_controls():
    current = result()
    current.candidates[0].ballot_name = '<tg-button>TESTE & NOME</tg-button>'
    current.candidates[0].party = '<PARTIDO>'
    rendered = build_president_channel_rich_html(current)
    assert '<tg-button' not in rendered
    assert '&lt;tg-button&gt;TESTE &amp; NOME&lt;/tg-button&gt;' in rendered
    assert '&lt;PARTIDO&gt;' in rendered


class StorageStub:
    def __init__(self):
        self.state = {}

    async def get_state(self, key):
        return self.state.get(key)

    async def set_state(self, key, value):
        self.state[key] = value

    async def list_live(self):
        return []


class TelegramStub:
    async def get_chat(self, chat_id):
        return SimpleNamespace(id=-100123)

    async def get_me(self):
        return SimpleNamespace(id=999)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(
            status="administrator", can_post_messages=True, can_edit_messages=True
        )


def make_bot(storage):
    bot = ElectionBot(settings(), SimpleNamespace(), storage, SimpleNamespace())
    bot.application = SimpleNamespace(bot=TelegramStub())
    return bot


def test_monitor_sends_new_channel_posts_without_inline_buttons_on_the_wire(monkeypatch):
    async def scenario():
        stop = asyncio.Event()
        captured = []
        storage = StorageStub()
        bot = make_bot(storage)

        class ResultStub:
            def __init__(self):
                self.calls = 0

            async def fetch(self, scope, *, office="presidente", force=False):
                assert (scope, office) == ("br", "presidente")
                self.calls += 1
                current = result()
                current.generation_id = f"g{self.calls}"
                current.total_votes = current.valid_votes = self.calls * 10
                current.candidates[0].votes = self.calls * 10
                return current, False

        async def fake_bot_api_post(*, token, method, payload):
            assert method == "sendRichMessage"
            assert payload["chat_id"] == -100123
            assert "reply_markup" not in payload
            rich = payload["rich_message"]
            assert rich["skip_entity_detection"] is True
            assert "<tg-button" not in rich["html"]
            assert "<table " in rich["html"]
            assert "17:00:02" in rich["html"]
            captured.append(payload)
            if len(captured) == 2:
                stop.set()
            return {"message_id": 2000 + len(captured)}

        monkeypatch.setattr("app.telegram_rich._bot_api_post", fake_bot_api_post)
        monkeypatch.setattr("app.monitor.president_poll_interval", lambda _settings: 0.01)
        state = ElectionMonitorState()
        await asyncio.wait_for(
            monitor_loop(settings(), ResultStub(), storage, bot, stop, state),
            timeout=2,
        )
        assert len(captured) == 2
        assert state.consecutive_failures == 0
        assert storage.state["channel:president:last_generation_id"] == "g2"
        assert storage.state["channel:president:last_post_message_id"] == "2002"
        assert "<td align=\"right\">10</td>" in captured[0]["rich_message"]["html"]
        assert "<td align=\"right\">20</td>" in captured[1]["rich_message"]["html"]

    asyncio.run(scenario())


def test_channel_failed_send_does_not_commit_the_fingerprint(monkeypatch):
    async def scenario():
        storage = StorageStub()
        bot = make_bot(storage)

        async def fail_send(**kwargs):
            raise RichMessageError("Telegram temporarily unavailable")

        monkeypatch.setattr("app.channel_posts.send_rich_html", fail_send)
        assert await publish_president_channel_update(bot, result()) is False
        assert "channel:president:last_post_signature" not in storage.state

        async def successful_send(**kwargs):
            assert "<tg-button" not in kwargs["rich_html"]
            return {"message_id": 2001}

        monkeypatch.setattr("app.channel_posts.send_rich_html", successful_send)
        assert await publish_president_channel_update(bot, result()) is True
        assert storage.state["channel:president:last_post_message_id"] == "2001"

    asyncio.run(scenario())
