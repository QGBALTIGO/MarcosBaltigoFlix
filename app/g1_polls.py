from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, asdict
from typing import Any

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


@dataclass(slots=True)
class G1PollChoice:
    name: str
    party: str
    percentage: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class G1Poll:
    office: str
    scope: str
    round: int
    institute: str
    question: str
    margin_error: str
    sample_size: int | None
    field_period: str
    registrations: list[str]
    methodology: str
    choices: list[G1PollChoice]
    source_url: str
    fetched_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["choices"] = [c.to_dict() for c in self.choices]
        return data


def g1_url(office: str, scope: str = "br", round_: int = 1, institute: str | None = None) -> str:
    office = office.lower().strip()
    scope = scope.lower().strip()
    if office not in OFFICES:
        raise ValueError("Cargo inválido")
    if round_ not in {1, 2}:
        raise ValueError("Turno inválido")
    inst = (institute or "").lower().strip()
    if inst and inst not in INSTITUTES:
        raise ValueError("Instituto inválido")

    if office == "presidente" and scope == "br":
        base = "https://especiaisg1.globo/politica/eleicoes/2026/pesquisas-eleitorais"
    else:
        if scope not in STATE_SLUGS:
            raise ValueError("UF inválida para este cargo")
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
    m_margin = re.search(r"margem de erro(?: máxima para o total da amostra)? (?:é|de) ([^.]+?)(?:,|\.)", methodology, re.I)
    margin = m_margin.group(1).strip() if m_margin else ""
    m_sample = re.search(r"([\d.]+) entrevistas|ouviu ([\d.]+) pessoas|entrevistou ([\d.]+) pessoas|entrevistou ([\d.]+) eleitores", methodology, re.I)
    sample = None
    if m_sample:
        raw = next((g for g in m_sample.groups() if g), "")
        if raw:
            sample = int(raw.replace(".", ""))
    m_period = re.search(r"(?:entre|nos dias) (?:os dias )?([^.]*)", methodology, re.I)
    period = m_period.group(1).strip() if m_period else ""
    regs = sorted(set(re.findall(r"\b(?:BR|AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)-\d{5}/2026\b", methodology, re.I)))
    return methodology, margin, sample, period, regs


def _parse_total(lines: list[str]) -> tuple[str, list[G1PollChoice]]:
    # The rendered G1 page places the selected question before the first Total block.
    question = ""
    total_idx = -1
    for i, line in enumerate(lines):
        if line.lower() == "total":
            total_idx = i
            break
    if total_idx < 0:
        return question, []
    for i in range(total_idx - 1, max(-1, total_idx - 12), -1):
        if lines[i].lower() in {"estimulada","espontânea","votos válidos","estimulada - voto 1","estimulada - voto 2"} or "cenário" in lines[i].lower():
            question = lines[i]
            break

    end = len(lines)
    for i in range(total_idx + 1, len(lines)):
        if lines[i].lower() == "estratos":
            end = i
            break
    seg = lines[total_idx + 1:end]
    choices: list[G1PollChoice] = []
    pct_re = re.compile(r"^(\d{1,3}(?:[.,]\d+)?)\s*%$")
    party_re = re.compile(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9-]{2,20}$")
    noise = {
        "image: candidato","candidato","margem de erro","em %","intenção de voto, em %",
        "clique nos ícones abaixo para destacá-los nos gráficos:"
    }

    for i, line in enumerate(seg):
        m = pct_re.match(line)
        if not m:
            continue
        percentage = float(m.group(1).replace(",", "."))
        name = ""
        party = ""
        # Candidate label normally follows the percentage in rendered DOM.
        for j in range(i + 1, min(len(seg), i + 7)):
            cand = seg[j].strip()
            low = cand.lower()
            if not cand or low in noise or low.startswith("margem de erro"):
                continue
            if pct_re.match(cand):
                break
            if party_re.match(cand) and name:
                party = cand
                break
            if not name:
                name = cand.replace("Image: Candidato", "").strip()
        if name and not any(c.name == name and c.percentage == percentage for c in choices):
            choices.append(G1PollChoice(name=name, party=party, percentage=percentage))

    # Some chart implementations expose name first and percentage after it.
    if not choices:
        for i, line in enumerate(seg):
            if line.lower().startswith("margem de erro") or line.lower() in noise:
                continue
            if party_re.match(line) and i > 0:
                name = seg[i - 1]
                for j in range(i + 1, min(len(seg), i + 5)):
                    m = pct_re.match(seg[j])
                    if m:
                        choices.append(G1PollChoice(name=name, party=line, percentage=float(m.group(1).replace(",", "."))))
                        break
    return question or "Estimulada", choices


