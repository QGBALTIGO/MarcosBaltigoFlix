from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import Browser, Playwright, async_playwright, TimeoutError as PlaywrightTimeoutError


STATE_SLUGS = {
    "ac":"acre","al":"alagoas","ap":"amapa","am":"amazonas","ba":"bahia","ce":"ceara",
    "df":"distrito-federal","es":"espirito-santo","go":"goias","ma":"maranhao","mt":"mato-grosso",
    "ms":"mato-grosso-do-sul","mg":"minas-gerais","pa":"para","pb":"paraiba","pr":"parana",
    "pe":"pernambuco","pi":"piaui","rj":"rio-de-janeiro","rn":"rio-grande-do-norte",
    "rs":"rio-grande-do-sul","ro":"rondonia","rr":"roraima","sc":"santa-catarina",
    "sp":"sao-paulo","se":"sergipe","to":"tocantins",
}
OFFICES = {"presidente","governador","senador"}
INSTITUTES = {"datafolha","quaest"}
G1_API_MARKER = "/api/pesquisas-eleitorais/graficos/"


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
    source_url: str
    api_url: str
    fetched_at: str
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["choices"] = [c.to_dict() for c in self.choices]
        return data


@dataclass(slots=True)
class Discovery:
    api_url: str
    page_url: str
    institute: str
    methodology: str
    sample_size: int | None
    field_period: str
    registrations: list[str]
    methodology_margin: str


def g1_url(office: str, scope: str = "br", round_: int = 1, institute: str | None = None) -> str:
    office = office.lower().strip()
    scope = scope.lower().strip()
    if office not in OFFICES:
        raise ValueError("Cargo inválido. Use presidente, governador ou senador.")
    if round_ not in {1, 2}:
        raise ValueError("Turno inválido.")
    inst = (institute or "").lower().strip()
    if inst and inst not in INSTITUTES:
        raise ValueError("Instituto inválido. Use Datafolha ou Quaest.")

    if office == "presidente" and scope == "br":
        base = "https://especiaisg1.globo/politica/eleicoes/2026/pesquisas-eleitorais"
    else:
        if scope not in STATE_SLUGS:
            raise ValueError("Informe uma UF válida para este cargo.")
        base = f"https://especiaisg1.globo/{scope}/{STATE_SLUGS[scope]}/eleicoes/2026/pesquisas-eleitorais"

    parts = [base, office, f"{round_}-turno"]
    if inst:
        parts.append(inst)
    parts.append("internal")
    return "/".join(parts) + "/"


def _clean_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", x).strip() for x in text.splitlines() if x.strip()]


def _parse_methodology(lines: list[str]) -> tuple[str, str, int | None, str, list[str]]:
    try:
        idx = next(i for i, x in enumerate(lines) if x.lower() == "metodologia")
    except StopIteration:
        return "", "", None, "", []
    end = len(lines)
    for i in range(idx + 1, len(lines)):
        if lines[i].lower().startswith("sobre o pesquisas eleitorais"):
            end = i
            break
    methodology = " ".join(lines[idx + 1:end]).strip()
    margin_match = re.search(
        r"margem de erro(?: máxima para o total da amostra)?\s+(?:é|de)\s+([^.,]+(?:[.,]\d+)?\s+(?:pontos?|p\.p\.))",
        methodology,
        re.I,
    )
    margin = margin_match.group(1).strip() if margin_match else ""
    sample = None
    for pattern in (
        r"([\d.]+) entrevistas",
        r"ouviu ([\d.]+) pessoas",
        r"entrevistou ([\d.]+) pessoas",
        r"entrevistou ([\d.]+) eleitores",
    ):
        m = re.search(pattern, methodology, re.I)
        if m:
            sample = int(m.group(1).replace(".", ""))
            break
    period = ""
    m_period = re.search(r"(?:entre|nos dias)\s+(?:os dias\s+)?([^.]*)", methodology, re.I)
    if m_period:
        period = m_period.group(1).strip()
    regs = sorted(set(re.findall(
        r"\b(?:BR|AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)-\d{5}/2026\b",
        methodology,
        re.I,
    )))
    return methodology, margin, sample, period, regs


