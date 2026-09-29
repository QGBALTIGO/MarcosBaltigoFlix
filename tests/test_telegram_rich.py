from app.g1_polls import Discovery, parse_g1_payload
from app.models import Candidate, ElectionResult
from app.telegram_rich import (
    STATE_RESULT_PAGE_SIZE,
    build_g1_channel_rich_html,
    build_president_result_rich_html,
    build_state_inline_menu_rich_html,
    build_state_office_result_rich_html,
)


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
    assert 'type="url" style="primary"' not in rich
    assert '<tg-button type="url" url="' in rich
    assert "⬅️ Voltar" not in rich
    assert "📤 Compartilhar" not in rich
    assert "<b>52,63%</b>" in rich
    assert "25%" in rich



def _state_result(scope: str = "ms", count: int = 19, pre_election: bool = True) -> ElectionResult:
    candidates = []
    for index in range(1, count + 1):
        candidates.append(
            Candidate(
                number=1000 + index,
                sequence=index,
                candidate_id=str(index),
                name=f"CANDIDATO {index:02d}",
                ballot_name=f"CANDIDATO {index:02d}",
                party=f"P{index:02d}",
                party_name=f"PARTIDO {index:02d}",
                votes=0 if pre_election else (count - index + 1) * 100,
                percentage=0.0 if pre_election else round((count - index + 1) / 190 * 100, 2),
                percentage_exact=0.0 if pre_election else round((count - index + 1) / 190 * 100, 2),
                vote_destination="",
                official_status="",
                elected_flag=False,
                vice_name="",
                vice_party="",
            )
        )
    return ElectionResult(
        scope=scope,
        election_code=6259,
        round=1,
        phase="pre_election" if pre_election else "oficial",
        generated_date="" if pre_election else "04/10/2026",
        generated_time="" if pre_election else "17:20:00",
        totalization_date="" if pre_election else "04/10/2026",
        totalization_time="" if pre_election else "17:20:00",
        generation_id=f"state-{scope}",
        disclosure_enabled=not pre_election,
        final_totalization=False,
        progress_status="Aguardando" if pre_election else "em andamento",
        mathematically_defined="",
        no_elected_assignment=False,
        no_elected_reasons=[],
        sections_total=0 if pre_election else 100,
        sections_counted=0 if pre_election else 20,
        sections_pending=0 if pre_election else 80,
        sections_counted_pct=0.0 if pre_election else 20.0,
        electorate_total=0 if pre_election else 1000,
        turnout=0 if pre_election else 500,
        turnout_pct=0.0 if pre_election else 50.0,
        abstention=0 if pre_election else 500,
        abstention_pct=0.0 if pre_election else 50.0,
        total_votes=0 if pre_election else 10000,
        valid_votes=0 if pre_election else 9500,
        blank_votes=0 if pre_election else 200,
        null_votes=0 if pre_election else 300,
        void_votes=0,
        void_sub_judice_votes=0,
        candidates=candidates,
    )


def test_state_rich_first_page_has_pagination_and_contextual_share():
    result = _state_result()
    rich = build_state_office_result_rich_html(
        result,
        office="federal",
        page=0,
        panel_url="https://example.test/app",
        panel_web_app=True,
        shared=False,
    )

    assert STATE_RESULT_PAGE_SIZE == 8
    assert "<h2>🗳️ Deputado Federal • Mato Grosso do Sul</h2>" in rich
    assert "Página 1 de 3 · 19 candidaturas" in rich
    assert "CANDIDATO 01" in rich
    assert "CANDIDATO 08" in rich
    assert "CANDIDATO 09" not in rich
    assert "Próxima ➡️" in rich
    assert "⬅️ Anterior" not in rich
    assert 'data="state:view:ms:federal:1"' in rich
    assert 'data="state:refresh:ms:federal:0"' in rich
    assert 'query="estado ms federal 0"' in rich
    assert "⬅️ Voltar" in rich
    assert "📤 Compartilhar" in rich


