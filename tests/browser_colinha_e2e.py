from __future__ import annotations

import json
import mimetypes
import os
import shutil
import struct
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)

DATA_PHOTO = (
    "data:image/svg+xml;charset=utf-8,"
    + urllib.parse.quote(
        """<svg xmlns="http://www.w3.org/2000/svg" width="240" height="300">
        <rect width="240" height="300" fill="#e9ecef"/>
        <circle cx="120" cy="92" r="48" fill="#9aa4b2"/>
        <rect x="54" y="154" width="132" height="110" rx="42" fill="#9aa4b2"/>
        </svg>"""
    )
)

CANDIDATES = {
    "federal": [
        {
            "id": "fed-1313",
            "number": "1313",
            "name": "ANA FEDERAL",
            "ballot_name": "ANA FEDERAL",
            "party": "P13",
            "party_name": "PARTIDO 13",
            "photo": DATA_PHOTO,
            "occupation": "Candidatura de teste",
        },
        {
            "id": "fed-2211",
            "number": "2211",
            "name": "BIA FEDERAL",
            "ballot_name": "BIA FEDERAL",
            "party": "P22",
            "party_name": "PARTIDO 22",
            "photo": "",
            "occupation": "Candidatura de teste",
        },
    ],
    "estadual": [
        {
            "id": "est-13131",
            "number": "13131",
            "name": "BRUNO ESTADUAL",
            "ballot_name": "BRUNO ESTADUAL",
            "party": "P13",
            "party_name": "PARTIDO 13",
            "photo": DATA_PHOTO,
            "occupation": "Candidatura de teste",
        },
        {
            "id": "est-22122",
            "number": "22122",
            "name": "CAIO ESTADUAL",
            "ballot_name": "CAIO ESTADUAL",
            "party": "P22",
            "party_name": "PARTIDO 22",
            "photo": "",
            "occupation": "Candidatura de teste",
        },
    ],
    "senador": [
        {
            "id": "sen-133",
            "number": "133",
            "name": "CARLA SENADO",
            "ballot_name": "CARLA SENADO",
            "party": "P13",
            "party_name": "PARTIDO 13",
            "photo": DATA_PHOTO,
            "occupation": "Candidatura de teste",
        },
        {
            "id": "sen-400",
            "number": "400",
            "name": "DAVI SENADO",
            "ballot_name": "DAVI SENADO",
            "party": "P40",
            "party_name": "PARTIDO 40",
            "photo": "",
            "occupation": "Candidatura de teste",
        },
    ],
    "governador": [
        {
            "id": "gov-13",
            "number": "13",
            "name": "EVA GOVERNO",
            "ballot_name": "EVA GOVERNO",
            "party": "P13",
            "party_name": "PARTIDO 13",
            "photo": DATA_PHOTO,
            "occupation": "Candidatura de teste",
        },
        {
            "id": "gov-22",
            "number": "22",
            "name": "FELIPE GOVERNO",
            "ballot_name": "FELIPE GOVERNO",
            "party": "P22",
            "party_name": "PARTIDO 22",
            "photo": "",
            "occupation": "Candidatura de teste",
        },
    ],
    "presidente": [
        {
            "id": "pre-13",
            "number": "13",
            "name": "GABI PRESIDÊNCIA",
            "ballot_name": "GABI PRESIDÊNCIA",
            "party": "P13",
            "party_name": "PARTIDO 13",
            "photo": DATA_PHOTO,
            "occupation": "Candidatura de teste",
        },
        {
            "id": "pre-22",
            "number": "22",
            "name": "HEITOR PRESIDÊNCIA",
            "ballot_name": "HEITOR PRESIDÊNCIA",
            "party": "P22",
            "party_name": "PARTIDO 22",
            "photo": "",
            "occupation": "Candidatura de teste",
        },
    ],
}


class StaticHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            target = STATIC / "index.html"
        elif path.startswith("/static/"):
            target = STATIC / path.removeprefix("/static/")
        else:
            self.send_error(404)
            return

        if not target.is_file():
            self.send_error(404)
            return

        content = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class TestServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StaticHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self) -> "TestServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def candidate_payload(office: str, scope: str) -> dict:
    rows = [dict(item, uf=scope.upper(), office=office) for item in CANDIDATES[office]]
    return {
        "office": office,
        "scope": "br" if office == "presidente" else scope,
        "count": len(rows),
        "candidates": rows,
        "estimate": {"available": False, "source": "mock"},
        "candidate_source": {"label": "fixture"},
    }


