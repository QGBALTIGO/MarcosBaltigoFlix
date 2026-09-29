from app.bot import start_keyboard, start_message_text, states_keyboard
from app.config import Settings


def settings(*, simulation: bool = False, webapp_url: str = "https://example.com/app") -> Settings:
    return Settings(
        telegram_bot_token="test-token",
        election_mode="simulation" if simulation else "official",
        tse_base_url="https://example.com",
        tse_environment="official",
        tse_election_code=1,
        tse_state_election_code=2,
        tse_cycle="ele2026",
        tse_president_cargo="0001",
        poll_seconds=20,
        request_timeout=12.0,
        database_path=":memory:",
        admin_ids=set(),
        port=8000,
        webapp_url=webapp_url,
        channel_id="",
    )


def test_start_message_has_requested_structure_and_formatting():
    text = start_message_text(settings())

    assert text.startswith("<b>🗳️ Eleições 2026 • Apuração Oficial</b>")
    assert "Bem-vindo ao <b>Eleições 2026</b>." in text
    assert "<blockquote>" in text
    assert "</blockquote>" in text
    for line in (
        "📊 Consulte resultados por cargo e estado",
        "🔄 Acompanhe a evolução da totalização",
        "🔔 Receba alertas durante a apuração",
        "🔎 Consulte pesquisas eleitorais disponíveis",
        "🏛️ Acesse informações sobre os candidatos",
    ):
        assert line in text
    assert "<i><b>Transparência:</b>" in text
    assert "sem projeções próprias ou indicação de voto" in text
    assert "👇 <b>Escolha uma opção abaixo para começar.</b>" in text


def test_start_message_never_shows_simulation_disclosure():
    text = start_message_text(settings(simulation=True))
    assert "Modo de simulação" not in text
    assert "não representam votos reais" not in text


def test_start_keyboard_private_has_webapp_then_two_buttons():
    keyboard = start_keyboard(settings(), external_chat=False).inline_keyboard

    assert len(keyboard) == 2
    assert [button.text for button in keyboard[0]] == ["📲 Abrir painel completo"]
    assert keyboard[0][0].web_app is not None
    assert [button.text for button in keyboard[1]] == ["🗳️ Presidente", "🗺️ Estados"]
    assert keyboard[1][0].callback_data == "result:br"
    assert keyboard[1][1].callback_data == "start:states"


def test_start_keyboard_external_uses_deep_link_instead_of_webapp():
    keyboard = start_keyboard(settings(), external_chat=True).inline_keyboard

    assert keyboard[0][0].web_app is None
    assert keyboard[0][0].url == "https://t.me/ResultadoEleicoes_Bot?start=painel"


def test_states_keyboard_has_all_27_ufs_and_back_button():
    keyboard = states_keyboard().inline_keyboard
    uf_buttons = [button for row in keyboard[:-1] for button in row]

    assert len(uf_buttons) == 27
    assert uf_buttons[0].text == "AC"
    assert uf_buttons[-1].text == "TO"
    assert all(button.callback_data.startswith("result:") for button in uf_buttons)
    assert keyboard[-1][0].text == "⬅️ Voltar"
    assert keyboard[-1][0].callback_data == "start:home"
