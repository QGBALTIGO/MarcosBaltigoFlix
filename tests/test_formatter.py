from app.config import Settings
from app.formatter import format_result
from app.tse import parse_ea20
from tests.test_parser import fixture


def settings():
    return Settings(
        telegram_bot_token="",
        election_mode="simulation",
        tse_base_url="https://example.test",
        tse_environment="simulado2026",
        tse_election_code=21270,
        tse_cycle="ele2026",
        tse_president_cargo="0001",
        poll_seconds=20,
        request_timeout=10,
        database_path=":memory:",
        admin_ids=set(),
        port=8000,
        webapp_url="",
        channel_id="",
    )


def test_formatter_warns_simulation():
    text = format_result(parse_ea20(fixture(), "br"), settings())
    assert "DADOS DE SIMULAÇÃO" in text
    assert "Candidato A" not in text  # uses ballot name on purpose
    assert "<b>10 • A</b>" in text