def install_routes(context: BrowserContext, fail_office: str | None = None) -> None:
    def api_route(route, request) -> None:
        parsed = urllib.parse.urlparse(request.url)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/candidates":
            office = query.get("office", ["presidente"])[0]
            scope = query.get("scope", ["br"])[0]
            if office == fail_office:
                route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps({"detail": "fixture source unavailable"}),
                )
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(candidate_payload(office, scope)),
            )
            return

        if path == "/api/g1/catalog":
            route.fulfill(status=200, content_type="application/json", body='{"cargos":[]}')
            return

        if path == "/api/location/reverse":
            route.fulfill(status=200, content_type="application/json", body='{"uf":"MS"}')
            return

        if path == "/api/result":
            office = query.get("office", ["presidente"])[0]
            scope = query.get("scope", ["br"])[0]
            rows = candidate_payload(office, scope)["candidates"]
            payload = {
                "scope": scope,
                "election_code": 6257 if office == "presidente" else 6259,
                "round": 1,
                "phase": "pre_election",
                "generated_date": "",
                "generated_time": "",
                "totalization_date": "",
                "totalization_time": "",
                "generation_id": f"pre-election:{office}:{scope}",
                "disclosure_enabled": False,
                "final_totalization": False,
                "progress_status": "Aguardando início da apuração oficial",
                "mathematically_defined": "",
                "no_elected_assignment": False,
                "no_elected_reasons": [],
                "sections_total": 0,
                "sections_counted": 0,
                "sections_pending": 0,
                "sections_counted_pct": 0.0,
                "electorate_total": 0,
                "turnout": 0,
                "turnout_pct": 0.0,
                "abstention": 0,
                "abstention_pct": 0.0,
                "total_votes": 0,
                "valid_votes": 0,
                "blank_votes": 0,
                "null_votes": 0,
                "void_votes": 0,
                "void_sub_judice_votes": 0,
                "pre_election": True,
                "simulation": False,
                "source_label": "Tribunal Superior Eleitoral (TSE)",
                "source_url": "https://resultados.tse.jus.br/",
                "candidates": [
                    {
                        "number": int(item["number"]),
                        "sequence": index,
                        "candidate_id": item["id"],
                        "name": item["name"],
                        "ballot_name": item["ballot_name"],
                        "party": item["party"],
                        "party_name": item["party_name"],
                        "votes": 0,
                        "percentage": 0.0,
                        "percentage_exact": 0.0,
                        "vote_destination": "",
                        "official_status": "",
                        "elected_flag": False,
                        "vice_name": "",
                        "vice_party": "",
                    }
                    for index, item in enumerate(rows, start=1)
                ],
            }
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            )
            return

        if path == "/api/g1/poll":
            route.fulfill(
                status=502,
                content_type="application/json",
                body='{"detail":"fixture intentionally unavailable"}',
            )
            return

        if path == "/api/health":
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return

        route.fulfill(status=404, content_type="application/json", body='{"detail":"fixture 404"}')

    context.route("**/api/**", api_route)
    context.route(
        "https://telegram.org/js/telegram-web-app.js",
        lambda route: route.fulfill(status=200, content_type="application/javascript", body=""),
    )
    for pattern in (
        "https://raw.githubusercontent.com/**",
        "https://flagcdn.com/**",
        "https://thumb.wikimedia.org/**",
        "https://especiaisg1.globo/**",
    ):
        context.route(pattern, lambda route: route.abort())


def new_context(
    browser: Browser,
    scope: str = "ms",
    viewport: dict[str, int] | None = None,
    *,
    fail_office: str | None = None,
    telegram_mock: bool = False,
    corrupt_storage: bool = False,
) -> BrowserContext:
    context = browser.new_context(
        viewport=viewport or {"width": 390, "height": 844},
        accept_downloads=True,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
        ),
    )
    install_routes(context, fail_office=fail_office)

    storage_script = f"""
        localStorage.setItem('election_scope', {json.dumps(scope)});
        localStorage.setItem('election_scope_source', 'manual');
        {"localStorage.setItem('resultado_eleicoes_2026:colinha:"+scope+"', '{broken json');" if corrupt_storage else ""}
    """
    context.add_init_script(storage_script)

    if telegram_mock:
        context.add_init_script(
            """
            (() => {
              const listeners = {};
              const webApp = {
                platform: 'ios',
                isFullscreen: false,
                viewportStableHeight: 844,
                safeAreaInset: {top:47,right:0,bottom:34,left:0},
                contentSafeAreaInset: {top:0,right:0,bottom:0,left:0},
                ready(){},
                expand(){},
                setHeaderColor(){},
                setBackgroundColor(){},
                setBottomBarColor(){},
                requestFullscreen(){
                  this.isFullscreen = true;
                  setTimeout(() => (listeners.fullscreenChanged || []).forEach(fn => fn()), 0);
                },
                onEvent(name, fn){ (listeners[name] ||= []).push(fn); },
                CloudStorage: {
                  getItem(key, cb){ cb(null, ''); },
                  setItem(key, value, cb){ if(cb) cb(null, true); }
                }
              };
              window.Telegram = {WebApp:webApp};
            })();
            """
        )
    return context


def page_with_errors(context: BrowserContext, base_url: str) -> tuple[Page, list[str]]:
    page = context.new_page()
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(base_url + "/", wait_until="domcontentloaded")
    expect(page.locator('[data-view="colinha"]')).to_be_visible()
    return page, page_errors


def open_colinha(page: Page) -> None:
    page.locator('[data-view="colinha"]').click()
    expect(page.locator("#colinhaView")).to_be_visible()
    expect(page.locator('[data-colinha-slot="federal"]')).to_be_visible(timeout=10_000)
    expect(page.locator(".colinha-card")).to_have_count(6)


def fill_vote(page: Page, key: str, number: str) -> None:
    page.locator(f'[data-colinha-input="{key}"]').fill(number)


def click_urna_digits(page: Page, number: str) -> None:
    for digit in number:
        page.locator(f'[data-urna-digit="{digit}"]').click()


def assert_no_horizontal_overflow(page: Page) -> None:
    data = page.evaluate(
        """
        () => {
          const width = window.innerWidth;
          const cards = [...document.querySelectorAll('.colinha-card,.train-card,.colinha-sticky-actions')]
            .filter(el => !el.hidden && getComputedStyle(el).display !== 'none')
            .map(el => {
              const r = el.getBoundingClientRect();
              return {left:r.left,right:r.right,width:r.width};
            });
          return {
            width,
            scrollWidth: document.documentElement.scrollWidth,
            cards
          };
        }
        """
    )
    assert data["scrollWidth"] <= data["width"] + 1, data
    for rect in data["cards"]:
        assert rect["left"] >= -1, rect
        assert rect["right"] <= data["width"] + 1, rect