def _question_label(code: str) -> str:
    upper = code.upper()
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


def _parse_payload(payload: dict[str, Any], discovery: Discovery, office: str, scope: str, round_: int) -> G1Poll:
    resultado = payload.get("resultado") or {}
    scenarios = resultado.get("cenarios") or []
    if not scenarios:
        raise ValueError("O G1 não retornou cenários para esta pesquisa.")

    scenario = next(
        (x for x in scenarios if str(x.get("bandeira_slug", "")).lower() == "total"),
        scenarios[0],
    )
    question = scenario.get("pergunta") or {}
    question_code = str(question.get("codigo") or "")
    question_label = str(question.get("conteudo") or "").strip() or _question_label(question_code)

    party_by_name: dict[str, str] = {}
    for option in scenario.get("opcoes_resposta") or []:
        name = str(option.get("nome") or "").strip()
        party = option.get("partido") or {}
        if name:
            party_by_name[name] = str(party.get("sigla") or "").strip()

    choices: list[G1PollChoice] = []
    all_dates: set[str] = set()
    for series in scenario.get("data") or []:
        name = str(series.get("option") or "").strip()
        if not name:
            continue
        points: list[G1HistoryPoint] = []
        for item in series.get("values") or []:
            date = str(item.get("date") or "").strip()
            if not date:
                continue
            day = date[:10]
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

    latest_date = max(all_dates) if all_dates else ""
    if latest_date:
        for choice in choices:
            exact = next((x for x in reversed(choice.history) if x.date == latest_date), None)
            if exact is not None:
                choice.percentage = exact.percentage

    # Mantém a ordem apresentada pela própria fonte; não cria ranking editorial.
    margin = scenario.get("margem")
    try:
        margin_points = float(margin) if margin not in (None, "") else None
    except (TypeError, ValueError):
        margin_points = None

    institute = discovery.institute or "G1"
    fingerprint_raw = json.dumps({
        "office": office,
        "scope": scope,
        "round": round_,
        "institute": institute,
        "question": question_code,
        "latest_date": latest_date,
        "choices": [(c.name, c.percentage) for c in choices],
    }, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:24]

    return G1Poll(
        office=office,
        scope=scope,
        round=round_,
        institute=institute,
        question=question_label,
        question_code=question_code,
        latest_date=latest_date,
        margin_error_points=margin_points,
        sample_size=discovery.sample_size,
        field_period=discovery.field_period,
        registrations=discovery.registrations,
        methodology=discovery.methodology,
        choices=choices,
        source_url=discovery.page_url,
        api_url=discovery.api_url,
        fetched_at=datetime.now(ZoneInfo("America/Campo_Grande")).isoformat(timespec="seconds"),
        fingerprint=fingerprint,
    )


