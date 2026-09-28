from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx

from .g1_polls import G1PollClient


RAW_CANDIDATES = (
    "https://raw.githubusercontent.com/herminiotorres/dossie-cidadao/main/"
    "docs/data/tse/candidatos/{scope}/{office}.json"
)
RAW_TSE_MIRROR = (
    "https://raw.githubusercontent.com/leofn/tse-candidatos-2026/main/dados/{file}"
)

OFFICE_FILES = {
    "presidente": "presidente",
    "governador": "governador",
    "senador": "senador",
    "federal": "deputado-federal",
    "estadual": "deputado-estadual",
    "distrital": "deputado-distrital",
}

POLL_OFFICES = {"presidente", "governador", "senador"}

# O TSE indeferiu definitivamente o pedido da chapa de Pablo Marçal em 11/09/2026.
# O candidato substituto do PRTB que aparece nas fontes oficiais é Leonardo Avalanche.
EXCLUDED_CANDIDATE_IDS = {"280002553884"}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in {"#NULO", "#NE", "-1", "NÃO DIVULGÁVEL"} else text


def _money_br_to_float(value: str | None) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _candidate_scope(office: str, scope: str) -> str:
    return "BR" if office == "presidente" else scope.upper()


def _office_file(office: str, scope: str) -> str:
    if office == "estadual" and scope.lower() == "df":
        return OFFICE_FILES["distrital"]
    if office not in OFFICE_FILES:
        raise ValueError("Cargo inválido.")
    return OFFICE_FILES[office]


@dataclass(slots=True)
class CacheItem:
    created: float
    value: Any


