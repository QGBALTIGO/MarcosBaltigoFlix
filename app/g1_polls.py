from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx


G1_API_BASE = "https://especiaisg1.globo/api"
G1_INDEX_URL = "https://s.glbimg.com/jo/el/2026/pesquisas-eleitorais/pesquisas-lista-localidades.json"

STATE_SLUGS = {
    "ac":"acre","al":"alagoas","ap":"amapa","am":"amazonas","ba":"bahia","ce":"ceara",
    "df":"distrito-federal","es":"espirito-santo","go":"goias","ma":"maranhao","mt":"mato-grosso",
    "ms":"mato-grosso-do-sul","mg":"minas-gerais","pa":"para","pb":"paraiba","pr":"parana",
    "pe":"pernambuco","pi":"piaui","rj":"rio-de-janeiro","rn":"rio-grande-do-norte",
    "rs":"rio-grande-do-sul","ro":"rondonia","rr":"roraima","sc":"santa-catarina",
    "sp":"sao-paulo","se":"sergipe","to":"tocantins",
}
STATE_NAMES = {
    "br":"Brasil","ac":"Acre","al":"Alagoas","ap":"Amapá","am":"Amazonas","ba":"Bahia",
    "ce":"Ceará","df":"Distrito Federal","es":"Espírito Santo","go":"Goiás","ma":"Maranhão",
    "mt":"Mato Grosso","ms":"Mato Grosso do Sul","mg":"Minas Gerais","pa":"Pará","pb":"Paraíba",
    "pr":"Paraná","pe":"Pernambuco","pi":"Piauí","rj":"Rio de Janeiro","rn":"Rio Grande do Norte",
    "rs":"Rio Grande do Sul","ro":"Rondônia","rr":"Roraima","sc":"Santa Catarina",
    "sp":"São Paulo","se":"Sergipe","to":"Tocantins",
}
OFFICES = {"presidente", "governador", "senador"}
INSTITUTES = {"datafolha":"Datafolha", "quaest":"Quaest"}
OFFICE_LABELS = {"presidente":"Presidente", "governador":"Governador", "senador":"Senador"}

_PAGE_OBJECT_RE = re.compile(r"window\.g1PesquisasEleitorais\s*=\s*\{(.*?)\}", re.S)
_METHOD_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*methodology[^"\']*["\'][^>]*>.*?'
    r'<p[^>]+class=["\'][^"\']*methodology__description[^"\']*["\'][^>]*>(.*?)</p>',
    re.S | re.I,
)


@dataclass(slots=True)
class G1HistoryPoint:
    date: str
    percentage: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class G1PollChoice:
    name: str
    party: str
    percentage: float
    history: list[G1HistoryPoint]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["history"] = [x.to_dict() for x in self.history]
        return data


@dataclass(slots=True)
class G1QuestionOption:
    code: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class G1Stratum:
    group: str
    group_slug: str
    label: str
    margin_error_points: float | None
    choices: list[G1PollChoice]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["choices"] = [c.to_dict() for c in self.choices]
        return data


@dataclass(slots=True)
class G1Poll:
    office: str
    scope: str
    round: int
    institute: str
    question: str
    question_code: str
    latest_date: str
    margin_error_points: float | None
    sample_size: int | None
    field_period: str
    registrations: list[str]
    methodology: str
    choices: list[G1PollChoice]
    available_questions: list[G1QuestionOption]
    strata: list[G1Stratum]
    strata_order: list[str]
    source_url: str
    api_url: str
    fetched_at: str
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["choices"] = [c.to_dict() for c in self.choices]
        data["available_questions"] = [q.to_dict() for q in self.available_questions]
        data["strata"] = [s.to_dict() for s in self.strata]
        return data


@dataclass(slots=True)
class Discovery:
    api_url: str
    page_url: str
    institute: str
    question_code: str
    page_id: str
    methodology: str
    sample_size: int | None
    field_period: str
    registrations: list[str]
    questions: list[G1QuestionOption]