class G1PollClient:
    def __init__(self, cache_seconds: int = 600, discovery_seconds: int = 21600) -> None:
        self.cache_seconds = cache_seconds
        self.discovery_seconds = discovery_seconds
        self.http = httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                "User-Agent": "Mozilla/5.0 (compatible; ResultadoEleicoesBot/1.0; +https://t.me/ResultadoEleicoes)",
            },
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = asyncio.Lock()
        self._discoveries: dict[str, tuple[float, Discovery]] = {}
        self._cache: dict[str, tuple[float, G1Poll]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _key(office: str, scope: str, round_: int, institute: str | None) -> str:
        return f"{office.lower()}:{scope.lower()}:{round_}:{(institute or '').lower()}"

    async def _ensure_browser(self) -> Browser:
        if self._browser:
            return self._browser
        async with self._browser_lock:
            if self._browser:
                return self._browser
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            return self._browser

    async def _discover(self, office: str, scope: str, round_: int, institute: str | None, force: bool = False) -> Discovery:
        key = self._key(office, scope, round_, institute)
        existing = self._discoveries.get(key)
        now = time.monotonic()
        if existing and not force and now - existing[0] < self.discovery_seconds:
            return existing[1]

        page_url = g1_url(office, scope, round_, institute)
        browser = await self._ensure_browser()
        context = await browser.new_context(
            locale="pt-BR",
            timezone_id="America/Campo_Grande",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        api_urls: list[str] = []
        api_event = asyncio.Event()

        def on_response(response) -> None:
            if G1_API_MARKER in response.url:
                api_urls.append(response.url)
                api_event.set()

        page.on("response", on_response)
        try:
            response = await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            if response and response.status >= 400:
                raise ValueError(f"Página do G1 retornou HTTP {response.status}.")
            try:
                await asyncio.wait_for(api_event.wait(), timeout=18)
            except asyncio.TimeoutError:
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except PlaywrightTimeoutError:
                    pass
            await page.wait_for_timeout(600)
            body = await page.locator("body").inner_text(timeout=15000)
            lines = _clean_lines(body)
            methodology, methodology_margin, sample, period, regs = _parse_methodology(lines)
            unique = list(dict.fromkeys(api_urls))
            if not unique:
                raise ValueError("A página do G1 não expôs a API de gráficos.")
            # A página selecionada carrega primeiro o gráfico principal da pergunta corrente.
            api_url = unique[0]
            qs = parse_qs(urlparse(api_url).query)
            actual_institute = (qs.get("instituto") or [institute or "G1"])[0]
            discovery = Discovery(
                api_url=api_url,
                page_url=page_url,
                institute=str(actual_institute),
                methodology=methodology,
                sample_size=sample,
                field_period=period,
                registrations=regs,
                methodology_margin=methodology_margin,
            )
            self._discoveries[key] = (now, discovery)
            return discovery
        finally:
            await context.close()

    async def fetch(
        self,
        office: str,
        scope: str = "br",
        round_: int = 1,
        institute: str | None = None,
        force: bool = False,
    ) -> G1Poll:
        office = office.lower().strip()
        scope = scope.lower().strip()
        inst = (institute or "").lower().strip() or None
        # validates parameters before acquiring a lock
        g1_url(office, scope, round_, inst)
        key = self._key(office, scope, round_, inst)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and not force and now - cached[0] < self.cache_seconds:
            return cached[1]

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and not force and now - cached[0] < self.cache_seconds:
                return cached[1]

            discovery = await self._discover(office, scope, round_, inst, force=False)
            try:
                response = await self.http.get(discovery.api_url, headers={"Cache-Control": "no-cache"})
                response.raise_for_status()
            except httpx.HTTPStatusError:
                discovery = await self._discover(office, scope, round_, inst, force=True)
                response = await self.http.get(discovery.api_url, headers={"Cache-Control": "no-cache"})
                response.raise_for_status()

            poll = _parse_payload(response.json(), discovery, office, scope, round_)
            self._cache[key] = (time.monotonic(), poll)
            return poll

    async def debug(self, url: str) -> dict[str, Any]:
        browser = await self._ensure_browser()
        context = await browser.new_context(locale="pt-BR", viewport={"width":1280,"height":900})
        page = await context.new_page()
        responses: list[str] = []
        page.on("response", lambda r: responses.append(r.url) if G1_API_MARKER in r.url else None)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=18000)
            except PlaywrightTimeoutError:
                pass
            body = await page.locator("body").inner_text()
            return {
                "status": response.status if response else None,
                "url": page.url,
                "title": await page.title(),
                "api_urls": list(dict.fromkeys(responses)),
                "lines": _clean_lines(body)[:500],
            }
        finally:
            await context.close()

    async def close(self) -> None:
        await self.http.aclose()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
