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
    "https://raw.githubusercontent.com/pedrorosemberg/eleicoes.metadax.org/"
    "prod/data/2026/candidatos/{scope}.json"
)
RAW_TSE_MIRROR = (
    "https://raw.githubusercontent.com/leofn/tse-candidatos-2026/main/dados/{file}"
)

RAW_TSE_ASSETS = (
    "https://raw.githubusercontent.com/pedrorosemberg/eleicoes.metadax.org/"
    "prod/data/2026/bens/{scope}.json"
)
RAW_TSE_FINANCES = (
    "https://raw.githubusercontent.com/pedrorosemberg/eleicoes.metadax.org/"
    "prod/data/2026/financas/{scope}.json"
)

OFFICE_FILES = {
    "presidente": "PRESIDENTE",
    "governador": "GOVERNADOR",
    "senador": "SENADOR",
    "federal": "DEPUTADO FEDERAL",
    "estadual": "DEPUTADO ESTADUAL",
    "distrital": "DEPUTADO DISTRITAL",
}

POLL_OFFICES = {"presidente", "governador", "senador"}

# O TSE indeferiu definitivamente o pedido da chapa de Pablo Marçal em 11/09/2026.
# O candidato substituto do PRTB que aparece nas fontes oficiais é Leonardo Avalanche.
EXCLUDED_CANDIDATE_IDS = {"280002553884", "280002554479"}


def _norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _repair_text(value: Any) -> str:
    text = str(value or "")
    # Repair common UTF-8/Windows-1252 mojibake while preserving already-correct UTF-8.
    if any(marker in text for marker in ("Ã", "Â", "â€", "â€“", "â€”")):
        try:
            repaired = text.encode("latin-1").decode("utf-8")
            if repaired.count("�") <= text.count("�"):
                text = repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return unicodedata.normalize("NFC", text)


def _clean(value: Any) -> str:
    text = _repair_text(value).strip()
    return "" if text in {"#NULO", "#NE", "-1", "NÃO DIVULGÁVEL"} else text