def _canonical_institute(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower()
    if key not in INSTITUTES:
        raise ValueError("Instituto inválido. Use Datafolha ou Quaest.")
    return INSTITUTES[key]


def g1_url(office: str, scope: str = "br", round_: int = 1, institute: str | None = None) -> str:
    office = office.lower().strip()
    scope = scope.lower().strip()
    if office not in OFFICES:
        raise ValueError("Cargo inválido. Use presidente, governador ou senador.")
    if round_ not in {1, 2}:
        raise ValueError("Turno inválido.")
    canonical_institute = _canonical_institute(institute)

    if office == "presidente" and scope == "br":
        base = "https://especiaisg1.globo/politica/eleicoes/2026/pesquisas-eleitorais"
    else:
        if scope not in STATE_SLUGS:
            raise ValueError("Informe uma UF válida para este cargo.")
        base = f"https://especiaisg1.globo/{scope}/{STATE_SLUGS[scope]}/eleicoes/2026/pesquisas-eleitorais"

    url = f"{base}/{office}/{round_}-turno"
    if canonical_institute:
        url += f"/{canonical_institute}"
    return url + "/"


def _clean_html(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _extract_js_string(block: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*:\s*[\"']([^\"']*)[\"']", block)
    return match.group(1).strip() if match else ""


def parse_page_config(page_html: str) -> dict[str, str]:
    match = _PAGE_OBJECT_RE.search(page_html)
    if not match:
        raise ValueError("A página do G1 não expôs a configuração da pesquisa.")
    block = match.group(1)
    data = {
        "paginaId": _extract_js_string(block, "paginaId"),
        "turno": _extract_js_string(block, "turno"),
        "instituto": _extract_js_string(block, "instituto"),
        "tipoPergunta": _extract_js_string(block, "tipoPergunta"),
        "environment": _extract_js_string(block, "environment"),
        "uf": _extract_js_string(block, "uf"),
        "cargo": _extract_js_string(block, "cargo"),
    }
    if not data["paginaId"] or not data["tipoPergunta"]:
        raise ValueError("A configuração do G1 veio sem paginaId/tipoPergunta.")
    return data


def _parse_questions(page_html: str) -> list[G1QuestionOption]:
    pattern = re.compile(
        r'<(?:p|a)\b[^>]*\bid=["\']([A-Z][A-Z0-9-]+)["\'][^>]*>(.*?)</(?:p|a)>',
        re.S | re.I,
    )
    seen: set[str] = set()
    questions: list[G1QuestionOption] = []
    for match in pattern.finditer(page_html):
        code = match.group(1).strip().upper()
        if not any(marker in code for marker in ("-PRE-", "-GOV-", "-SEN-")):
            continue
        label = _clean_html(match.group(2))
        if code and label and code not in seen:
            seen.add(code)
            questions.append(G1QuestionOption(code=code, label=label))
    return questions


def _parse_methodology(page_html: str) -> tuple[str, int | None, str, list[str]]:
    match = _METHOD_RE.search(page_html)
    methodology = _clean_html(match.group(1)) if match else ""

    sample = None
    for pattern in (
        r"([\d.]+) entrevistas",
        r"ouviu ([\d.]+) pessoas",
        r"entrevistou ([\d.]+) pessoas",
        r"entrevistou ([\d.]+) eleitores",
    ):
        found = re.search(pattern, methodology, re.I)
        if found:
            sample = int(found.group(1).replace(".", ""))
            break

    field_period = ""
    period_patterns = (
        r"entre os dias\s+([^.;]+)",
        r"entre\s+([^.;]+?)(?:\.|,\s+com|,\s+e)",
        r"nos dias\s+([^.;]+)",
    )
    for pattern in period_patterns:
        found = re.search(pattern, methodology, re.I)
        if found:
            field_period = found.group(1).strip()
            break

    registrations = sorted(set(re.findall(
        r"\b(?:BR|AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)-\d{5}/2026\b",
        methodology,
        re.I,
    )))
    return methodology, sample, field_period, registrations


def _question_label(code: str) -> str:
    upper = code.upper()
    if upper.startswith("APROVACAO"):
        return "Aprovação do governo"
    if upper.startswith("AVALIACAO"):
        return "Avaliação do governo"
    if upper.startswith("REJEICAO"):
        return "Rejeição"
    if upper.startswith("SEGTURNO"):
        scenario = upper.rsplit("-", 1)[-1].lstrip("0") or "1"
        return f"Segundo turno — cenário {scenario}"
    if upper.startswith("ESTIMULADA"):
        if "VOTO-1" in upper:
            return "Estimulada — voto 1"
        if "VOTO-2" in upper:
            return "Estimulada — voto 2"
        return "Estimulada"
    if upper.startswith("ESPONT"):
        return "Espontânea"
    if "VALID" in upper:
        return "Votos válidos"
    if "REJE" in upper:
        return "Rejeição"
    if "SEGUNDO" in upper or "2T" in upper:
        return "Segundo turno"
    return code


def _percent(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(number * 100.0 if abs(number) <= 1.0 else number, 2)


def _series_choices(
    series_list: list[dict[str, Any]],
    party_by_name: dict[str, str],
) -> tuple[list[G1PollChoice], set[str]]:
    choices: list[G1PollChoice] = []
    all_dates: set[str] = set()
    for series in series_list:
        name = str(series.get("option") or "").strip()
        if not name:
            continue
        points: list[G1HistoryPoint] = []
        for item in series.get("values") or []:
            raw_date = str(item.get("date") or "").strip()
            if not raw_date:
                continue
            day = raw_date[:10]
            all_dates.add(day)
            points.append(G1HistoryPoint(day, _percent(item.get("value"))))
        points.sort(key=lambda x: x.date)
        if points:
            choices.append(G1PollChoice(
                name=name,
                party=party_by_name.get(name, ""),
                percentage=points[-1].percentage,
                history=points,
            ))
    return choices, all_dates


def parse_g1_payload(
    payload: dict[str, Any],
    discovery: Discovery,
    office: str,
    scope: str,
    round_: int,
) -> G1Poll:
    resultado = payload.get("resultado") or {}
    scenarios = resultado.get("cenarios") or []
    if not scenarios:
        raise ValueError("O G1 não retornou cenários para esta pesquisa.")

    scenario = next(
        (
            x for x in scenarios
            if str(x.get("bandeira_slug", "")).lower() == "total"
            or str(x.get("variavel_cruzamento", "")).lower() == "total"
        ),
        scenarios[0],
    )
    question = scenario.get("pergunta") or {}
    question_code = str(question.get("codigo") or discovery.question_code or "")
    question_label = str(question.get("conteudo") or "").strip() or _question_label(question_code)

    party_by_name: dict[str, str] = {}
    for option in scenario.get("opcoes_resposta") or []:
        name = str(option.get("nome") or "").strip()
        party = option.get("partido") or {}
        if name:
            party_by_name[name] = str(party.get("sigla") or "").strip()

    choices, all_dates = _series_choices(scenario.get("data") or [], party_by_name)
    latest_date = max(all_dates) if all_dates else ""
    if latest_date:
        for choice in choices:
            exact = next((x for x in reversed(choice.history) if x.date == latest_date), None)
            if exact is not None:
                choice.percentage = exact.percentage

    margin = scenario.get("margem")
    try:
        margin_points = float(margin) if margin not in (None, "") else None
    except (TypeError, ValueError):
        margin_points = None

    strata: list[G1Stratum] = []
    for item in resultado.get("estratos") or []:
        stratum_choices, _ = _series_choices(item.get("data") or [], party_by_name)
        raw_margin = item.get("margem")
        try:
            stratum_margin = float(raw_margin) if raw_margin not in (None, "") else None
        except (TypeError, ValueError):
            stratum_margin = None
        strata.append(G1Stratum(
            group=str(item.get("bandeira") or "").strip(),
            group_slug=str(item.get("bandeira_slug") or "").strip(),
            label=str(item.get("variavel_cruzamento") or "").strip(),
            margin_error_points=stratum_margin,
            choices=stratum_choices,
        ))

    strata_order = [str(x).strip() for x in (resultado.get("ordenacao_estratos") or []) if str(x).strip()]

    fingerprint_raw = json.dumps({
        "office": office,
        "scope": scope,
        "round": round_,
        "institute": discovery.institute,
        "question": question_code,
        "latest_date": latest_date,
        "choices": [(c.name, c.percentage) for c in choices],
    }, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:24]

    return G1Poll(
        office=office,
        scope=scope,
        round=round_,
        institute=discovery.institute,
        question=question_label,
        question_code=question_code,
        latest_date=latest_date,
        margin_error_points=margin_points,
        sample_size=discovery.sample_size,
        field_period=discovery.field_period,
        registrations=discovery.registrations,
        methodology=discovery.methodology,
        choices=choices,
        available_questions=discovery.questions,
        strata=strata,
        strata_order=strata_order,
        source_url=discovery.page_url,
        api_url=discovery.api_url,
        fetched_at=datetime.now(ZoneInfo("America/Campo_Grande")).isoformat(timespec="seconds"),
        fingerprint=fingerprint,
    )


def _date_br(iso_day: str) -> str:
    if not iso_day:
        return "data não informada"
    try:
        return datetime.strptime(iso_day[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso_day


def _fmt_percent(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:.1f}".replace(".", ",") + "%"


def _choice_delta(choice: G1PollChoice, latest_date: str) -> str:
    points = [x for x in choice.history if x.date <= latest_date]
    points.sort(key=lambda x: x.date)
    if len(points) < 2:
        return ""
    current = points[-1].percentage
    previous = points[-2].percentage
    delta = round(current - previous, 2)
    if abs(delta) < 0.001:
        return " • sem variação na rodada anterior"
    sign = "+" if delta > 0 else ""
    value = f"{sign}{delta:.1f}".replace(".", ",")
    return f" • {value} p.p. vs. rodada anterior"


def format_g1_poll(poll: G1Poll, max_choices: int = 20) -> str:
    place = STATE_NAMES.get(poll.scope, poll.scope.upper())
    office = OFFICE_LABELS.get(poll.office, poll.office.title())
    lines = [
        f"<b>PESQUISA ELEITORAL • {html_lib.escape(office.upper())}</b>",
        f"<b>{html_lib.escape(place)}</b> • {poll.round}º turno • {html_lib.escape(poll.institute)}",
        f"{html_lib.escape(poll.question)} • rodada de <b>{_date_br(poll.latest_date)}</b>",
        "",
        "ℹ️ <b>Pesquisa de intenção de voto — não é apuração de votos.</b>",
        "",
    ]

    for choice in poll.choices[:max_choices]:
        party = f" • {html_lib.escape(choice.party)}" if choice.party else ""
        lines.append(
            f"<b>{html_lib.escape(choice.name)}</b>{party}: "
            f"<b>{_fmt_percent(choice.percentage)}</b>{_choice_delta(choice, poll.latest_date)}"
        )

    details: list[str] = []
    if poll.sample_size:
        details.append(f"{poll.sample_size:,}".replace(",", ".") + " entrevistados")
    if poll.margin_error_points is not None:
        details.append(f"margem de erro ±{poll.margin_error_points:g} p.p.")
    if poll.field_period:
        details.append(f"campo: {html_lib.escape(poll.field_period)}")
    if details:
        lines.extend(["", "<b>Metodologia:</b> " + " • ".join(details)])
    if poll.registrations:
        lines.append("<b>Registro(s) no TSE:</b> " + ", ".join(html_lib.escape(x) for x in poll.registrations))

    lines.extend([
        "",
        "<b>Fonte:</b> G1 • dados da pesquisa publicada pelo instituto",
        f'<a href="{html_lib.escape(poll.source_url, quote=True)}">Abrir especial do G1</a>',
    ])
    return "\n".join(lines)


class G1PollClient:
    def __init__(self, cache_seconds: int = 600, discovery_seconds: int = 21600) -> None:
        self.cache_seconds = cache_seconds
        self.discovery_seconds = discovery_seconds
        self.http = httpx.AsyncClient(
            timeout=25,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; ResultadoEleicoesBot/1.0; +https://t.me/ResultadoEleicoes)",
            },
        )
        self._discoveries: dict[str, tuple[float, Discovery]] = {}
        self._cache: dict[str, tuple[float, G1Poll]] = {}

    @staticmethod
    def _key(
        office: str,
        scope: str,
        round_: int,
        institute: str | None,
        question_code: str | None = None,
    ) -> str:
        return (
            f"{office.lower()}:{scope.lower()}:{round_}:{(institute or '').lower()}:"
            f"{(question_code or '').upper()}"
        )

    async def _discover(
        self,
        office: str,
        scope: str,
        round_: int,
        institute: str | None,
        question_code: str | None = None,
        force: bool = False,
    ) -> Discovery:
        key = self._key(office, scope, round_, institute, question_code)
        existing = self._discoveries.get(key)
        now = time.monotonic()
        if existing and not force and now - existing[0] < self.discovery_seconds:
            return existing[1]

        page_url = g1_url(office, scope, round_, institute)
        response = await self.http.get(page_url, headers={"Accept": "text/html,application/xhtml+xml"})
        response.raise_for_status()
        page_html = response.text
        config = parse_page_config(page_html)

        page_id = config["paginaId"]
        questions = _parse_questions(page_html)
        default_question_code = config["tipoPergunta"].upper()
        selected_question_code = (question_code or default_question_code).strip().upper()
        available_codes = {q.code for q in questions}
        if questions and selected_question_code not in available_codes:
            raise ValueError("Pergunta indisponível para esta pesquisa.")
        if not any(q.code == default_question_code for q in questions):
            questions.insert(0, G1QuestionOption(
                code=default_question_code,
                label=_question_label(default_question_code),
            ))
        actual_institute = config["instituto"] or _canonical_institute(institute) or "G1"
        query = urlencode({"tipo_pergunta": selected_question_code, "instituto": actual_institute})
        api_url = f"{G1_API_BASE}/pesquisas-eleitorais/graficos/{page_id}/?{query}"

        methodology, sample, period, regs = _parse_methodology(page_html)
        discovery = Discovery(
            api_url=api_url,
            page_url=page_url,
            institute=actual_institute,
            question_code=selected_question_code,
            page_id=page_id,
            methodology=methodology,
            sample_size=sample,
            field_period=period,
            registrations=regs,
            questions=questions,
        )
        self._discoveries[key] = (time.monotonic(), discovery)
        return discovery

    async def fetch(
        self,
        office: str,
        scope: str = "br",
        round_: int = 1,
        institute: str | None = None,
        question_code: str | None = None,
        force: bool = False,
    ) -> G1Poll:
        office = office.lower().strip()
        scope = scope.lower().strip()
        canonical = _canonical_institute(institute)
        g1_url(office, scope, round_, canonical)

        key = self._key(office, scope, round_, canonical, question_code)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and not force and now - cached[0] < self.cache_seconds:
            return cached[1]

        discovery = await self._discover(
            office, scope, round_, canonical, question_code, force=force
        )
        response = await self.http.get(
            discovery.api_url,
            headers={"Accept": "application/json", "Cache-Control": "no-cache" if force else "max-age=0"},
        )
        if response.status_code >= 400 and not force:
            discovery = await self._discover(
                office, scope, round_, canonical, question_code, force=True
            )
            response = await self.http.get(discovery.api_url, headers={"Accept": "application/json"})
        response.raise_for_status()
        poll = parse_g1_payload(response.json(), discovery, office, scope, round_)
        self._cache[key] = (time.monotonic(), poll)
        return poll

    async def catalog(self) -> dict[str, Any]:
        response = await self.http.get(G1_INDEX_URL, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.http.aclose()
