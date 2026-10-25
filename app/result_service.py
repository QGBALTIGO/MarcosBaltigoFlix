from __future__ import annotations

import httpx
import asyncio
import json
from pathlib import Path
from time import monotonic
from datetime import date, datetime, time as dt_time
from zoneinfo import ZoneInfo

from .candidates import CandidateDirectory
from .config import Settings
from .models import Candidate, ElectionResult
from .tse import TSEClient


PRE_ELECTION_PHASE = "pre_election"
BRASILIA = ZoneInfo("America/Sao_Paulo")
FIRST_ROUND_DATE = date(2026, 10, 4)
SECOND_ROUND_DATE = date(2026, 10, 25)
RUNOFF = json.loads((Path(__file__).parent / "data/second_round_2026.json").read_text(encoding="utf-8"))


def is_pre_election(result: ElectionResult) -> bool:
    return result.phase == PRE_ELECTION_PHASE


def build_zero_result(
    settings: Settings,
    directory: dict,
    *,
    scope: str,
    office: str,
    raw_url: str,
) -> ElectionResult:
    candidates: list[Candidate] = []
    for index, item in enumerate(directory.get("candidates") or [], start=1):
        raw_number = str(item.get("number") or "").strip()
        try:
            number = int(raw_number) if raw_number else None
        except ValueError:
            number = None

        candidates.append(
            Candidate(
                number=number,
                sequence=index,
                candidate_id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                ballot_name=str(item.get("ballot_name") or item.get("name") or ""),
                party=str(item.get("party") or ""),
                party_name=str(item.get("party_name") or ""),
                votes=0,
                percentage=0.0,
                percentage_exact=0.0,
                vote_destination="",
                official_status=str(
                    item.get("candidacy_status")
                    or item.get("judgment_status")
                    or ""
                ),
                elected_flag=False,
                vice_name="",
                vice_party="",
            )
        )

    # Before official totalization starts, keep a neutral directory order instead of
    # implying a ranking among candidacies that all have zero votes.
    candidates.sort(
        key=lambda item: (
            item.ballot_name.casefold(),
            item.number if item.number is not None else 999999,
        )
    )

    return ElectionResult(
        scope=scope.lower(),
        election_code=settings.code_for_office(office),
        round=settings.round_for_office(office),
        phase=PRE_ELECTION_PHASE,
        generated_date="",
        generated_time="",
        totalization_date="",
        totalization_time="",
        generation_id=f"pre-election:{office}:{scope.lower()}",
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
        candidates=candidates,
        raw_url=raw_url,
    )


def official_release_open(round_: int = 1, now: datetime | None = None) -> bool:
    now = now or datetime.now(BRASILIA)
    election_date = SECOND_ROUND_DATE if round_ == 2 else FIRST_ROUND_DATE
    if now.date() < election_date:
        return False
    if now.date() > election_date:
        return True
    release = datetime.combine(election_date, dt_time(17, 0), tzinfo=BRASILIA)
    return now >= release


def official_first_round_release_open(now: datetime | None = None) -> bool:
    return official_release_open(1, now)


class ResultService:
    """Official TSE result feed with a real-candidacy zero baseline before publication."""

    def __init__(
        self,
        settings: Settings,
        tse: TSEClient,
        candidates: CandidateDirectory,
    ) -> None:
        self.settings = settings
        self.tse = tse
        self.candidates = candidates
        self._directories = {}
        self._directory_locks = {}

    async def directory(self, office: str, scope: str) -> dict:
        if self.settings.election_round != 2 or office not in {"presidente", "governador"} or self.settings.is_simulation:
            return await self.candidates.list(office, scope, include_poll=False)
        scope = scope.lower()
        if office == "governador" and scope not in RUNOFF["governor_states"]:
            return {"office": office, "scope": scope, "count": 0, "candidates": [], "note": "Não há segundo turno para governador nesta UF.", "round": 2}
        key = office + ":" + scope
        async with self._directory_locks.setdefault(key, asyncio.Lock()):
            cached = self._directories.get(key)
            if cached and monotonic() - cached[0] < 600:
                return cached[1]
            rows = []
            try:
                result, _ = await self.tse.fetch(scope, office=office)
                rows = [{"id": c.candidate_id, "number": str(c.number or ""), "name": c.name, "ballot_name": c.ballot_name, "party": c.party, "party_name": c.party_name} for c in result.candidates]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 410}: raise
            except (httpx.TimeoutException, httpx.NetworkError):
                if office != "presidente": raise
            if not rows and office == "presidente":
                rows = RUNOFF["president_candidates"]
            rows = [{**item, "photo": "", "estimate_percentage": None, "estimate_history": [], "uf": scope.upper(), "office": office} for item in rows]
            data = {"office": office, "scope": scope, "round": 2, "count": len(rows), "candidates": rows, "estimate": {"available": False}, "candidate_source": {"label": "TSE · segundo turno", "url": self.tse.result_url(scope, office=office)}}
            self._directories[key] = (monotonic(), data)
            return data

    async def fetch(
        self,
        scope: str = "br",
        *,
        office: str = "presidente",
        force: bool = False,
    ) -> tuple[ElectionResult, bool]:
        # The TSE does not release the 1st-round result files before 17:00 Brasília
        # on election day. Return a local zero baseline until then instead of
        # repeatedly requesting a URL that is expected to be 404.
        if (
            (not force or self.settings.election_round == 2)
            and not self.settings.is_simulation
            and not official_release_open(self.settings.round_for_office(office))
        ):
            directory_scope = "br" if office == "presidente" and scope.lower() == "zz" else scope
            directory = await self.directory(office, directory_scope)
            return (
                build_zero_result(
                    self.settings,
                    directory,
                    scope=scope,
                    office=office,
                    raw_url=self.tse.result_url(scope, office=office),
                ),
                False,
            )

        if self.settings.election_round == 2 and office == "governador" and not self.settings.is_simulation and scope.lower() not in RUNOFF["governor_states"]:
            raise ValueError("Não há segundo turno para governador nesta UF.")
        try:
            return await self.tse.fetch(scope, force=force, office=office)
        except httpx.HTTPStatusError as exc:
            # The official result files are expected to be unavailable before TSE
            # starts publishing totalization. Only that publication-not-found case
            # is converted into a zero baseline; other HTTP errors remain visible.
            if self.settings.is_simulation or exc.response.status_code not in {404, 410}:
                raise

        directory_scope = "br" if office == "presidente" and scope.lower() == "zz" else scope
        directory = await self.directory(office, directory_scope)
        result = build_zero_result(
            self.settings,
            directory,
            scope=scope,
            office=office,
            raw_url=self.tse.result_url(scope, office=office),
        )
        return result, False
