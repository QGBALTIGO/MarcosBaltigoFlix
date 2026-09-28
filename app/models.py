from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class Candidate:
    number: int | None
    sequence: int | None
    candidate_id: str
    name: str
    ballot_name: str
    party: str
    party_name: str
    votes: int
    percentage: float
    percentage_exact: float | None
    vote_destination: str
    official_status: str
    elected_flag: bool
    vice_name: str
    vice_party: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ElectionResult:
    scope: str
    election_code: int
    round: int
    phase: str
    generated_date: str
    generated_time: str
    totalization_date: str
    totalization_time: str
    generation_id: str
    disclosure_enabled: bool
    final_totalization: bool
    progress_status: str
    mathematically_defined: str
    no_elected_assignment: bool
    no_elected_reasons: list[str]
    sections_total: int
    sections_counted: int
    sections_pending: int
    sections_counted_pct: float
    electorate_total: int
    turnout: int
    turnout_pct: float
    abstention: int
    abstention_pct: float
    total_votes: int
    valid_votes: int
    blank_votes: int
    null_votes: int
    void_votes: int
    void_sub_judice_votes: int
    candidates: list[Candidate]
    raw_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidates"] = [c.to_dict() for c in self.candidates]
        return data