class G1PollClient:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._cache: dict[str, G1Poll] = {}

    async def _ensure_browser(self) -> Browser:
        if self._browser:
            return self._browser
        async with self._lock:
            if self._browser:
                return self._browser
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            return self._browser

    async def fetch(self, office: str, scope: str = "br", round_: int = 1, institute: str | None = None, force: bool = False) -> G1Poll:
        url = g1_url(office, scope, round_, institute)
        if not force and url in self._cache:
            return self._cache[url]
        browser = await self._ensure_browser()
        context = await browser.new_context(
            locale="pt-BR",
            timezone_id="America/Campo_Grande",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
            viewport={"width": 1440, "height": 1200},
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=18000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(3500)
            body = await page.locator("body").inner_text(timeout=15000)
            lines = _clean_lines(body)
            methodology, margin, sample, period, regs = _parse_methodology(lines)
            question, choices = _parse_total(lines)

            # If the visible chart uses SVG/ARIA, collect explicit percentage/name pairs.
            if not choices:
                aria = await page.locator('[aria-label*="%"]').evaluate_all(
                    """els => els.map(e => ({label:e.getAttribute('aria-label')||'', text:e.innerText||'', parent:(e.parentElement?.innerText||'').slice(0,300)}))"""
                )
                for item in aria:
                    s = " ".join([item.get("label",""), item.get("text",""), item.get("parent","")])
                    m = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", s)
                    if not m:
                        continue
                    pct = float(m.group(1).replace(",", "."))
                    clean = re.sub(r"\d{1,3}(?:[.,]\d+)?\s*%", "", s)
                    parts = [x.strip() for x in re.split(r"[\n|•]", clean) if x.strip()]
                    name = parts[0] if parts else ""
                    if name and not any(c.name == name for c in choices):
                        choices.append(G1PollChoice(name=name, party="", percentage=pct))

            institute_name = (institute or "").title()
            if not institute_name:
                m = re.search(r"\b(Datafolha|Quaest)\b", methodology, re.I)
                institute_name = m.group(1).title() if m else "G1"

            from datetime import datetime
            from zoneinfo import ZoneInfo
            poll = G1Poll(
                office=office.lower(),
                scope=scope.lower(),
                round=round_,
                institute=institute_name,
                question=question,
                margin_error=margin,
                sample_size=sample,
                field_period=period,
                registrations=regs,
                methodology=methodology,
                choices=choices,
                source_url=url,
                fetched_at=datetime.now(ZoneInfo("America/Campo_Grande")).isoformat(timespec="seconds"),
            )
            self._cache[url] = poll
            return poll
        finally:
            await context.close()

    async def debug(self, url: str) -> dict[str, Any]:
        browser = await self._ensure_browser()
        context = await browser.new_context(locale="pt-BR", viewport={"width":1440,"height":1200})
        page = await context.new_page()
        responses: list[str] = []
        page.on("response", lambda r: responses.append(r.url) if any(x in r.url.lower() for x in ("json","api","pesquis","elei")) else None)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=18000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(3500)
            body = await page.locator("body").inner_text()
            lines = _clean_lines(body)
            percent_contexts = await page.locator("body *").evaluate_all(
                """els => els.filter(e => /^\\s*\\d+(?:[.,]\\d+)?%\\s*$/.test((e.innerText||'').trim())).slice(0,80).map(e => ({tag:e.tagName, cls:e.className||'', text:(e.innerText||'').trim(), parent:(e.parentElement?.innerText||'').slice(0,250)}))"""
            )
            scripts = await page.locator("script[src]").evaluate_all("els => els.map(e=>e.src)")
            api_payloads = []
            for api_url in list(dict.fromkeys(responses)):
                if "/api/pesquisas-eleitorais/" not in api_url:
                    continue
                try:
                    api_resp = await context.request.get(api_url, timeout=20000)
                    payload = await api_resp.json()
                    api_payloads.append({"url": api_url, "status": api_resp.status, "payload": payload})
                except Exception as exc:
                    api_payloads.append({"url": api_url, "error": type(exc).__name__})
            return {
                "status": response.status if response else None,
                "url": page.url,
                "title": await page.title(),
                "lines": lines[:500],
                "percent_contexts": percent_contexts[:80],
                "scripts": scripts[:80],
                "interesting_responses": list(dict.fromkeys(responses))[:120],
                "api_payloads": api_payloads[:10],
            }
        finally:
            await context.close()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
