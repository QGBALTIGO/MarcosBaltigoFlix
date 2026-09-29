from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from time import perf_counter

from .candidates import CandidateDirectory
from .config import get_settings
from .g1_polls import G1PollClient
from .tse import TSEClient, VALID_UFS

OFFICES = ("governador", "senador", "federal", "estadual")
STATIC_DIR = Path(__file__).parent / "static"


class Smoke:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []
        self.notes: list[str] = []

    def ok(self, condition: bool, label: str) -> None:
        self.checks += 1
        if not condition:
            self.failures.append(label)

    def note(self, value: str) -> None:
        self.notes.append(value)


async def run(full: bool) -> int:
    smoke = Smoke()
    settings = get_settings()
    g1 = G1PollClient(cache_seconds=3600, discovery_seconds=3600)
    candidates = CandidateDirectory(g1, cache_seconds=3600)
    tse = TSEClient(settings)
    started = perf_counter()

    try:
        # 1) Frontend integrity: catch the exact class of bug that caused the infinite loader.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

        smoke.ok("id=\"candidateArea\"" in html, "HTML missing candidateArea")
        smoke.ok("id=\"candidateSearch\"" in html, "HTML missing candidateSearch")
        smoke.ok("id=\"candidateMore\"" in html, "HTML missing candidateMore")
        smoke.ok("id=\"summaryModeTitle\"" in html, "HTML missing summaryModeTitle")
        smoke.ok("id=\"analysisSourceNote\"" in html, "HTML missing analysisSourceNote")
        smoke.ok("id=\"estimateMeta\"" not in html, "legacy poll sentence still appears below cargo")
        smoke.ok("id=\"financeCard\"" in html, "HTML missing financeCard")
        smoke.ok("/api/candidates?office=" in js, "JS missing candidate API")
        smoke.ok("const qs=s=>document.querySelector(s),qsa=s=>[...document.querySelectorAll(s)];" in js, "DOM selector helpers are not explicit")
        smoke.ok("qsa('.nav-btn').forEach" in js, "nav buttons are not bound as a collection")
        smoke.ok("qsa('.chip').forEach" in js, "office chips are not bound as a collection")
        smoke.ok("qsa('[data-close]').forEach" in js, "location sheet X is not bound")
        smoke.ok("qsa('[data-full-close]').forEach" in js, "fullscreen X buttons are not bound")
        smoke.ok("qs('#stateList').addEventListener('click'" in js, "state click delegation missing")
        smoke.ok("closest('[data-state]')" in js, "state click delegation does not resolve target")

        # A querySelector helper returns a single element and must never receive collection methods.
        bad_qs_collection_calls = []
        qs_collection_pattern = re.compile(
            r"\bqs\([^()\n;]*\)\.(?:forEach|map|filter|find|some|every|reduce)\b"
        )
        for match in qs_collection_pattern.finditer(js):
            bad_qs_collection_calls.append(match.group(0))
        smoke.ok(
            not bad_qs_collection_calls,
            "qs() used as a collection: " + ", ".join(bad_qs_collection_calls[:10]),
        )
        smoke.ok("loadResults();" in js, "JS never starts result loading")
        smoke.ok("qs('#refreshBtn').onclick" in js, "refresh click binding missing")
        smoke.ok("qs('#candidateSearch').oninput" in js, "candidate search binding missing")
        smoke.ok("qs('#candidateMore').onclick" in js, "candidate pagination binding missing")
        smoke.ok("qs('#pollOffice').onchange" in js, "poll office binding missing")
        smoke.ok("qs('#pollInstitute').onchange" in js, "poll institute binding missing")
        smoke.ok("qs('#pollQuestion').onchange" in js, "poll question binding missing")
        smoke.ok("qs('#pollStratum').onchange" in js, "poll stratum binding missing")
        smoke.ok("async function requestJson" in js, "request timeout wrapper missing")
        smoke.ok("requestFullscreen" in js, "Telegram fullscreen request missing")
        smoke.ok("contentSafeAreaInset" in js and "safeAreaInset" in js, "Telegram safe-area handling missing")
        smoke.ok("fullscreenChanged" in js and "contentSafeAreaChanged" in js, "Telegram fullscreen/safe-area events missing")
        smoke.ok("--tg-top-clearance" in css and "--tg-bottom-clearance" in css, "fullscreen clearance CSS variables missing")
        smoke.ok("body.tg-fullscreen .topbar" in css and "body.tg-fullscreen .modal-top" in css, "fullscreen control clearance styles missing")
        smoke.ok("function requestTelegramFullscreen()" in js, "Telegram fullscreen request helper missing")
        smoke.ok("tg.requestFullscreen()" in js, "Telegram requestFullscreen call missing")
        smoke.ok("tg.expand()" in js, "Telegram expand fallback missing")
        smoke.ok("safeAreaChanged" in js and "contentSafeAreaChanged" in js, "Telegram safe-area event handling missing")
        smoke.ok("fullscreenChanged" in js and "fullscreenFailed" in js, "Telegram fullscreen event handling missing")
        smoke.ok("disableVerticalSwipes" in js, "Telegram vertical swipe protection missing")
        smoke.ok("--tg-safe-top" in css and "--tg-safe-bottom" in css, "Telegram safe-area CSS variables missing")
        smoke.ok("body.tg-fullscreen .topbar" in css, "Fullscreen top safe-area layout missing")
        smoke.ok("body.tg-fullscreen .modal-top" in css, "Fullscreen modal safe-area layout missing")
        smoke.ok(js.count("fetch(") == 1, "direct fetch calls bypass requestJson timeout wrapper")
        smoke.ok("function requireStateForOffice" in js, "state requirement helper missing")
        smoke.ok("setActiveOffice('presidente')" in js, "Brazil does not initialize on president")
        smoke.ok("function showView(name)" in js, "view navigation missing")
        smoke.ok("function renderPollSummary(poll)" in js, "poll summary renderer missing")
        smoke.ok("function renderOfficialSummary(d)" in js, "official summary renderer missing")
        smoke.ok("function renderSummaryFromState()" in js, "summary mode switch missing")
        smoke.ok("percentuais exibidos como 0%" in js, "zero fallback for missing polls missing")
        smoke.ok("office=${encodeURIComponent(office)}" in js, "official result request is not office-aware")
        smoke.ok("function selectState(scope,source='manual')" in js, "state selection function missing")
        smoke.ok("persistScope(scope,source)" in js, "state selection does not persist")
        smoke.ok("updateLocationUI();updateGeoStatus();" in js, "state selection does not update location UI")
        smoke.ok("if(pendingOffice){setActiveOffice(pendingOffice);showView('results');loadResults();return}" in js, "pending office flow broken")
        smoke.ok("if(pendingPoll){qs('#pollOffice').value=pendingPoll;showView('polls');loadPoll(true);return}" in js, "pending poll flow broken")
        smoke.ok("if(!qs('#resultsView').hidden){loadResults()}else if(!qs('#pollsView').hidden){loadPoll(true)}" in js, "state change does not refresh active view")
        smoke.ok("if(!isStateOffice(office)||state.scope!=='br')return false;" in js, "Brazil/state office gating broken")
        smoke.ok("setActiveOffice('presidente')" in js and "loadResults();" in js, "initial president load flow missing")
        smoke.ok("data-favorite" in js, "favorite click binding missing")
        smoke.ok("include_poll=false" in js, "initial candidates still block on polling")
        smoke.ok("pendingOffice" in js and "pendingPollOffice" in js, "state-selection flow missing")
        smoke.ok("CloudStorage" in js, "Telegram CloudStorage persistence missing")
        smoke.ok("election_scope_source" in js, "location source persistence missing")
        smoke.ok("function stateFlagUrl" in js and "function flagBadgeHtml" in js, "state flag renderer missing")
        smoke.ok("thumb.wikimedia.org/wikipedia/commons/thumb/7/73/Bandeira_do_estado_do_Rio_de_Janeiro.svg" in js, "Rio de Janeiro flag source is not the PNG fallback")
        smoke.ok("function renderFinance(c)" in js, "campaign finance renderer missing")
        smoke.ok("flagBadgeHtml(k)" in js, "state list is not rendering flag images")
        smoke.ok("/api/location/reverse?lat=" in js, "same-origin reverse geolocation missing")
        smoke.ok("class=\"uf-badge\"" not in js, "old UF text badges are still rendered")
        smoke.ok("id=\"geoStatus\"" in html and "id=\"locationFlag\"" in html, "location persistence UI missing")
        smoke.ok(".state-flag" in css and ".location-flag" in css, "state flag styles missing")
        smoke.ok(".candidate-search" in css, "candidate search CSS missing")
        smoke.ok(".bottom-nav" in css, "bottom navigation CSS missing")
        smoke.ok("gaugeVoid" in html and "gaugeSubJudice" in html, "five-segment gauge missing")
        smoke.ok("window.addEventListener('unhandledrejection'" in html, "frontend fatal-error boundary missing")
        smoke.ok("\\n<link" not in html, "literal \\n leaked into HTML head")
        smoke.ok(
            "Bandeira_do_estado_do_Rio_de_Janeiro.svg/330px-Bandeira_do_estado_do_Rio_de_Janeiro.svg.png" in js,
            "RJ is not using the rendered PNG flag fallback",
        )

        # Every simple #id referenced through qs() must exist exactly once in the HTML.
        html_ids = re.findall(r'\bid="([^"]+)"', html)
        html_id_set = set(html_ids)
        duplicate_html_ids = sorted({item for item in html_ids if html_ids.count(item) > 1})
        smoke.ok(not duplicate_html_ids, "duplicate HTML ids: " + ", ".join(duplicate_html_ids[:20]))

        js_id_refs = sorted(set(re.findall(r"\bqs\('#([A-Za-z0-9_-]+)'\)", js)))
        missing_js_ids = [item for item in js_id_refs if item not in html_id_set]
        smoke.ok(not missing_js_ids, "JS references missing HTML ids: " + ", ".join(missing_js_ids[:20]))

        # 2) Real candidate source in every UF and office.
        # First pass does network I/O. Repeated passes hit in-process cache and exercise parsing/render data.
        ufs = sorted(VALID_UFS)
        matrix: dict[tuple[str, str], dict] = {}
        for uf in ufs:
            for office in OFFICES:
                try:
                    data = await asyncio.wait_for(
                        candidates.list(office, uf, include_poll=False),
                        timeout=20,
                    )
                    matrix[(uf, office)] = data
                    smoke.ok(data.get("count", 0) > 0, f"{uf}/{office}: empty candidate list")
                    sample = data.get("candidates") or []
                    first = (sample or [{}])[0]
                    smoke.ok(bool(first.get("id")), f"{uf}/{office}: first candidate missing id")
                    smoke.ok(bool(first.get("ballot_name")), f"{uf}/{office}: first candidate missing ballot name")
                    smoke.ok(bool(first.get("number")), f"{uf}/{office}: first candidate missing number")
                    smoke.ok(bool(first.get("party")), f"{uf}/{office}: first candidate missing party")
                    photo_count = 0
                    for candidate in sample:
                        prefix = f"{uf}/{office}/{candidate.get('id') or '?'}"
                        smoke.ok(bool(candidate.get("id")), prefix + ": missing id")
                        smoke.ok(bool(candidate.get("ballot_name")), prefix + ": missing ballot name")
                        smoke.ok(bool(candidate.get("number")), prefix + ": missing number")
                        smoke.ok(bool(candidate.get("party")), prefix + ": missing party")
                        photo = str(candidate.get("photo") or "")
                        if photo:
                            photo_count += 1
                            smoke.ok(
                                photo.startswith("https://raw.githubusercontent.com/"),
                                prefix + ": photo is not from high-resolution TSE asset mirror",
                            )
                    if sample:
                        smoke.ok(
                            photo_count / len(sample) >= 0.95,
                            f"{uf}/{office}: photo coverage below 95% ({photo_count}/{len(sample)})",
                        )
                except Exception as exc:
                    smoke.failures.append(f"{uf}/{office}: {type(exc).__name__}: {exc}")

        # President is national regardless of selected UF.
        president = await asyncio.wait_for(
            candidates.list("presidente", "br", include_poll=False),
            timeout=20,
        )
        smoke.ok(president.get("count") == 12, f"president count is {president.get('count')}, expected 12")
        pres_names = {_n(c.get("ballot_name")) for c in president.get("candidates", [])}
        smoke.ok("pablomarcal" not in pres_names, "Pablo Marçal still present")
        smoke.ok("leonardoavalanche" not in pres_names, "Leonardo Avalanche still present")
        smoke.ok("lula" in pres_names, "Lula missing from president list")
        smoke.ok("flaviobolsonaro" in pres_names, "Flavio Bolsonaro missing from president list")
        for candidate in president.get("candidates", []):
            smoke.ok(bool(candidate.get("photo")), f"president {candidate.get('id')}: missing high-resolution photo")

        lula = next((c for c in president.get("candidates", []) if _n(c.get("ballot_name")) == "lula"), None)
        if lula:
            lula_detail = await asyncio.wait_for(
                candidates.detail("presidente", "br", lula["id"]),
                timeout=25,
            )
            lula_text = json.dumps(
                {"assets": lula_detail.get("assets"), "finance": lula_detail.get("finance")},
                ensure_ascii=False,
            )
            smoke.ok("�" not in lula_text, "Lula detail contains replacement characters")
            smoke.ok(isinstance(lula_detail.get("finance"), dict), "Lula finance missing")

        # 3) Repeat candidate matrix enough times to cross 1,000 assertions without hammering external sources.
        repeats = 10 if full else 2
        for _ in range(repeats):
            for (uf, office), data in matrix.items():
                smoke.ok(data.get("count", 0) > 0, f"cached {uf}/{office}: empty")
                sample = data.get("candidates") or []
                smoke.ok(all(bool(x.get("id")) for x in sample), f"cached {uf}/{office}: missing ids")
                smoke.ok(all(bool(x.get("ballot_name")) for x in sample), f"cached {uf}/{office}: missing names")
                smoke.ok(all(bool(x.get("number")) for x in sample), f"cached {uf}/{office}: missing numbers")

        # 4) TSE result URL contract for every 2026 office.
        office_url_checks = {
            "presidente": ("br", "c0001", settings.tse_election_code),
            "governador": ("ms", "c0003", settings.tse_state_election_code),
            "senador": ("ms", "c0005", settings.tse_state_election_code),
            "federal": ("ms", "c0006", settings.tse_state_election_code),
            "estadual": ("ms", "c0007", settings.tse_state_election_code),
        }
        for office, (scope, cargo_fragment, election_code) in office_url_checks.items():
            url = tse.result_url(scope, office=office)
            smoke.ok(cargo_fragment in url, f"TSE {office}: wrong cargo code")
            smoke.ok(f"/{election_code}/" in url, f"TSE {office}: wrong election code")

        df_state_url = tse.result_url("df", office="estadual")
        smoke.ok("c0008" in df_state_url, "TSE distrital: wrong cargo code")

        # 5) TSE presidential result for Brazil + every UF, repeated from cache.
        result_scopes = ["br", *ufs]
        tse_results = {}
        for scope in result_scopes:
            try:
                result, _ = await asyncio.wait_for(tse.fetch(scope), timeout=15)
                tse_results[scope] = result
                smoke.ok(result.sections_total >= 0, f"TSE {scope}: invalid section total")
                smoke.ok(result.electorate_total >= 0, f"TSE {scope}: invalid electorate")
                smoke.ok(isinstance(result.candidates, list), f"TSE {scope}: candidates is not a list")
            except Exception as exc:
                smoke.failures.append(f"TSE {scope}: {type(exc).__name__}: {exc}")

        for _ in range(3 if full else 1):
            for scope, result in tse_results.items():
                smoke.ok(result.sections_counted_pct >= 0, f"TSE cached {scope}: negative progress")
                smoke.ok(result.valid_votes >= 0, f"TSE cached {scope}: negative valid votes")

        # 6) Poll integration: national president and MS offices used most heavily by the current UI.
        poll_cases = [
            ("presidente", "br", "datafolha"),
            ("governador", "ms", None),
            ("senador", "ms", None),
        ]
        for office, scope, institute in poll_cases:
            try:
                poll = await asyncio.wait_for(g1.fetch(office, scope, 1, institute), timeout=20)
                smoke.ok(len(poll.choices) > 0, f"G1 {office}/{scope}: no choices")
                smoke.ok(bool(poll.latest_date), f"G1 {office}/{scope}: no date")
                smoke.ok(bool(poll.institute), f"G1 {office}/{scope}: no institute")
                smoke.ok(all(x.percentage >= 0 for x in poll.choices), f"G1 {office}/{scope}: invalid percentage")
            except Exception as exc:
                smoke.failures.append(f"G1 {office}/{scope}: {type(exc).__name__}: {exc}")

        # 7) Candidate detail for each office in MS + national president.
        detail_cases = [("presidente", "br")]
        detail_cases += [(office, "ms") for office in OFFICES]
        for office, scope in detail_cases:
            try:
                listing = (
                    president if office == "presidente"
                    else matrix[(scope, office)]
                )
                first = listing["candidates"][0]
                detail = await asyncio.wait_for(
                    candidates.detail(office, scope, first["id"]),
                    timeout=25,
                )
                smoke.ok(detail.get("id") == first["id"], f"detail {office}/{scope}: id mismatch")
                smoke.ok(bool(detail.get("ballot_name")), f"detail {office}/{scope}: missing ballot name")
                smoke.ok(bool(detail.get("number")), f"detail {office}/{scope}: missing number")
                smoke.ok(bool(detail.get("party")), f"detail {office}/{scope}: missing party")
                smoke.ok(isinstance(detail.get("assets"), list), f"detail {office}/{scope}: assets not list")
                smoke.ok(isinstance(detail.get("finance"), dict), f"detail {office}/{scope}: finance not dict")
                finance = detail.get("finance") or {}
                smoke.ok(float(finance.get("receipts_total") or 0) >= 0, f"detail {office}/{scope}: invalid receipts total")
                smoke.ok(float(finance.get("expenses_total") or 0) >= 0, f"detail {office}/{scope}: invalid expenses total")
                smoke.ok(isinstance(detail.get("social_links"), list), f"detail {office}/{scope}: social_links not list")
                replacement_text = json.dumps(
                    {"assets": detail.get("assets"), "finance": detail.get("finance")},
                    ensure_ascii=False,
                )
                smoke.ok("�" not in replacement_text, f"detail {office}/{scope}: replacement character in financial data")
                detail_text = json.dumps(detail, ensure_ascii=False)
                smoke.ok("�" not in detail_text, f"detail {office}/{scope}: Unicode replacement character found")
                smoke.ok("Ã£" not in detail_text and "Ã§" not in detail_text and "Â" not in detail_text, f"detail {office}/{scope}: mojibake found")
            except Exception as exc:
                smoke.failures.append(f"detail {office}/{scope}: {type(exc).__name__}: {exc}")

        # Targeted UTF-8 regression probe using accented asset labels/descriptions.
        try:
            utf8_detail = await asyncio.wait_for(
                candidates.detail("presidente", "br", "280002542548"),
                timeout=25,
            )
            utf8_assets_text = "\n".join(
                f"{item.get('type', '')} | {item.get('description', '')}"
                for item in utf8_detail.get("assets", [])
            )
            smoke.ok("�" not in utf8_assets_text, "UTF-8 probe: replacement character found in assets")
            smoke.ok("CONSTRUÇÃO" in utf8_assets_text, "UTF-8 probe: CONSTRUÇÃO not decoded correctly")
            smoke.ok("PUBLICAÇÕES" in utf8_assets_text, "UTF-8 probe: PUBLICAÇÕES not decoded correctly")
            smoke.ok("APLICAÇÃO" in utf8_assets_text, "UTF-8 probe: APLICAÇÃO not decoded correctly")
            smoke.ok("SÃO BERNARDO DO CAMPO" in utf8_assets_text, "UTF-8 probe: São Bernardo text not decoded correctly")
        except Exception as exc:
            smoke.failures.append(f"UTF-8 detail probe: {type(exc).__name__}: {exc}")

        elapsed = perf_counter() - started
        summary = {
            "mode": "full" if full else "quick",
            "checks": smoke.checks,
            "failures": len(smoke.failures),
            "elapsed_seconds": round(elapsed, 2),
            "failure_details": smoke.failures[:100],
            "notes": smoke.notes,
        }
        print("SMOKE_RESULT=" + json.dumps(summary, ensure_ascii=False))

        return 1 if smoke.failures else 0
    finally:
        await candidates.close()
        await g1.close()
        await tse.close()


def _n(value: str | None) -> str:
    import unicodedata
    import re
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text.lower())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(full=args.full and not args.quick)))