def _money_br_to_float(value: str | None) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _number_to_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return _money_br_to_float(value)


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
            raw = response.content
            text = None
            for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    candidate = raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
                # Prefer a decoding with no replacement characters or obvious mojibake.
                if "�" not in candidate and not any(
                    marker in candidate for marker in ("Ã£", "Ã§", "Ã¡", "Ã©", "Ã³", "Â")
                ):
                    text = candidate
                    break
                if text is None:
                    text = candidate
            if text is None:
                text = raw.decode("utf-8", errors="replace")
            return self._put("text:" + url, unicodedata.normalize("NFC", text))

    async def _candidate_rows(self, office: str, scope: str) -> list[dict[str, Any]]:
        source_scope = _candidate_scope(office, scope)
        cargo = _office_file(office, scope)
        url = RAW_CANDIDATES.format(scope=source_scope)
        data = await self._json(url)
        if not isinstance(data, list):
            raise ValueError("Fonte de candidaturas retornou formato inesperado.")

        rows = [
            row for row in data
            if str(row.get("cargo") or "").strip().upper() == cargo
        ]
        if office == "presidente":
            rows = [
                row for row in rows
                if str(row.get("sqCandidato") or "") not in EXCLUDED_CANDIDATE_IDS
            ]
        return rows

    @staticmethod
    def _base_candidate(row: dict[str, Any]) -> dict[str, Any]:
        partido = row.get("partido") or {}
        return {
            "id": str(row.get("sqCandidato") or row.get("SQ_CANDIDATO") or ""),
            "number": str(row.get("numero") or row.get("NR_CANDIDATO") or ""),
            "name": _clean(row.get("nomeCompleto") or row.get("NM_CANDIDATO")),
            "ballot_name": _clean(row.get("nomeUrna") or row.get("NM_URNA_CANDIDATO")),
            "social_name": _clean(row.get("NM_SOCIAL_CANDIDATO")),
            "uf": _clean(row.get("uf") or row.get("SG_UF")),
            "office": _clean(row.get("cargo") or row.get("DS_CARGO")),
            "party": _clean(partido.get("sigla") or row.get("SG_PARTIDO")),
            "party_name": _clean(partido.get("nome") or row.get("NM_PARTIDO")),
            "coalition": _clean(row.get("coligacao") or row.get("NM_COLIGACAO")),
            "coalition_composition": _clean(row.get("DS_COMPOSICAO_COLIGACAO")),
            "candidacy_status": _clean(row.get("situacao") or row.get("DS_SITUACAO_CANDIDATURA")),
            "judgment_status": _clean(row.get("situacaoJulgamento")),
            "gender": _clean(row.get("genero") or row.get("DS_GENERO")),
            "race": _clean(row.get("DS_COR_RACA")),
            "education": _clean(row.get("grauInstrucao") or row.get("DS_GRAU_INSTRUCAO")),
            "occupation": _clean(row.get("ocupacao") or row.get("DS_OCUPACAO")),
            "campaign_spending_limit": float(row.get("tetoGastos") or 0),
            "photo": _clean(row.get("fotoUrl")),
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
                # Preserve the higher-resolution TSE-derived official photo from
                # the candidate dataset. G1 thumbnails are used only for polling data.

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
                "mirror": "eleicoes.metadax.org",
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

    async def _asset_map(self, scope: str) -> dict[str, list[dict[str, Any]]]:
        source_scope = scope.upper()
        key = f"assetmap:{source_scope}"
        cached = self._fresh(key)
        if cached is not None:
            return cached

        data = await self._json(RAW_TSE_ASSETS.format(scope=source_scope))
        grouped: dict[str, list[dict[str, Any]]] = {}
        if isinstance(data, list):
            for row in data:
                sq = str(row.get("sqCandidato") or "").strip()
                if sq:
                    grouped.setdefault(sq, []).append(row)
        return self._put(key, grouped)

    async def _finance_map(self, scope: str) -> dict[str, dict[str, list[dict[str, Any]]]]:
        source_scope = scope.upper()
        key = f"financemap:{source_scope}"
        cached = self._fresh(key)
        if cached is not None:
            return cached

        data = await self._json(RAW_TSE_FINANCES.format(scope=source_scope))
        grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}

        if isinstance(data, dict):
            for row in data.get("receitas") or []:
                sq = str(row.get("sqCandidato") or "").strip()
                if sq:
                    grouped.setdefault(sq, {"receipts": [], "expenses": []})["receipts"].append(row)
            for row in data.get("despesas") or []:
                sq = str(row.get("sqCandidato") or "").strip()
                if sq:
                    grouped.setdefault(sq, {"receipts": [], "expenses": []})["expenses"].append(row)

        return self._put(key, grouped)

    @staticmethod
    def _top_donors(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        totals: dict[str, float] = {}
        for row in rows:
            name = _clean(row.get("doador")) or "Origem não identificada"
            totals[name] = totals.get(name, 0.0) + _number_to_float(row.get("valor"))
        return [
            {"name": name, "value": round(value, 2)}
            for name, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]

    @staticmethod
    def _top_suppliers(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
        totals: dict[str, float] = {}
        for row in rows:
            name = _clean(row.get("fornecedor")) or "Fornecedor não identificado"
            totals[name] = totals.get(name, 0.0) + _number_to_float(row.get("valor"))
        return [
            {"name": name, "value": round(value, 2)}
            for name, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]

    async def detail(self, office: str, scope: str, candidate_id: str) -> dict[str, Any]:
        listing = await self.list(office, scope, include_poll=False)
        candidate = next(
            (item for item in listing["candidates"] if item["id"] == str(candidate_id)),
            None,
        )
        if candidate is None:
            raise ValueError("Candidatura não encontrada.")

        source_scope = _candidate_scope(office, scope)
        results = await asyncio.gather(
            self._csv_map(source_scope, "candidate"),
            self._csv_map(source_scope, "complement"),
            self._csv_map(source_scope, "social"),
            self._asset_map(source_scope),
            self._finance_map(source_scope),
            return_exceptions=True,
        )

        main_map = {} if isinstance(results[0], Exception) else results[0]
        complement_map = {} if isinstance(results[1], Exception) else results[1]
        social_map = {} if isinstance(results[2], Exception) else results[2]
        assets_map = {} if isinstance(results[3], Exception) else results[3]
        finance_map = {} if isinstance(results[4], Exception) else results[4]

        base = (main_map.get(str(candidate_id)) or [{}])[0]
        complement = (complement_map.get(str(candidate_id)) or [{}])[0]
        assets_rows = assets_map.get(str(candidate_id)) or []
        social_rows = social_map.get(str(candidate_id)) or []
        finance_rows = finance_map.get(str(candidate_id)) or {"receipts": [], "expenses": []}

        assets = [
            {
                "type": "",
                "description": _clean(row.get("descricao")),
                "value": _number_to_float(row.get("valor")),
            }
            for row in assets_rows
            if _clean(row.get("descricao"))
        ]
        assets.sort(key=lambda item: item["value"], reverse=True)

        receipts = finance_rows.get("receipts") or []
        expenses = finance_rows.get("expenses") or []
        receipts_total = round(sum(_number_to_float(row.get("valor")) for row in receipts), 2)
        expenses_total = round(sum(_number_to_float(row.get("valor")) for row in expenses), 2)

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
                "campaign_spending_limit": (
                    _money_br_to_float(complement.get("VR_DESPESA_MAX_CAMPANHA"))
                    or float(candidate.get("campaign_spending_limit") or 0)
                ),
                "assets": assets,
                "assets_total": round(sum(item["value"] for item in assets), 2),
                "finance": {
                    "receipts_total": receipts_total,
                    "expenses_total": expenses_total,
                    "receipts_count": len(receipts),
                    "expenses_count": len(expenses),
                    "top_donors": self._top_donors(receipts),
                    "top_suppliers": self._top_suppliers(expenses),
                    "spending_limit": (
                        _money_br_to_float(complement.get("VR_DESPESA_MAX_CAMPANHA"))
                        or float(candidate.get("campaign_spending_limit") or 0)
                    ),
                },
                "social_links": [
                    _clean(row.get("DS_URL"))
                    for row in social_rows
                    if _clean(row.get("DS_URL"))
                ],
                "detail_source_note": (
                    "Dados pessoais, bens, redes e prestação de contas obtidos de "
                    "espelhos automatizados dos conjuntos públicos do TSE."
                ),
            }
        )
        return candidate

    async def close(self) -> None:
        await self.http.aclose()
