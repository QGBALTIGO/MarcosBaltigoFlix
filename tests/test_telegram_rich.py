from app.g1_polls import Discovery, parse_g1_payload
from app.telegram_rich import build_g1_channel_rich_html


def _poll():
    discovery = Discovery(
        api_url="https://example.test/api",
        page_url="https://example.test/page",
        institute="Datafolha",
        question_code="ESTIMULADA-PRE-002",
        page_id="100",
        methodology="2.002 entrevistas. Margem de erro de 2 pontos. Registro BR-00304/2026.",
        sample_size=2002,
        field_period="22 e 23 de setembro",
        registrations=["BR-00304/2026"],
    )
    payload = {
        "resultado": {
            "cenarios": [{
                "bandeira_slug": "total",
                "variavel_cruzamento": "Total",
                "margem": 2,
                "pergunta": {"codigo": "ESTIMULADA-PRE-002"},
                "opcoes_resposta": [
                    {"nome": "Candidato A", "partido": {"sigla": "AAA"}},
                    {"nome": "Candidato B", "partido": {"sigla": "BBB"}},
                    {"nome": "Indecisos", "partido": None},
                ],
                "data": [
                    {"option": "Candidato A", "values": [
                        {"date": "2026-09-17T00:00:00Z", "value": 0.39},
                        {"date": "2026-09-24T00:00:00Z", "value": 0.40},
                    ]},
                    {"option": "Candidato B", "values": [
                        {"date": "2026-09-17T00:00:00Z", "value": 0.36},
                        {"date": "2026-09-24T00:00:00Z", "value": 0.36},
                    ]},
                    {"option": "Indecisos", "values": [
                        {"date": "2026-09-24T00:00:00Z", "value": 0.02},
                    ]},
                ],
            }]
        }
    }
    return parse_g1_payload(payload, discovery, "presidente", "br", 1)


def test_rich_poll_uses_native_table_and_details():
    rich = build_g1_channel_rich_html(
        _poll(),
        headline="PESQUISA ELEITORAL • PRESIDENTE",
        panel_url="https://example.test/painel",
    )
    assert "<table bordered striped compact>" in rich
    assert '<th align="left">Candidato</th>' in rich
    assert "<b>40%</b>" in rich
    assert "+1,0 p.p." in rich
    assert "<details><summary>Metodologia e registro</summary>" in rich
    assert "<tg-button-row" in rich
    assert 'style="primary"' in rich
    assert "Indecisos" in rich