def test_state_rich_middle_page_has_previous_and_next():
    rich = build_state_office_result_rich_html(
        _state_result(),
        office="senador",
        page=1,
        panel_url="https://example.test/app",
        shared=False,
    )

    assert "Página 2 de 3 · 19 candidaturas" in rich
    assert "CANDIDATO 09" in rich
    assert "CANDIDATO 16" in rich
    assert "CANDIDATO 08" not in rich
    assert "CANDIDATO 17" not in rich
    assert 'data="state:view:ms:senador:0"' in rich
    assert 'data="state:view:ms:senador:2"' in rich
    assert 'data="state:refresh:ms:senador:1"' in rich


def test_state_rich_last_page_clamps_and_shared_keeps_context():
    rich = build_state_office_result_rich_html(
        _state_result(),
        office="governador",
        page=999,
        panel_url="https://t.me/ResultadoEleicoes_Bot?start=painel",
        panel_web_app=False,
        shared=True,
    )

    assert "Página 3 de 3 · 19 candidaturas" in rich
    assert "CANDIDATO 17" in rich
    assert "CANDIDATO 19" in rich
    assert "Próxima ➡️" not in rich
    assert "⬅️ Anterior" in rich
    assert 'data="state:view:ms:governador:1"' in rich
    assert 'data="state:refresh:ms:governador:2"' in rich
    assert "📊 Painel ao vivo" in rich
    assert "⬅️ Voltar" not in rich
    assert "📤 Compartilhar" not in rich


def test_df_state_rich_uses_distrital_name():
    rich = build_state_office_result_rich_html(
        _state_result(scope="df", count=2),
        office="estadual",
        page=0,
        panel_url="",
        shared=False,
    )
    assert "<h2>🗳️ Deputado Distrital • Distrito Federal</h2>" in rich


def test_state_inline_menu_has_exactly_four_office_actions():
    rich = build_state_inline_menu_rich_html("sp")

    assert "<h2>São Paulo</h2>" in rich
    assert rich.count('data="state:open:sp:') == 4
    assert "Deputado Federal" in rich
    assert "Deputado Estadual" in rich
    assert "Senador" in rich
    assert "Governador" in rich


def test_state_inline_menu_uses_distrital_for_df():
    rich = build_state_inline_menu_rich_html("df")
    assert "Deputado Distrital" in rich
    assert "Deputado Estadual" not in rich


def test_shared_state_result_can_return_to_office_menu():
    rich = build_state_office_result_rich_html(
        _state_result(scope="sp", count=2),
        office="governador",
        page=0,
        panel_url="https://t.me/ResultadoEleicoes_Bot?start=painel",
        panel_web_app=False,
        shared=True,
    )
    assert 'data="state:sp"' in rich
    assert "⬅️ Cargos" in rich


def test_president_table_is_alphabetical_before_counting():
    result = _president_result(pre_election=True)
    # Deliberately keep the source list in non-alphabetical order.
    assert result.candidates[0].ballot_name == "CANDIDATO UM"

    rich = build_president_result_rich_html(result, panel_url="")
    assert rich.index("CANDIDATO DOIS") < rich.index("CANDIDATO UM")


def test_president_table_reorders_by_percentage_and_votes_when_live():
    result = _president_result(pre_election=False)
    result.candidates = list(reversed(result.candidates))

    rich = build_president_result_rich_html(result, panel_url="")
    assert rich.index("CANDIDATO UM") < rich.index("CANDIDATO DOIS")


def test_state_pagination_uses_live_ranking_not_source_order():
    result = _state_result(count=19, pre_election=False)
    result.candidates = list(reversed(result.candidates))

    rich = build_state_office_result_rich_html(
        result,
        office="federal",
        page=0,
        panel_url="",
        shared=False,
    )

    assert "CANDIDATO 01" in rich
    assert "CANDIDATO 08" in rich
    assert "CANDIDATO 09" not in rich
    assert rich.index("CANDIDATO 01") < rich.index("CANDIDATO 08")
