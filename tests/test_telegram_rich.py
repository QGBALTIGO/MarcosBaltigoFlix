from app.g1_polls import Discovery, parse_g1_payload
from app.models import Candidate, ElectionResult
from app.telegram_rich import build_g1_channel_rich_html, build_president_result_rich_html


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
                    {"nome": "Candidato A", "partido": {"sigla": "PL"}},
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
    assert "<b>Candidato A</b><br/>22 - PL" in rich
    assert "+1,0 p.p." in rich
    assert "<details><summary>Metodologia e registro</summary>" in rich
    assert "<tg-button-row" in rich
    assert 'style="primary"' in rich
    assert "Indecisos" in rich



def _president_result(*, pre_election: bool = True) -> ElectionResult:
    return ElectionResult(
        scope="br",
        election_code=6257,
        round=1,
        phase="pre_election" if pre_election else "oficial",
        generated_date="" if pre_election else "04/10/2026",
        generated_time="" if pre_election else "17:10:00",
        totalization_date="" if pre_election else "04/10/2026",
        totalization_time="" if pre_election else "17:10:00",
        generation_id="pre-election:presidente:br" if pre_election else "live",
        disclosure_enabled=not pre_election,
        final_totalization=False,
        progress_status="Aguardando início da apuração oficial" if pre_election else "em andamento",
        mathematically_defined="",
        no_elected_assignment=False,
        no_elected_reasons=[],
        sections_total=0 if pre_election else 100,
        sections_counted=0 if pre_election else 25,
        sections_pending=0 if pre_election else 75,
        sections_counted_pct=0.0 if pre_election else 25.0,
        electorate_total=0 if pre_election else 1000,
        turnout=0 if pre_election else 200,
        turnout_pct=0.0 if pre_election else 20.0,
        abstention=0 if pre_election else 800,
        abstention_pct=0.0 if pre_election else 80.0,
        total_votes=0 if pre_election else 200,
        valid_votes=0 if pre_election else 190,
        blank_votes=0 if pre_election else 4,
        null_votes=0 if pre_election else 6,
        void_votes=0,
        void_sub_judice_votes=0,
        candidates=[
            Candidate(
                number=13,
                sequence=1,
                candidate_id="1",
                name="CANDIDATO UM",
                ballot_name="CANDIDATO UM",
                party="P13",
                party_name="PARTIDO 13",
                votes=0 if pre_election else 100,
                percentage=0.0 if pre_election else 52.63,
                percentage_exact=0.0 if pre_election else 52.63,
                vote_destination="",
                official_status="",
                elected_flag=False,
                vice_name="",
                vice_party="",
            ),
            Candidate(
                number=22,
                sequence=2,
                candidate_id="2",
                name="CANDIDATO DOIS",
                ballot_name="CANDIDATO DOIS",
                party="P22",
                party_name="PARTIDO 22",
                votes=0 if pre_election else 90,
                percentage=0.0 if pre_election else 47.37,
                percentage_exact=0.0 if pre_election else 47.37,
                vote_destination="",
                official_status="",
                elected_flag=False,
                vice_name="",
                vice_party="",
            ),
        ],
    )


def test_president_rich_uses_native_tables_and_only_requested_buttons():
    rich = build_president_result_rich_html(
        _president_result(),
        panel_url="https://example.test/app",
        panel_web_app=True,
        shared=False,
    )

    assert "<table bordered striped compact>" in rich
    assert "<caption>Resultado presidencial</caption>" in rich
    assert "<caption>Totalização</caption>" in rich
    assert "<b>CANDIDATO UM</b><br/>13 · P13" in rich
    assert "<b>0%</b>" in rich
    assert "Apuração ainda não iniciada" in rich

    assert rich.count("<tg-button ") == 4
    assert "🔄 Atualizar" in rich
    assert "📊 Painel ao vivo" in rich
    assert "⬅️ Voltar" in rich
    assert "📤 Compartilhar" in rich
    assert 'type="switch_inline_query_chosen_chat"' in rich
    assert 'allow-user-chats allow-group-chats' in rich
    assert "Fonte G1" not in rich
    assert "Abrir resultados do TSE" not in rich


def test_shared_president_rich_keeps_update_button_for_groups():
    rich = build_president_result_rich_html(
        _president_result(pre_election=False),
        panel_url="https://t.me/ResultadoEleicoes_Bot?start=painel",
        panel_web_app=False,
        shared=True,
    )

    assert rich.count("<tg-button ") == 2
    assert rich.count("<tg-button-row") == 2
    assert "🔄 Atualizar resultado" in rich
    assert 'type="callback_data"' in rich
    assert 'data="president:refresh:br"' in rich
    assert 'type="callback_data" style="primary"' not in rich
    assert "📊 Painel ao vivo" in rich
    assert 'type="url" style="primary"' in rich
    assert "⬅️ Voltar" not in rich
    assert "📤 Compartilhar" not in rich
    assert "<b>52,63%</b>" in rich
    assert "25%" in rich
