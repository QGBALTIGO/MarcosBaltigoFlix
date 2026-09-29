from __future__ import annotations

import json
import mimetypes
import os
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

        if path in {"/api/result", "/api/g1/poll"}:
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

        # Search-by-name path.
        page.locator('[data-colinha-clear="estadual"]').click()
        page.locator('[data-colinha-search="estadual"]').click()
        assert "show" in (page.locator("#colinhaPickerSheet").get_attribute("class") or "")
        page.locator("#colinhaPickerSearch").fill("bruno")
        expect(page.locator("#colinhaPickerList .colinha-picker-item")).to_have_count(1)
        page.locator("#colinhaPickerList .colinha-picker-item").click()
        expect(page.locator('[data-colinha-input="estadual"]')).to_have_value("13131")

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

        page.screenshot(path=str(ARTIFACTS / "urna-fim-390x844.png"), full_page=True)

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

        page.screenshot(path=str(ARTIFACTS / "colinha-390x844.png"), full_page=True)
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
        expect(page.locator("#urnaCompare")).to_contain_text("segunda candidatura")
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
        expect(page.locator("#colinhaPickerList")).to_contain_text("Não foi possível carregar", timeout=10_000)
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
                    full_page=True,
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

        # Simulate the visual viewport shrinking as a mobile keyboard opens.
        page.locator('[data-colinha-input="governador"]').focus()
        page.evaluate(
            """
            () => {
              Object.defineProperty(window, 'innerHeight', {configurable:true, value:844});
              window.dispatchEvent(new Event('resize'));
              document.body.classList.add('keyboard-open');
            }
            """
        )
        assert "keyboard-open" in (page.locator("body").get_attribute("class") or "")
        nav_opacity = page.locator(".bottom-nav").evaluate("el => getComputedStyle(el).opacity")
        sticky_opacity = page.locator(".colinha-sticky-actions").evaluate("el => getComputedStyle(el).opacity")
        assert float(nav_opacity) == 0.0
        assert float(sticky_opacity) == 0.0

        page.locator('[data-colinha-input="governador"]').blur()
        page.evaluate("document.body.classList.remove('keyboard-open')")
        assert_no_horizontal_overflow(page)
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
    browser = playwright.chromium.launch(headless=True)
    tests: list[tuple[str, Callable[[Browser, str], None]]] = [
        ("full_flow", test_full_flow),
        ("legend_and_invalid_votes", test_legend_and_invalid_votes),
        ("senate_duplicate_in_urna", test_senate_duplicate_in_urna),
        ("state_picker_and_df", test_state_picker_and_df),
        ("source_failure_is_visible", test_source_failure_is_visible),
        ("corrupt_storage_recovers", test_corrupt_storage_recovers),
        ("responsive_matrix", test_responsive_matrix),
        ("telegram_fullscreen_and_keyboard_guard", test_telegram_fullscreen_and_keyboard_guard),
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
