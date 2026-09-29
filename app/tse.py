from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import httpx

from .config import Settings
from .models import Candidate, ElectionResult

OFFICE_CARGO_CODES = {
    "presidente": 1,
    "governador": 3,
    "senador": 5,
    "federal": 6,
    "estadual": 7,
    "distrital": 8,
}

VALID_UFS = {
    "ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms",
    "mg", "pa", "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc",
    "sp", "se", "to",
}


def _i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _f(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(".", "").replace(",", ".") if "," in str(value) else str(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _candidate_iter(
    data: dict[str, Any],
    cargo_code: int = 1,
) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield (candidate, party) from the EA20 hierarchy carg -> agr -> par -> cand."""
    for cargo in data.get("carg") or []:
        if _i(cargo.get("cd")) != cargo_code:
            continue
        for aggregation in cargo.get("agr") or []:
            for party in aggregation.get("par") or []:
                for candidate in party.get("cand") or []:
                    yield candidate, party


def parse_ea20(
    data: dict[str, Any],
    scope: str,
    raw_url: str = "",
    cargo_code: int = 1,
) -> ElectionResult:
    seen: set[str] = set()
    candidates: list[Candidate] = []

    for cand, party in _candidate_iter(data, cargo_code=cargo_code):
        candidate_id = _text(cand.get("sqcand")) or f"{cand.get('n')}:{cand.get('nm')}"
        if candidate_id in seen:
            continue
        seen.add(candidate_id)

        vice_name = ""
        vice_party = ""
        for vice in cand.get("vs") or []:
            if vice.get("tp") == "v":
                vice_name = _text(vice.get("nmu") or vice.get("nm"))
                vice_party = _text(vice.get("sgp"))
                break

        candidates.append(
            Candidate(
                number=_i(cand.get("n"), 0) or None,
                sequence=_i(cand.get("seq"), 0) or None,
                candidate_id=candidate_id,
                name=_text(cand.get("nm")),
                ballot_name=_text(cand.get("nmu") or cand.get("nm")),
                party=_text(party.get("sg")),
                party_name=_text(party.get("nm")),
                votes=_i(cand.get("vap")),
                percentage=_f(cand.get("pvap")),
                percentage_exact=_f(cand.get("pvapn")) if cand.get("pvapn") not in (None, "") else None,
                vote_destination=_text(cand.get("dvt")),
                official_status=_text(cand.get("st")),
                elected_flag=_text(cand.get("e")).lower() == "s",
                vice_name=vice_name,
                vice_party=vice_party,
            )
        )

    # A ordem por votos é apenas apresentação do dado objetivo da apuração.
    candidates.sort(key=lambda item: (-item.votes, item.sequence or 9999, item.number or 999))

    s = data.get("s") or {}
    e = data.get("e") or {}
    v = data.get("v") or {}
    md = _text(data.get("md")).lower()

    return ElectionResult(
        scope=scope.lower(),
        election_code=_i(data.get("ele")),
        round=_i(data.get("t"), 1),
        phase=_text(data.get("f")),
        generated_date=_text(data.get("dg")),
        generated_time=_text(data.get("hg")),
        totalization_date=_text(data.get("dt")),
        totalization_time=_text(data.get("ht")),
        generation_id=_text(data.get("idg")),
        disclosure_enabled=_text(data.get("dv")).lower() == "s",
        final_totalization=_text(data.get("tf")).lower() == "s",
        progress_status=_text(data.get("and")),
        mathematically_defined=md,
        no_elected_assignment=_text(data.get("esae")).lower() == "s",
        no_elected_reasons=[_text(x) for x in (data.get("mnae") or []) if _text(x)],
        sections_total=_i(s.get("ts")),
        sections_counted=_i(s.get("st")),
        sections_pending=_i(s.get("snt")),
        sections_counted_pct=_f(s.get("pst")),
        electorate_total=_i(e.get("te")),
        turnout=_i(e.get("c")),
        turnout_pct=_f(e.get("pc")),
        abstention=_i(e.get("a")),
        abstention_pct=_f(e.get("pa")),
        total_votes=_i(v.get("tv")),
        valid_votes=_i(v.get("vv")),
        blank_votes=_i(v.get("vb")),
        null_votes=_i(v.get("tvn")),
        void_votes=_i(v.get("van")),
        void_sub_judice_votes=_i(v.get("vansj")),
        candidates=candidates,
        raw_url=raw_url,
    )


class TSEClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=settings.request_timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "Eleicoes2026Bot/1.0 (+dados-publicos-TSE)",
            },
            follow_redirects=True,
        )
        self._etag: dict[str, str] = {}
        self._last_modified: dict[str, str] = {}
        self._cache: dict[str, ElectionResult] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def result_url(self, scope: str = "br", office: str = "presidente") -> str:
        scope = scope.lower().strip()
        office = office.lower().strip()
        if scope != "br" and scope not in VALID_UFS:
            raise ValueError("UF inválida. Use BR ou uma sigla como MS, SP, RJ.")
        if office not in OFFICE_CARGO_CODES:
            raise ValueError("Cargo inválido.")
        if office != "presidente" and scope == "br":
            raise ValueError("Este cargo exige uma UF.")

        actual_office = "distrital" if office == "estadual" and scope == "df" else office
        cargo_code = OFFICE_CARGO_CODES[actual_office]
        code = (
            self.settings.tse_election_code
            if office == "presidente"
            else self.settings.tse_state_election_code
        )
        ecode = f"{code:06d}"
        cargo = f"{cargo_code:04d}"
        filename = f"{scope}-c{cargo}-e{ecode}-u.json"
        return (
            f"{self.settings.tse_base_url}/{self.settings.tse_environment}/"
            f"{self.settings.tse_cycle}/{code}/dados/{scope}/{filename}"
        )

    async def fetch(
        self,
        scope: str = "br",
        force: bool = False,
        office: str = "presidente",
    ) -> tuple[ElectionResult, bool]:
        scope = scope.lower().strip()
        office = office.lower().strip()
        actual_office = "distrital" if office == "estadual" and scope == "df" else office
        cargo_code = OFFICE_CARGO_CODES.get(actual_office)
        if cargo_code is None:
            raise ValueError("Cargo inválido.")
        cache_key = f"{office}:{scope}"
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            url = self.result_url(scope, office=office)
            headers: dict[str, str] = {}
            if not force:
                if self._etag.get(cache_key):
                    headers["If-None-Match"] = self._etag[cache_key]
                if self._last_modified.get(cache_key):
                    headers["If-Modified-Since"] = self._last_modified[cache_key]

            response = await self.client.get(url, headers=headers)
            if response.status_code == 304:
                cached = self._cache.get(cache_key)
                if cached is None:
                    # Situação defensiva: um 304 sem cache local não é útil; refaça sem validadores.
                    response = await self.client.get(url)
                else:
                    return cached, False

            response.raise_for_status()
            data = response.json()
            result = parse_ea20(data, scope=scope, raw_url=url, cargo_code=cargo_code)

            old = self._cache.get(cache_key)
            changed = old is None or old.generation_id != result.generation_id or old.totalization_time != result.totalization_time
            self._cache[cache_key] = result
            if response.headers.get("etag"):
                self._etag[cache_key] = response.headers["etag"]
            if response.headers.get("last-modified"):
                self._last_modified[cache_key] = response.headers["last-modified"]
            return result, changed

    def cached(self, scope: str = "br", office: str = "presidente") -> ElectionResult | None:
        return self._cache.get(f"{office.lower()}:{scope.lower()}")

    async def close(self) -> None:
        await self.client.aclose()
