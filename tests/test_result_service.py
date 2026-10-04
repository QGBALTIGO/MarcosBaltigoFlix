import asyncio

import httpx
import pytest

from app.config import Settings
from app.models import Candidate, ElectionResult
from app.result_service import (
    PRE_ELECTION_PHASE,
    ResultService,
    build_zero_result,
    is_pre_election,
    official_first_round_release_open,
)


def settings(mode: str = "official") -> Settings:
    return Settings(
        telegram_bot_token="",
        election_mode=mode,
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
        webapp_url="",
        channel_id="",
    )


DIRECTORY = {
    "candidates": [
        {
            "id": "2",
            "number": "22",
            "name": "CANDIDATO DOIS",
            "ballot_name": "DOIS",
            "party": "P22",
            "party_name": "PARTIDO 22",
            "candidacy_status": "APTO",
        },
        {
            "id": "1",
            "number": "13",
            "name": "CANDIDATO UM",
            "ballot_name": "UM",
            "party": "P13",
            "party_name": "PARTIDO 13",
            "candidacy_status": "APTO",
        },
    ]
}


def test_build_zero_result_uses_real_identity_and_zero_votes():
    result = build_zero_result(
        settings(),
        DIRECTORY,
        scope="br",
        office="presidente",
        raw_url="https://resultados.tse.jus.br/oficial/ele2026/6257/dados/br/br-c0001-e006257-u.json",
    )

    assert is_pre_election(result)
    assert result.phase == PRE_ELECTION_PHASE
    assert result.election_code == 6257
    assert result.total_votes == 0
    assert result.sections_counted_pct == 0
    assert [(c.ballot_name, c.number, c.votes, c.percentage) for c in result.candidates] == [
        ("DOIS", 22, 0, 0.0),
        ("UM", 13, 0, 0.0),
    ]


class DirectoryStub:
    async def list(self, office, scope, include_poll=False):
        assert include_poll is False
        return DIRECTORY


class Tse404Stub:
    def result_url(self, scope, office="presidente"):
        return f"https://resultados.tse.jus.br/{scope}/{office}.json"

    async def fetch(self, scope, force=False, office="presidente"):
        request = httpx.Request("GET", self.result_url(scope, office))
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)


class Tse500Stub(Tse404Stub):
    async def fetch(self, scope, force=False, office="presidente"):
        request = httpx.Request("GET", self.result_url(scope, office))
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("server error", request=request, response=response)


class TseLiveStub(Tse404Stub):
    async def fetch(self, scope, force=False, office="presidente"):
        result = ElectionResult(
            scope=scope,
            election_code=6257,
            round=1,
            phase="oficial",
            generated_date="04/10/2026",
            generated_time="17:10:00",
            totalization_date="04/10/2026",
            totalization_time="17:10:00",
            generation_id="live",
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
                    name="CANDIDATO UM",
                    ballot_name="UM",
                    party="P13",
                    party_name="PARTIDO 13",
                    votes=10,
                    percentage=100.0,
                    percentage_exact=100.0,
                    vote_destination="Válido",
                    official_status="",
                    elected_flag=False,
                    vice_name="",
                    vice_party="",
                )
            ],
            raw_url=self.result_url(scope, office),
        )
        return result, True


def test_result_service_falls_back_only_on_unpublished_official_file():
    service = ResultService(settings(), Tse404Stub(), DirectoryStub())
    result, changed = asyncio.run(service.fetch("br", office="presidente", force=True))

    assert changed is False
    assert is_pre_election(result)
    assert all(c.votes == 0 and c.percentage == 0 for c in result.candidates)


def test_result_service_does_not_hide_real_server_errors():
    service = ResultService(settings(), Tse500Stub(), DirectoryStub())

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(service.fetch("br", office="presidente", force=True))


def test_result_service_switches_automatically_to_live_official_result():
    service = ResultService(settings(), TseLiveStub(), DirectoryStub())
    result, changed = asyncio.run(service.fetch("br", office="presidente", force=True))

    assert changed is True
    assert not is_pre_election(result)
    assert result.total_votes == 10
    assert result.candidates[0].votes == 10



def test_official_release_gate_matches_17h_brasilia():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Sao_Paulo")
    assert official_first_round_release_open(
        datetime(2026, 10, 4, 16, 59, 59, tzinfo=tz)
    ) is False
    assert official_first_round_release_open(
        datetime(2026, 10, 4, 17, 0, 0, tzinfo=tz)
    ) is True