def test_full_flow(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)

        assert page.locator('[data-view="polls"]').count() == 0
        assert page.locator('.nav-btn:has-text("Pesquisas")').count() == 0

        fill_vote(page, "federal", "1313")
        expect(page.locator('[data-colinha-match="federal"]')).to_contain_text("ANA FEDERAL")

        fill_vote(page, "estadual", "13131")
        expect(page.locator('[data-colinha-match="estadual"]')).to_contain_text("BRUNO ESTADUAL")

        fill_vote(page, "senador1", "133")
        expect(page.locator('[data-colinha-match="senador1"]')).to_contain_text("CARLA SENADO")

        fill_vote(page, "senador2", "133")
        expect(page.locator('[data-colinha-warning="senador2"]')).to_be_visible()
        expect(page.locator('[data-colinha-warning="senador2"]')).to_contain_text("mesmo número")

        page.locator('[data-colinha-clear="senador2"]').click()
        fill_vote(page, "senador2", "400")
        expect(page.locator('[data-colinha-match="senador2"]')).to_contain_text("DAVI SENADO")
        expect(page.locator('[data-colinha-warning="senador2"]')).to_be_hidden()

        fill_vote(page, "governador", "13")
        expect(page.locator('[data-colinha-match="governador"]')).to_contain_text("EVA GOVERNO")
        fill_vote(page, "presidente", "13")
        expect(page.locator('[data-colinha-match="presidente"]')).to_contain_text("GABI PRESIDÊNCIA")

        # Search-by-name path is inline inside the same office card.
        page.locator('[data-colinha-clear="estadual"]').click()
        page.locator('[data-colinha-search="estadual"]').click()
        expect(page.locator('[data-colinha-inline-search="estadual"]')).to_be_visible()
        page.locator('[data-colinha-name-input="estadual"]').fill("bruno")
        expect(page.locator('[data-colinha-inline-results="estadual"] .colinha-inline-item')).to_have_count(1)
        page.locator('[data-colinha-inline-results="estadual"] .colinha-inline-item').click()
        expect(page.locator('[data-colinha-input="estadual"]')).to_have_value("13131")
        expect(page.locator('[data-colinha-inline-search="estadual"]')).to_be_hidden()

        # Persistence survives a reload and re-entry into the tab.
        page.reload(wait_until="domcontentloaded")
        page.locator('[data-view="colinha"]').click()
        expect(page.locator('[data-colinha-input="federal"]')).to_have_value("1313")
        expect(page.locator('[data-colinha-input="estadual"]')).to_have_value("13131")
        expect(page.locator('[data-colinha-input="senador2"]')).to_have_value("400")

        # Image export creates a real downloadable PNG in desktop Chromium.
        with page.expect_download(timeout=15_000) as download_info:
            page.locator("#colinhaSaveImage").click()
        download = download_info.value
        assert download.suggested_filename == "minha-colinha-MS.png"
        path = Path(download.path())
        assert path.stat().st_size > 1000, path.stat().st_size
        png = path.read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", png[16:24]) == (1080, 1350)
        shutil.copy2(path, ARTIFACTS / "generated-colinha-MS.png")

        # Share falls back to the same generated image when Web Share is unavailable.
        with page.expect_download(timeout=15_000) as share_info:
            page.locator("#colinhaShare").click()
        share_download = share_info.value
        assert share_download.suggested_filename == "minha-colinha-MS.png"

        # Urna flow: one deliberate divergence, then finish the six steps.
        page.locator("#colinhaTrain").click()
        assert "show" in (page.locator("#urnaModal").get_attribute("class") or "")

        click_urna_digits(page, "1313")
        expect(page.locator("#urnaCandidate")).to_contain_text("ANA FEDERAL")
        expect(page.locator("#urnaCompare")).to_contain_text("É o número")
        page.locator("#urnaConfirm").click()

        click_urna_digits(page, "13132")
        expect(page.locator("#urnaCompare")).to_contain_text("Não é o número")
        expect(page.locator("#urnaCandidate")).to_contain_text("LEGENDA")
        page.locator("#urnaConfirm").click()

        click_urna_digits(page, "133")
        expect(page.locator("#urnaCandidate")).to_contain_text("CARLA SENADO")
        page.locator("#urnaConfirm").click()

        click_urna_digits(page, "400")
        expect(page.locator("#urnaCandidate")).to_contain_text("DAVI SENADO")
        page.locator("#urnaConfirm").click()

        click_urna_digits(page, "13")
        expect(page.locator("#urnaCandidate")).to_contain_text("EVA GOVERNO")
        page.locator("#urnaConfirm").click()

        click_urna_digits(page, "13")
        expect(page.locator("#urnaCandidate")).to_contain_text("GABI PRESIDÊNCIA")
        page.locator("#urnaConfirm").click()

        expect(page.locator("#urnaFinishView")).to_be_visible()
        expect(page.locator(".urna-finish-score")).to_contain_text("1 divergência")
        expect(page.locator(".urna-summary-row")).to_have_count(6)

        page.screenshot(path=str(ARTIFACTS / "urna-fim-390x844.png"))

        # Restart exercises BRANCO, CORRIGE and the disabled confirm guard.
        page.locator("#urnaRestart").click()
        assert page.locator("#urnaConfirm").is_disabled()
        page.locator("#urnaBlank").click()
        assert page.locator("#urnaConfirm").is_enabled()
        expect(page.locator("#urnaCandidate")).to_contain_text("VOTO EM BRANCO")
        page.locator("#urnaCorrect").click()
        assert page.locator("#urnaConfirm").is_disabled()
        click_urna_digits(page, "13")
        page.locator("#urnaCorrect").click()
        expect(page.locator(".urna-number-box.filled")).to_have_count(0)

        page.locator("#urnaClose").click()
        assert "show" not in (page.locator("#urnaModal").get_attribute("class") or "")

        page.wait_for_timeout(350)
        page.screenshot(path=str(ARTIFACTS / "colinha-390x844.png"))
        assert not errors, errors
    finally:
        context.close()