class CandidateDirectory:
    def __init__(self, g1: G1PollClient, cache_seconds: int = 1800) -> None:
        self.g1 = g1
        self.cache_seconds = cache_seconds
        self.http = httpx.AsyncClient(
            timeout=25,
            follow_redirects=True,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
            },
        )
        self._cache: dict[str, CacheItem] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _fresh(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item and time.monotonic() - item.created < self.cache_seconds:
            return item.value
        return None

    def _put(self, key: str, value: Any) -> Any:
        self._cache[key] = CacheItem(time.monotonic(), value)
        return value

    async def _json(self, url: str) -> Any:
        cached = self._fresh("json:" + url)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            cached = self._fresh("json:" + url)
            if cached is not None:
                return cached
            response = await self.http.get(url)
            response.raise_for_status()
            return self._put("json:" + url, response.json())

    async def _text(self, url: str) -> str:
        cached = self._fresh("text:" + url)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            cached = self._fresh("text:" + url)
            if cached is not None:
                return cached
            response = await self.http.get(url)
            response.raise_for_status()
            return self._put("text:" + url, response.text)

    async def _candidate_rows(self, office: str, scope: str) -> list[dict[str, Any]]:
        source_scope = _candidate_scope(office, scope)
        office_file = _office_file(office, scope)
        url = RAW_CANDIDATES.format(scope=source_scope, office=office_file)
        data = await self._json(url)
        if not isinstance(data, list):
            raise ValueError("Fonte de candidaturas retornou formato inesperado.")
        if office == "presidente":
            data = [
                row for row in data
                if str(row.get("SQ_CANDIDATO") or "") not in EXCLUDED_CANDIDATE_IDS
            ]
        return data

    @staticmethod
    def _base_candidate(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(row.get("SQ_CANDIDATO") or ""),
            "number": str(row.get("NR_CANDIDATO") or ""),
            "name": _clean(row.get("NM_CANDIDATO")),
            "ballot_name": _clean(row.get("NM_URNA_CANDIDATO")),
            "social_name": _clean(row.get("NM_SOCIAL_CANDIDATO")),
            "uf": _clean(row.get("SG_UF")),
            "office": _clean(row.get("DS_CARGO")),
            "party": _clean(row.get("SG_PARTIDO")),
            "party_name": _clean(row.get("NM_PARTIDO")),
            "coalition": _clean(row.get("NM_COLIGACAO")),
            "coalition_composition": _clean(row.get("DS_COMPOSICAO_COLIGACAO")),
            "candidacy_status": _clean(row.get("DS_SITUACAO_CANDIDATURA")),
            "gender": _clean(row.get("DS_GENERO")),
            "race": _clean(row.get("DS_COR_RACA")),
            "education": _clean(row.get("DS_GRAU_INSTRUCAO")),
            "occupation": _clean(row.get("DS_OCUPACAO")),
            "photo": "",
            "estimate_percentage": None,
            "estimate_history": [],
        }

    @staticmethod
    def _match_poll(candidate: dict[str, Any], choices: list[Any]) -> Any | None:
        names = {
            _norm(candidate.get("ballot_name")),
            _norm(candidate.get("name")),
        }
        names.discard("")
        exact = [choice for choice in choices if _norm(getattr(choice, "name", "")) in names]
        if exact:
            return exact[0]

        for choice in choices:
            p = _norm(getattr(choice, "name", ""))
            if not p:
                continue
            for c in names:
                if len(p) >= 5 and len(c) >= 5 and (p in c or c in p):
                    return choice
        return None

    async def list(
        self,
        office: str,
        scope: str,
        *,
        include_poll: bool = True,
    ) -> dict[str, Any]:
        office = office.lower().strip()
        scope = scope.lower().strip()
        if office not in OFFICE_FILES:
            raise ValueError("Cargo inválido.")

        rows = await self._candidate_rows(office, scope)
        candidates = [self._base_candidate(row) for row in rows]

        poll = None
        if include_poll and office in POLL_OFFICES:
            try:
                poll_scope = "br" if office == "presidente" else scope
                institute = "datafolha" if office == "presidente" else None
                poll = await self.g1.fetch(office, poll_scope, 1, institute)
            except Exception:
                poll = None

        if poll:
            for candidate in candidates:
                match = self._match_poll(candidate, poll.choices)
                if not match:
                    continue
                candidate["estimate_percentage"] = float(match.percentage)
                candidate["estimate_history"] = [
                    {"date": item.date, "percentage": item.percentage}
                    for item in match.history
                ]
                candidate["photo"] = str(getattr(match, "photo", "") or "")

            candidates.sort(
                key=lambda item: (
                    item["estimate_percentage"] is None,
                    -(item["estimate_percentage"] or 0),
                    item["ballot_name"],
                )
            )
        else:
            candidates.sort(key=lambda item: (item["ballot_name"], item["number"]))

        return {
            "office": office,
            "scope": "br" if office == "presidente" else scope,
            "count": len(candidates),
            "candidates": candidates,
            "estimate": (
                {
                    "available": True,
                    "source": "G1",
                    "institute": poll.institute,
                    "question": poll.question,
                    "date": poll.latest_date,
                    "margin_error_points": poll.margin_error_points,
                    "sample_size": poll.sample_size,
                    "field_period": poll.field_period,
                    "registrations": poll.registrations,
                    "source_url": poll.source_url,
                }
                if poll
                else {
                    "available": False,
                    "source": "TSE",
                    "note": "Não há pesquisa comparável integrada para este cargo/local.",
                }
            ),
            "candidate_source": {
                "label": "Dados Abertos do TSE",
                "dataset": "Candidatos 2026",
                "mirror": "dossie-cidadao",
            },
        }

    async def _csv_map(self, scope: str, kind: str) -> dict[str, list[dict[str, str]]]:
        source_scope = scope.upper()
        filename = {
            "candidate": f"consulta_cand_2026_{source_scope}.csv",
            "complement": f"consulta_cand_complementar_2026_{source_scope}.csv",
            "assets": f"bem_candidato_2026_{source_scope}.csv",
            "social": f"rede_social_candidato_2026_{source_scope}.csv",
        }[kind]
        key = f"csvmap:{filename}"
        cached = self._fresh(key)
        if cached is not None:
            return cached

        text = await self._text(RAW_TSE_MIRROR.format(file=filename))
        reader = csv.DictReader(io.StringIO(text), delimiter=";")
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in reader:
            sq = str(row.get("SQ_CANDIDATO") or "").strip()
            if sq:
                grouped.setdefault(sq, []).append(row)
        return self._put(key, grouped)

    async def detail(self, office: str, scope: str, candidate_id: str) -> dict[str, Any]:
        listing = await self.list(office, scope, include_poll=True)
        candidate = next(
            (item for item in listing["candidates"] if item["id"] == str(candidate_id)),
            None,
        )
        if candidate is None:
            raise ValueError("Candidatura não encontrada.")

        source_scope = _candidate_scope(office, scope)
        try:
            main_map, complement_map, assets_map, social_map = await asyncio.gather(
                self._csv_map(source_scope, "candidate"),
                self._csv_map(source_scope, "complement"),
                self._csv_map(source_scope, "assets"),
                self._csv_map(source_scope, "social"),
            )
        except Exception:
            main_map, complement_map, assets_map, social_map = {}, {}, {}, {}

        base = (main_map.get(str(candidate_id)) or [{}])[0]
        complement = (complement_map.get(str(candidate_id)) or [{}])[0]
        assets_rows = assets_map.get(str(candidate_id)) or []
        social_rows = social_map.get(str(candidate_id)) or []

        assets = [
            {
                "type": _clean(row.get("DS_TIPO_BEM_CANDIDATO")),
                "description": _clean(row.get("DS_BEM_CANDIDATO")),
                "value": _money_br_to_float(row.get("VR_BEM_CANDIDATO")),
            }
            for row in assets_rows
        ]
        assets.sort(key=lambda item: item["value"], reverse=True)

        candidate.update(
            {
                "birth_date": _clean(base.get("DT_NASCIMENTO")),
                "birth_state": _clean(base.get("SG_UF_NASCIMENTO")),
                "birth_city": _clean(complement.get("NM_MUNICIPIO_NASCIMENTO")),
                "civil_status": _clean(base.get("DS_ESTADO_CIVIL")),
                "nationality": _clean(complement.get("DS_NACIONALIDADE")),
                "age_at_inauguration": _clean(complement.get("NR_IDADE_DATA_POSSE")),
                "reelection": _clean(complement.get("ST_REELEICAO")),
                "judgment_status": _clean(complement.get("DS_SITUACAO_JULGAMENTO")),
                "ballot_status": _clean(complement.get("DS_SITUACAO_CANDIDATO_URNA")),
                "campaign_spending_limit": _money_br_to_float(
                    complement.get("VR_DESPESA_MAX_CAMPANHA")
                ),
                "assets": assets,
                "assets_total": round(sum(item["value"] for item in assets), 2),
                "social_links": [
                    _clean(row.get("DS_URL"))
                    for row in social_rows
                    if _clean(row.get("DS_URL"))
                ],
                "detail_source_note": (
                    "Dados pessoais, bens e redes obtidos de espelho do conjunto "
                    "Candidatos 2026 do TSE. Situações processuais podem mudar."
                ),
            }
        )
        return candidate

    async def close(self) -> None:
        await self.http.aclose()
