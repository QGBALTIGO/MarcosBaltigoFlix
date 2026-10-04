from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx

from .bot import ElectionBot
from .config import Settings
from .storage import Storage

log = logging.getLogger(__name__)

BRASILIA = ZoneInfo("America/Sao_Paulo")
ELECTION_DATES_2026 = {date(2026, 10, 4), date(2026, 10, 25)}
EXAME_URL = (
    "https://exame.com/brasil/"
    "votacao-para-presidente-no-exterior-veja-quem-venceu-no-japao-china-e-nova-zelandia/"
)

COUNTRY_ALIASES = {
    "Cingapura": "Singapura",
    "Taipei": "Taiwan (Taipé)",
    "Taiwan, em Taipé": "Taiwan (Taipé)",
    "China, incluindo Hong Kong": "China (incluindo Hong Kong)",
}

NUMBER_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "quatorze": 14, "catorze": 14, "quinze": 15, "dezesseis": 16,
    "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20,
}


@dataclass(frozen=True, slots=True)
class ExteriorCandidate:
    name: str
    party: str
    votes: int
    percentage: float | None = None


@dataclass(frozen=True, slots=True)
class ExteriorCountryResult:
    country: str
    candidates: tuple[ExteriorCandidate, ...]
    source_label: str
    source_url: str

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {
                "country": self.country,
                "candidates": [
                    (c.name, c.party, c.votes, c.percentage)
                    for c in self.candidates
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_h2 = ""
        self._h2_parts: list[str] | None = None
        self._li_parts: list[str] | None = None
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "h2":
            self._h2_parts = []
        elif tag == "li":
            self._li_parts = []

    def handle_data(self, data: str) -> None:
        if self._h2_parts is not None:
            self._h2_parts.append(data)
        if self._li_parts is not None:
            self._li_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2" and self._h2_parts is not None:
            value = " ".join("".join(self._h2_parts).split())
            if value:
                self.current_h2 = value
            self._h2_parts = None
        elif tag == "li" and self._li_parts is not None:
            value = " ".join("".join(self._li_parts).split())
            if value:
                self.items.append((self.current_h2, value))
            self._li_parts = None


_PODER_ROW = re.compile(
    r"^(?P<name>.+?)"
    r"(?:\s+\((?P<party>[^)]+)\))?"
    r"\s*[–—-]\s*"
    r"(?P<votes>[\d.]+)"
    r"(?:\s+votos?)?"
    r"\s*\((?P<pct>[\d,]+)%\)",
    re.IGNORECASE,
)


def _integer(value: str) -> int | None:
    normalized = value.strip().lower().rstrip(".,;")
    if normalized in NUMBER_WORDS:
        return NUMBER_WORDS[normalized]
    digits = re.sub(r"\D", "", normalized)
    return int(digits) if digits else None


def _clean_country(value: str) -> str:
    value = " ".join(html_lib.unescape(value).split()).strip(" :")
    return COUNTRY_ALIASES.get(value, value)


def parse_poder360_exterior(html_text: str, source_url: str) -> list[ExteriorCountryResult]:
    parser = _ArticleParser()
    parser.feed(html_text)
    grouped: dict[str, list[ExteriorCandidate]] = {}
    for heading, item in parser.items:
        match = _PODER_ROW.match(item)
        if not match or not heading:
            continue
        country = _clean_country(heading)
        if len(country) > 60 or country.lower() in {
            "mais lidas", "editorias", "eleições", "utilitários", "produtos",
        }:
            continue
        votes = _integer(match.group("votes"))
        if votes is None:
            continue
        pct = float(match.group("pct").replace(",", "."))
        grouped.setdefault(country, []).append(
            ExteriorCandidate(
                name=" ".join(match.group("name").split()),
                party=(match.group("party") or "").strip(),
                votes=votes,
                percentage=pct,
            )
        )

    results = []
    for country, candidates in grouped.items():
        if len(candidates) < 2:
            continue
        ordered = tuple(sorted(candidates, key=lambda c: (-c.votes, c.name.casefold())))
        results.append(
            ExteriorCountryResult(
                country=country,
                candidates=ordered,
                source_label="Poder360, com base em boletins de urna do TSE",
                source_url=source_url,
            )
        )
    return results


def _extract_exame_candidates(text: str) -> list[ExteriorCandidate]:
    found: dict[str, ExteriorCandidate] = {}

    verb_pattern = re.compile(
        r"(?P<name>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ ]{1,35}?)\s+"
        r"(?:recebeu|somou|teve)\s+"
        r"(?P<votes>[\d.]+|um|uma|dois|duas|três|tres|quatro|cinco|seis|sete|oito|nove|dez)"
        r"(?:\s+votos?)?",
        re.IGNORECASE,
    )
    contra_pattern = re.compile(
        r"contra\s+(?P<votes>[\d.]+|um|uma|dois|duas|três|tres|quatro|cinco|seis|sete|oito|nove|dez)"
        r"(?:\s+votos?)?\s+de\s+(?P<name>[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-Za-zÀ-ÿ ]{1,35})",
        re.IGNORECASE,
    )

    for pattern in (verb_pattern, contra_pattern):
        for match in pattern.finditer(text):
            name = " ".join(match.group("name").strip(" ,.;").split())
            # Trim narrative prefixes that can precede a candidate name.
            name = re.sub(r"^(?:O candidato do \w+|Enquanto)\s+", "", name, flags=re.I)
            votes = _integer(match.group("votes"))
            if not name or votes is None:
                continue
            key = name.casefold()
            found[key] = ExteriorCandidate(name=name, party="", votes=votes)

    return sorted(found.values(), key=lambda c: (-c.votes, c.name.casefold()))


def parse_exame_exterior(html_text: str, source_url: str) -> list[ExteriorCountryResult]:
    parser = _ArticleParser()
    parser.feed(html_text)
    results: list[ExteriorCountryResult] = []

    for _heading, item in parser.items:
        if ":" not in item:
            continue
        country_raw, detail = item.split(":", 1)
        country = _clean_country(country_raw)
        if not (2 <= len(country) <= 60):
            continue
        candidates = _extract_exame_candidates(detail)
        if len(candidates) < 2:
            continue
        results.append(
            ExteriorCountryResult(
                country=country,
                candidates=tuple(candidates),
                source_label="Exame, com base em boletins de urna disponíveis no TSE",
                source_url=source_url,
            )
        )
    return results


def _merge_results(
    primary: list[ExteriorCountryResult],
    secondary: list[ExteriorCountryResult],
) -> list[ExteriorCountryResult]:
    merged: dict[str, ExteriorCountryResult] = {}
    for item in secondary:
        merged[item.country.casefold()] = item
    for item in primary:
        merged[item.country.casefold()] = item
    return sorted(merged.values(), key=lambda item: item.country.casefold())


def format_exterior_channel_message(item: ExteriorCountryResult) -> str:
    rows = []
    for candidate in item.candidates:
        pct = ""
        if candidate.percentage is not None:
            pct = f" · {candidate.percentage:.2f}%".replace(".", ",")
        party = f" ({candidate.party})" if candidate.party else ""
        votes_text = f"{candidate.votes:,}".replace(",", ".")
        rows.append(
            f"• <b>{html_lib.escape(candidate.name)}</b>{html_lib.escape(party)} — "
            f"{votes_text} votos{pct}"
        )

    return (
        f"🌍 <b>Exterior • {html_lib.escape(item.country)}</b>\n\n"
        "<i>Boletins de urna já divulgados. Este recorte ainda não é a "
        "totalização oficial do resultado presidencial pelo TSE.</i>\n\n"
        + "\n".join(rows)
        + "\n\n"
        f"<b>Fonte do levantamento:</b> {html_lib.escape(item.source_label)}\n"
        f"{html_lib.escape(item.source_url)}"
    )


class ExteriorBulletinClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers={
                "User-Agent": "ResultadoEleicoesBot/1.0 (monitor informativo do exterior)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

    async def fetch(self) -> list[ExteriorCountryResult]:
        urls = [self.settings.exterior_bu_source_url, EXAME_URL]

        async def get(url: str) -> tuple[str, str]:
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                return url, response.text
            except Exception as exc:
                log.warning("Falha consultando fonte do exterior %s: %s", url, exc)
                return url, ""

        (poder_url, poder_html), (exame_url, exame_html) = await asyncio.gather(
            *(get(url) for url in urls)
        )
        primary = parse_poder360_exterior(poder_html, poder_url) if poder_html else []
        secondary = parse_exame_exterior(exame_html, exame_url) if exame_html else []
        return _merge_results(primary, secondary)

    async def close(self) -> None:
        await self.client.aclose()


async def exterior_bulletin_loop(
    settings: Settings,
    storage: Storage,
    bot: ElectionBot,
    client: ExteriorBulletinClient,
    stop_event: asyncio.Event,
) -> None:
    if not settings.exterior_bu_enabled:
        return

    while not stop_event.is_set():
        try:
            now = datetime.now(BRASILIA)
            if now.date() in ELECTION_DATES_2026:
                items = await client.fetch()
                if await bot.check_channel_ready():
                    for item in items[:20]:
                        key = "exterior:bu:" + hashlib.sha1(
                            item.country.casefold().encode("utf-8")
                        ).hexdigest()
                        previous = await storage.get_state(key)
                        if previous == item.fingerprint:
                            continue
                        if await bot.publish_exterior_update(
                            format_exterior_channel_message(item)
                        ):
                            await storage.set_state(key, item.fingerprint)
                            await asyncio.sleep(0.35)
        except Exception:
            log.exception("Falha no monitor de boletins do exterior")

        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.exterior_bu_poll_seconds,
            )
        except asyncio.TimeoutError:
            pass
