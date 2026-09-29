from __future__ import annotations

import httpx

from .candidates import CandidateDirectory
from .config import Settings
from .models import Candidate, ElectionResult
from .tse import TSEClient


PRE_ELECTION_PHASE = "pre_election"


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
        election_code=(
            settings.tse_election_code
            if office == "presidente"
            else settings.tse_state_election_code
        ),
        round=1,
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

    async def fetch(
        self,
        scope: str = "br",
        *,
        office: str = "presidente",
        force: bool = False,
    ) -> tuple[ElectionResult, bool]:
        try:
            return await self.tse.fetch(scope, force=force, office=office)
        except httpx.HTTPStatusError as exc:
            # The official result files are expected to be unavailable before TSE
            # starts publishing totalization. Only that publication-not-found case
            # is converted into a zero baseline; other HTTP errors remain visible.
            if self.settings.is_simulation or exc.response.status_code not in {404, 410}:
                raise

        directory = await self.candidates.list(
            office,
            scope,
            include_poll=False,
        )
        result = build_zero_result(
            self.settings,
            directory,
            scope=scope,
            office=office,
            raw_url=self.tse.result_url(scope, office=office),
        )
        return result, False