def test_legend_and_invalid_votes(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)

        fill_vote(page, "federal", "13")
        expect(page.locator('[data-colinha-match="federal"]')).to_contain_text("Voto de legenda")
        assert "is-match" in (page.locator('[data-colinha-slot="federal"]').get_attribute("class") or "")

        fill_vote(page, "governador", "99")
        expect(page.locator('[data-colinha-match="governador"]')).to_contain_text("Número não identificado")
        assert "is-invalid" in (page.locator('[data-colinha-slot="governador"]').get_attribute("class") or "")

        page.locator("#colinhaTrain").click()
        click_urna_digits(page, "13")
        expect(page.locator("#urnaCandidate")).to_contain_text("LEGENDA")
        assert page.locator("#urnaConfirm").is_enabled()

        assert not errors, errors
    finally:
        context.close()


def test_senate_duplicate_in_urna(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        fill_vote(page, "federal", "1313")
        fill_vote(page, "estadual", "13131")
        fill_vote(page, "senador1", "133")
        fill_vote(page, "senador2", "133")
        fill_vote(page, "governador", "13")
        fill_vote(page, "presidente", "13")

        expect(page.locator('[data-colinha-warning="senador2"]')).to_be_visible()

        page.locator("#colinhaTrain").click()
        for number in ("1313", "13131", "133"):
            click_urna_digits(page, number)
            page.locator("#urnaConfirm").click()

        click_urna_digits(page, "133")
        expect(page.locator("#urnaCompare")).to_contain_text("mesma candidatura")
        expect(page.locator("#urnaCompare")).to_contain_text("seria anulado")

        assert not errors, errors
    finally:
        context.close()


def test_state_picker_and_df(browser: Browser, base_url: str) -> None:
    context = new_context(browser, scope="br")
    try:
        page, errors = page_with_errors(context, base_url)
        page.locator('[data-view="colinha"]').click()
        expect(page.locator("#colinhaStateNeeded")).to_be_visible()
        page.locator("#colinhaChooseState").click()
        expect(page.locator('#stateList [data-state="br"]')).to_have_count(0)
        page.locator('#stateList [data-state="ms"]').click()
        expect(page.locator("#colinhaBody")).to_be_visible()
        expect(page.locator("#colinhaUfText")).to_have_text("MS")

        page.locator("#colinhaUfBtn").click()
        page.locator('#stateList [data-state="df"]').click()
        expect(page.locator("#colinhaUfText")).to_have_text("DF")
        expect(page.locator('[data-colinha-slot="estadual"] h3')).to_have_text("DEPUTADO DISTRITAL")
        assert not errors, errors
    finally:
        context.close()


def test_source_failure_is_visible(browser: Browser, base_url: str) -> None:
    context = new_context(browser, fail_office="estadual")
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        fill_vote(page, "estadual", "13131")
        expect(page.locator('[data-colinha-match="estadual"]')).to_contain_text("Fonte indisponível", timeout=10_000)
        assert "is-invalid" in (page.locator('[data-colinha-slot="estadual"]').get_attribute("class") or "")

        page.locator('[data-colinha-search="estadual"]').click()
        expect(page.locator('[data-colinha-inline-results="estadual"]')).to_contain_text("Não foi possível carregar", timeout=10_000)
        assert not errors, errors
    finally:
        context.close()


def test_corrupt_storage_recovers(browser: Browser, base_url: str) -> None:
    context = new_context(browser, corrupt_storage=True)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        for key in ("federal", "estadual", "senador1", "senador2", "governador", "presidente"):
            expect(page.locator(f'[data-colinha-input="{key}"]')).to_have_value("")
        assert not errors, errors
    finally:
        context.close()


def test_responsive_matrix(browser: Browser, base_url: str) -> None:
    sizes = [
        {"width": 320, "height": 568},
        {"width": 360, "height": 640},
        {"width": 375, "height": 667},
        {"width": 390, "height": 844},
        {"width": 430, "height": 932},
        {"width": 768, "height": 1024},
    ]
    for size in sizes:
        context = new_context(browser, viewport=size)
        try:
            page, errors = page_with_errors(context, base_url)
            open_colinha(page)
            fill_vote(page, "estadual", "13131")
            fill_vote(page, "senador1", "133")
            assert_no_horizontal_overflow(page)
            if size["width"] in {320, 390}:
                page.screenshot(
                    path=str(ARTIFACTS / f"colinha-{size['width']}x{size['height']}.png"),
                )
            assert not errors, (size, errors)
        finally:
            context.close()


def test_telegram_fullscreen_and_keyboard_guard(browser: Browser, base_url: str) -> None:
    context = new_context(
        browser,
        viewport={"width": 390, "height": 844},
        telegram_mock=True,
    )
    try:
        page, errors = page_with_errors(context, base_url)
        page.wait_for_function("document.body.classList.contains('tg-fullscreen')", timeout=5_000)
        open_colinha(page)

        # Validate the layout guard used when visualViewport reports a mobile keyboard.
        # Detection itself is source-gated in app/smoke.py; here we verify the resulting CSS state.
        page.evaluate("document.body.classList.add('keyboard-open')")
        body_class = page.locator("body").get_attribute("class") or ""
        assert "keyboard-open" in body_class, body_class
        page.wait_for_timeout(250)
        nav_opacity = page.locator(".bottom-nav").evaluate("el => getComputedStyle(el).opacity")
        sticky_opacity = page.locator(".colinha-sticky-actions").evaluate("el => getComputedStyle(el).opacity")
        assert float(nav_opacity) < 0.01, ("nav_opacity", nav_opacity, body_class)
        assert float(sticky_opacity) < 0.01, ("sticky_opacity", sticky_opacity, body_class)

        page.evaluate("document.body.classList.remove('keyboard-open')")

        # Colinha header should sit directly below Telegram's fullscreen chrome,
        # without adding the old extra controls guard a second time.
        geometry = page.evaluate(
            """
            () => {
              const head = document.querySelector('.colinha-head').getBoundingClientRect();
              const actions = document.querySelector('.colinha-sticky-actions').getBoundingClientRect();
              const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
              return {
                headTop: head.top,
                actionBottom: actions.bottom,
                navTop: nav.top,
                viewportHeight: innerHeight,
              };
            }
            """
        )
        assert geometry["headTop"] < 125, geometry
        assert geometry["actionBottom"] <= geometry["navTop"] - 4, geometry
        assert geometry["actionBottom"] < geometry["viewportHeight"], geometry

        assert_no_horizontal_overflow(page)
        assert not errors, errors
    finally:
        context.close()


def test_urna_fits_mobile_viewport(browser: Browser, base_url: str) -> None:
    sizes = [
        {"width": 320, "height": 568},
        {"width": 375, "height": 667},
        {"width": 390, "height": 844},
        {"width": 430, "height": 932},
    ]
    for size in sizes:
        context = new_context(browser, viewport=size, telegram_mock=True)
        try:
            page, errors = page_with_errors(context, base_url)
            page.wait_for_function("document.body.classList.contains('tg-fullscreen')", timeout=5_000)
            open_colinha(page)
            page.locator("#colinhaTrain").click()
            expect(page.locator("#urnaModal")).to_be_visible()
            page.wait_for_timeout(380)

            geometry = page.evaluate(
                """
                () => {
                  const modal = document.querySelector('#urnaModal');
                  const wrap = document.querySelector('.urna-wrap').getBoundingClientRect();
                  const machine = document.querySelector('.urna-machine').getBoundingClientRect();
                  const keypad = document.querySelector('.urna-keypad-wrap').getBoundingClientRect();
                  const footer = document.querySelector('.urna-footer').getBoundingClientRect();
                  return {
                    viewportHeight: innerHeight,
                    modalScrollHeight: modal.scrollHeight,
                    modalClientHeight: modal.clientHeight,
                    wrapTop: wrap.top,
                    wrapBottom: wrap.bottom,
                    machineBottom: machine.bottom,
                    keypadBottom: keypad.bottom,
                    footerBottom: footer.bottom,
                  };
                }
                """
            )
            assert geometry["wrapTop"] >= 0, (size, geometry)
            assert geometry["wrapBottom"] <= geometry["viewportHeight"] + 1, (size, geometry)
            assert geometry["machineBottom"] <= geometry["viewportHeight"] + 1, (size, geometry)
            assert geometry["keypadBottom"] <= geometry["viewportHeight"] + 1, (size, geometry)
            assert geometry["footerBottom"] <= geometry["viewportHeight"] + 1, (size, geometry)
            assert geometry["modalScrollHeight"] <= geometry["modalClientHeight"] + 1, (size, geometry)

            if size["width"] == 390:
                page.screenshot(path=str(ARTIFACTS / "urna-fit-390x844.png"))

            assert not errors, (size, errors)
        finally:
            context.close()




def test_results_use_real_candidates_at_zero_before_apuration(browser: Browser, base_url: str) -> None:
    context = new_context(browser, viewport={"width": 390, "height": 844}, telegram_mock=True)
    try:
        page, errors = page_with_errors(context, base_url)
        page.wait_for_function("document.body.classList.contains('tg-fullscreen')", timeout=5_000)
        page.wait_for_selector('[data-real-candidate]', timeout=8_000)
        page.wait_for_timeout(180)

        expect(page.locator("#summaryModeTitle")).to_have_text("APURAÇÃO")
        expect(page.locator("#totalVotes")).to_have_text("0 votos")
        expect(page.locator("#validShare")).to_have_text("0%")
        expect(page.locator("#candidateArea")).to_contain_text("GABI PRESIDÊNCIA")
        expect(page.locator("#candidateArea")).to_contain_text("0%")
        expect(page.locator("#candidateArea")).to_contain_text("Aguardando apuração oficial")

        assert page.locator("#candidateArea").get_by_text("Intenção de voto · não é apuração").count() == 0
        assert not errors, errors
    finally:
        context.close()


def test_fullscreen_all_views_and_modals_spacing(browser: Browser, base_url: str) -> None:
    context = new_context(
        browser,
        viewport={"width": 390, "height": 844},
        telegram_mock=True,
    )
    try:
        page, errors = page_with_errors(context, base_url)
        page.wait_for_function("document.body.classList.contains('tg-fullscreen')", timeout=5_000)
        page.wait_for_selector('[data-real-candidate]', timeout=8_000)
        page.wait_for_timeout(250)

        results_geometry = page.evaluate(
            """
            () => {
              const rect = sel => document.querySelector(sel).getBoundingClientRect();
              const topbar = rect('.topbar');
              const title = rect('.topbar .title');
              const location = rect('.location-bar');
              const summary = rect('#summaryCard');
              const chips = rect('#officeChips');
              const officeHead = rect('.office-result-head');
              const nav = rect('.bottom-nav');
              return {
                viewportHeight: innerHeight,
                titleTop: title.top,
                topbarBottom: topbar.bottom,
                locationTop: location.top,
                locationBottom: location.bottom,
                summaryTop: summary.top,
                chipsBottom: chips.bottom,
                officeHeadBottom: officeHead.bottom,
                navTop: nav.top,
                navBottom: nav.bottom,
              };
            }
            """
        )
        assert 82 <= results_geometry["titleTop"] <= 132, results_geometry
        assert results_geometry["locationTop"] - results_geometry["topbarBottom"] <= 2, results_geometry
        assert 0 <= results_geometry["summaryTop"] - results_geometry["locationBottom"] <= 24, results_geometry
        assert results_geometry["chipsBottom"] <= results_geometry["navTop"] - 4, results_geometry
        assert results_geometry["officeHeadBottom"] <= results_geometry["navTop"] - 2, results_geometry
        assert results_geometry["navBottom"] <= results_geometry["viewportHeight"] + 1, results_geometry
        page.screenshot(path=str(ARTIFACTS / "audit-results-390x844.png"))

        # Every public office tab should render inside the same compact fullscreen shell.
        for office in ("presidente", "governador", "senador", "federal", "estadual"):
            page.locator(f'[data-office="{office}"]').click()
            page.wait_for_selector('[data-real-candidate]', timeout=8_000)
            page.wait_for_timeout(80)
            assert_no_horizontal_overflow(page)
            office_geometry = page.evaluate(
                """
                () => {
                  const title = document.querySelector('.topbar .title').getBoundingClientRect();
                  const office = document.querySelector('.office-result-head').getBoundingClientRect();
                  const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
                  return {titleTop:title.top, officeTop:office.top, navTop:nav.top, viewportHeight:innerHeight};
                }
                """
            )
            assert office_geometry["titleTop"] <= 132, (office, office_geometry)
            assert office_geometry["officeTop"] < office_geometry["viewportHeight"] * 1.8, (office, office_geometry)

        # Candidate detail modal.
        page.locator('[data-real-candidate]').first.click()
        page.wait_for_function("document.querySelector('#candidateModal').classList.contains('show')")
        page.wait_for_timeout(340)
        candidate_geometry = page.evaluate(
            """
            () => {
              const header = document.querySelector('#candidateModal .modal-top').getBoundingClientRect();
              const hero = document.querySelector('#candidateModal .detail-hero').getBoundingClientRect();
              return {headerTop:header.top, headerBottom:header.bottom, heroTop:hero.top, viewportHeight:innerHeight};
            }
            """
        )
        assert candidate_geometry["headerTop"] <= 1, candidate_geometry
        assert candidate_geometry["headerBottom"] <= 175, candidate_geometry
        assert 0 <= candidate_geometry["heroTop"] - candidate_geometry["headerBottom"] <= 2, candidate_geometry
        page.screenshot(path=str(ARTIFACTS / "audit-candidate-modal-390x844.png"))
        page.locator("#detailHeart").click()
        page.locator("#candidateModal [data-full-close]").click()
        page.wait_for_timeout(340)

        # Advanced analysis modal.
        page.locator("#analysisBtn").click()
        page.wait_for_function("document.querySelector('#analysisModal').classList.contains('show')")
        page.wait_for_timeout(340)
        analysis_geometry = page.evaluate(
            """
            () => {
              const header = document.querySelector('#analysisModal .modal-top').getBoundingClientRect();
              const body = document.querySelector('#analysisModal .analysis-body').getBoundingClientRect();
              return {headerBottom:header.bottom, bodyTop:body.top, viewportHeight:innerHeight};
            }
            """
        )
        assert analysis_geometry["headerBottom"] <= 175, analysis_geometry
        assert 0 <= analysis_geometry["bodyTop"] - analysis_geometry["headerBottom"] <= 24, analysis_geometry
        page.screenshot(path=str(ARTIFACTS / "audit-analysis-modal-390x844.png"))
        page.locator("#analysisModal [data-full-close]").click()
        page.wait_for_timeout(340)

        # State sheet must use the available fullscreen height instead of leaving another guard above it.
        page.locator("#locationTrigger").click()
        page.wait_for_function("document.querySelector('#locationSheet').classList.contains('show')")
        page.wait_for_timeout(380)
        sheet_geometry = page.evaluate(
            """
            () => {
              const sheet = document.querySelector('#locationSheet').getBoundingClientRect();
              return {top:sheet.top, bottom:sheet.bottom, height:sheet.height, viewportHeight:innerHeight};
            }
            """
        )
        assert 70 <= sheet_geometry["top"] <= 125, sheet_geometry
        assert sheet_geometry["bottom"] <= sheet_geometry["viewportHeight"] + 1, sheet_geometry
        page.screenshot(path=str(ARTIFACTS / "audit-location-sheet-390x844.png"))
        page.locator("#locationSheet [data-close]").click()
        page.wait_for_timeout(340)

        # Favorites retains the same global chrome and must start directly after the location bar.
        page.locator('[data-view="favorites"]').click()
        expect(page.locator("#favoritesView")).to_be_visible()
        expect(page.locator("#favoritesList .candidate-card")).to_have_count(1)
        favorites_geometry = page.evaluate(
            """
            () => {
              const title = document.querySelector('.topbar .title').getBoundingClientRect();
              const location = document.querySelector('.location-bar').getBoundingClientRect();
              const heading = document.querySelector('#favoritesView .section-head').getBoundingClientRect();
              const card = document.querySelector('#favoritesList .candidate-card').getBoundingClientRect();
              const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
              return {
                titleTop:title.top,
                locationBottom:location.bottom,
                headingTop:heading.top,
                cardTop:card.top,
                navBottom:nav.bottom,
                viewportHeight:innerHeight
              };
            }
            """
        )
        assert favorites_geometry["titleTop"] <= 132, favorites_geometry
        assert 8 <= favorites_geometry["headingTop"] - favorites_geometry["locationBottom"] <= 42, favorites_geometry
        assert favorites_geometry["cardTop"] - favorites_geometry["headingTop"] <= 72, favorites_geometry
        assert favorites_geometry["navBottom"] <= favorites_geometry["viewportHeight"] + 1, favorites_geometry
        page.screenshot(path=str(ARTIFACTS / "audit-favorites-390x844.png"))

        # Colinha has its own header but must respect the same single safe-top clearance.
        page.locator('[data-view="colinha"]').click()
        expect(page.locator("#colinhaView")).to_be_visible()
        page.wait_for_timeout(220)
        colinha_geometry = page.evaluate(
            """
            () => {
              const head = document.querySelector('.colinha-head').getBoundingClientRect();
              const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
              const actions = document.querySelector('.colinha-sticky-actions').getBoundingClientRect();
              return {
                headTop:head.top,
                actionsBottom:actions.bottom,
                navTop:nav.top,
                viewportHeight:innerHeight
              };
            }
            """
        )
        assert 82 <= colinha_geometry["headTop"] <= 125, colinha_geometry
        assert colinha_geometry["actionsBottom"] <= colinha_geometry["navTop"] - 4, colinha_geometry

        assert not errors, errors
    finally:
        context.close()


def test_results_scroll_clearance(browser: Browser, base_url: str) -> None:
    context = new_context(
        browser,
        viewport={"width": 390, "height": 844},
        telegram_mock=True,
    )
    try:
        page, errors = page_with_errors(context, base_url)
        page.wait_for_function("document.body.classList.contains('tg-fullscreen')", timeout=5_000)
        page.wait_for_selector('[data-real-candidate]', timeout=8_000)
        # Verify the page can scroll its last interactive content above the fixed nav.
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(120)
        geometry = page.evaluate(
            """
            () => {
              const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
              const more = document.querySelector('#candidateMore');
              const area = document.querySelector('#candidateArea').getBoundingClientRect();
              const target = more && getComputedStyle(more).display !== 'none' ? more.getBoundingClientRect() : area;
              return {
                navTop:nav.top,
                targetBottom:target.bottom,
                documentBottom:document.documentElement.scrollHeight,
                viewportHeight:innerHeight,
                scrollY
              };
            }
            """
        )
        assert geometry["targetBottom"] <= geometry["navTop"] + 1, geometry
        assert not errors, errors
    finally:
        context.close()


def test_input_sanitization_and_limits(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)

        fill_vote(page, "federal", "1a3-1 3 999")
        expect(page.locator('[data-colinha-input="federal"]')).to_have_value("1313")
        expect(page.locator('[data-colinha-match="federal"]')).to_contain_text("ANA FEDERAL")

        fill_vote(page, "senador1", "1-3-3-9")
        expect(page.locator('[data-colinha-input="senador1"]')).to_have_value("133")

        fill_vote(page, "governador", "abc22")
        expect(page.locator('[data-colinha-input="governador"]')).to_have_value("22")
        expect(page.locator('[data-colinha-match="governador"]')).to_contain_text("FELIPE GOVERNO")
        assert not errors, errors
    finally:
        context.close()


def test_state_scoped_persistence(browser: Browser, base_url: str) -> None:
    context = new_context(browser, scope="ms")
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        fill_vote(page, "federal", "1313")
        expect(page.locator('[data-colinha-input="federal"]')).to_have_value("1313")

        page.locator("#colinhaUfBtn").click()
        page.locator('#stateList [data-state="df"]').click()
        expect(page.locator("#colinhaUfText")).to_have_text("DF")
        expect(page.locator('[data-colinha-input="federal"]')).to_have_value("")
        fill_vote(page, "federal", "2211")

        page.locator("#colinhaUfBtn").click()
        page.locator('#stateList [data-state="ms"]').click()
        expect(page.locator("#colinhaUfText")).to_have_text("MS")
        expect(page.locator('[data-colinha-input="federal"]')).to_have_value("1313")
        assert not errors, errors
    finally:
        context.close()


def test_inline_search_switch_and_accents(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)

        page.locator('[data-colinha-search="federal"]').click()
        expect(page.locator('[data-colinha-inline-search="federal"]')).to_be_visible()

        page.locator('[data-colinha-search="governador"]').click()
        expect(page.locator('[data-colinha-inline-search="federal"]')).to_be_hidden()
        expect(page.locator('[data-colinha-search="federal"]')).to_be_visible()
        expect(page.locator('[data-colinha-inline-search="governador"]')).to_be_visible()

        page.locator('[data-colinha-search="presidente"]').click()
        page.locator('[data-colinha-name-input="presidente"]').fill("presidencia")
        expect(page.locator('[data-colinha-inline-results="presidente"] .colinha-inline-item')).to_have_count(2)
        expect(page.locator('[data-colinha-inline-results="presidente"]')).to_contain_text("GABI PRESIDÊNCIA")
        assert not errors, errors
    finally:
        context.close()


def test_empty_colinha_training_allows_blank(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        page.locator("#colinhaTrain").click()
        page.locator("#urnaBlank").click()
        expect(page.locator("#urnaCandidate")).to_contain_text("VOTO EM BRANCO")
        expect(page.locator("#urnaCompare")).to_contain_text("não anotou")
        assert page.locator("#urnaConfirm").is_enabled()
        page.locator("#urnaConfirm").click()
        expect(page.locator("#urnaOfficeTitle")).to_contain_text("DEPUTADO ESTADUAL")
        assert not errors, errors
    finally:
        context.close()



def test_native_share_failure_falls_back_to_download(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    context.add_init_script(
        """
        Object.defineProperty(navigator, 'canShare', {
          configurable: true,
          value: () => true
        });
        Object.defineProperty(navigator, 'share', {
          configurable: true,
          value: async () => {
            const error = new Error('native share unavailable');
            error.name = 'NotAllowedError';
            throw error;
          }
        });
        """
    )
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        fill_vote(page, "federal", "1313")
        with page.expect_download(timeout=15_000) as download_info:
            page.locator("#colinhaSaveImage").click()
        download = download_info.value
        assert download.suggested_filename == "minha-colinha-MS.png"
        assert Path(download.path()).stat().st_size > 1000
        expect(page.locator(".colinha-toast")).to_contain_text("salva pelo navegador")
        assert not errors, errors
    finally:
        context.close()


def test_accessibility_basics(browser: Browser, base_url: str) -> None:
    context = new_context(browser)
    try:
        page, errors = page_with_errors(context, base_url)
        open_colinha(page)
        result = page.evaluate(
            """
            () => {
              const root = document.querySelector('#colinhaView');
              const buttons = [...root.querySelectorAll('button')];
              const unlabeled = buttons.filter(btn => {
                const text = (btn.textContent || '').trim();
                return !text && !btn.getAttribute('aria-label') && !btn.getAttribute('title');
              }).length;
              const inputs = [...root.querySelectorAll('input')];
              const unlabeledInputs = inputs.filter(input => !input.getAttribute('aria-label')).length;
              return {unlabeled, unlabeledInputs};
            }
            """
        )
        assert result == {"unlabeled": 0, "unlabeledInputs": 0}, result
        assert not errors, errors
    finally:
        context.close()


def run_suite(playwright: Playwright, base_url: str) -> None:
    engine = os.getenv("E2E_BROWSER", "chromium").strip().lower()
    if engine not in {"chromium", "webkit", "firefox"}:
        raise SystemExit(f"Unsupported E2E_BROWSER={engine}")
    browser_type = getattr(playwright, engine)
    print(f"BROWSER_ENGINE={engine}")
    browser = browser_type.launch(headless=True)
    tests: list[tuple[str, Callable[[Browser, str], None]]] = [
        ("full_flow", test_full_flow),
        ("legend_and_invalid_votes", test_legend_and_invalid_votes),
        ("senate_duplicate_in_urna", test_senate_duplicate_in_urna),
        ("state_picker_and_df", test_state_picker_and_df),
        ("source_failure_is_visible", test_source_failure_is_visible),
        ("corrupt_storage_recovers", test_corrupt_storage_recovers),
        ("responsive_matrix", test_responsive_matrix),
        ("telegram_fullscreen_and_keyboard_guard", test_telegram_fullscreen_and_keyboard_guard),
        ("urna_fits_mobile_viewport", test_urna_fits_mobile_viewport),
        ("results_use_real_candidates_at_zero_before_apuration", test_results_use_real_candidates_at_zero_before_apuration),
        ("fullscreen_all_views_and_modals_spacing", test_fullscreen_all_views_and_modals_spacing),
        ("results_scroll_clearance", test_results_scroll_clearance),
        ("input_sanitization_and_limits", test_input_sanitization_and_limits),
        ("state_scoped_persistence", test_state_scoped_persistence),
        ("inline_search_switch_and_accents", test_inline_search_switch_and_accents),
        ("empty_colinha_training_allows_blank", test_empty_colinha_training_allows_blank),
        ("native_share_failure_falls_back_to_download", test_native_share_failure_falls_back_to_download),
        ("accessibility_basics", test_accessibility_basics),
    ]
    failures: list[tuple[str, BaseException]] = []
    started = time.perf_counter()
    try:
        for name, test in tests:
            test_started = time.perf_counter()
            try:
                test(browser, base_url)
                print(f"[PASS] {name} ({time.perf_counter() - test_started:.2f}s)")
            except BaseException as exc:
                failures.append((name, exc))
                print(f"[FAIL] {name}: {exc!r}")
    finally:
        browser.close()

    elapsed = time.perf_counter() - started
    print(f"BROWSER_SUITE tests={len(tests)} failures={len(failures)} elapsed={elapsed:.2f}s")
    if failures:
        for name, exc in failures:
            print(f" - {name}: {type(exc).__name__}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    with TestServer() as server:
        with sync_playwright() as playwright:
            run_suite(playwright, server.base_url)
