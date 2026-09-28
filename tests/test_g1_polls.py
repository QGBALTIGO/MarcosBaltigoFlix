from app.g1_polls import Discovery, parse_g1_payload, parse_page_config


def test_parse_page_config():
    html = """
    <script>
    window.g1PesquisasEleitorais = {
      paginaId: "100",
      turno: "1",
      instituto: "Datafolha",
      tipoPergunta: "ESTIMULADA-PRE-002",
      environment: "prod",
      apiToken: ""
    }
    </script>
    """
    cfg = parse_page_config(html)
    assert cfg["paginaId"] == "100"
    assert cfg["tipoPergunta"] == "ESTIMULADA-PRE-002"
    assert cfg["instituto"] == "Datafolha"


def test_parse_payload_uses_latest_date_not_array_order():
    discovery = Discovery(
        api_url="https://example.test/api",
        page_url="https://example.test/page",
        institute="Datafolha",
        question_code="ESTIMULADA-PRE-002",
        page_id="100",
        methodology="Teste",
        sample_size=2002,
        field_period="22 e 23 de setembro",
        registrations=["BR-00304/2026"],
        questions=[],
    )
    payload = {
        "resultado": {
            "cenarios": [{
                "bandeira_slug": "total",
                "variavel_cruzamento": "Total",
                "margem": 2,
                "pergunta": {"codigo": "ESTIMULADA-PRE-002"},
                "opcoes_resposta": [
                    {"nome": "Candidato A", "partido": {"sigla": "PL"}}
                ],
                "data": [{
                    "option": "Candidato A",
                    "values": [
                        {"date": "2026-09-24T00:00:00Z", "value": 0.40},
                        {"date": "2026-09-17T00:00:00Z", "value": 0.39},
                    ],
                }],
            }]
        }
    }
    result = parse_g1_payload(payload, discovery, "presidente", "br", 1)
    assert result.latest_date == "2026-09-24"
    assert result.choices[0].percentage == 40.0
    assert result.margin_error_points == 2.0
    assert result.choices[0].number == "22"
    assert result.choices[0].party == "PL"
