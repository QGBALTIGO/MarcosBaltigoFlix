from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
STATIC = APP / "static"


def test_public_http_route_contracts_are_present():
    main = (APP / "main.py").read_text(encoding="utf-8")

    expected = {
        "/",
        "/api/health",
        "/api/result",
        "/api/g1/poll",
        "/api/g1/status",
        "/api/g1/catalog",
        "/api/location/reverse",
        "/api/candidates",
        "/api/candidates/{office}/{scope}/{candidate_id}",
    }

    declared = set(
        re.findall(r'@app\.get\("([^"]+)"\)', main)
    )
    assert expected <= declared, sorted(expected - declared)


def test_frontend_api_references_have_backend_routes():
    main = (APP / "main.py").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    colinha_js = (STATIC / "colinha.js").read_text(encoding="utf-8")

    declared = set(re.findall(r'@app\.get\("([^"]+)"\)', main))

    # Dynamic frontend URLs are normalized to their backend route templates.
    required_by_frontend = {
        "/api/result",
        "/api/g1/poll",
        "/api/g1/catalog",
        "/api/location/reverse",
        "/api/candidates",
        "/api/candidates/{office}/{scope}/{candidate_id}",
    }

    assert required_by_frontend <= declared, sorted(required_by_frontend - declared)
    for literal in (
        "/api/result?",
        "/api/g1/poll?",
        "/api/g1/catalog",
        "/api/location/reverse?",
        "/api/candidates?office=",
        "/api/candidates/",
    ):
        assert literal in app_js or literal in colinha_js, literal


def test_navigation_views_have_exact_dom_targets():
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    nav_views = re.findall(r'data-view="([^"]+)"', html)
    assert nav_views == ["results", "colinha", "favorites"]

    for view in nav_views:
        target = {
            "results": "resultsView",
            "colinha": "colinhaView",
            "favorites": "favoritesView",
        }[view]
        assert f'id="{target}"' in html


def test_fullscreen_spacing_uses_one_top_clearance():
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    assert "const controlsGuard=0;" in js
    assert "var(--tg-controls-guard)" not in css
    assert "body.tg-fullscreen .topbar" in css
    assert "body.tg-fullscreen .modal-top" in css
    assert "body.tg-fullscreen .sheet" in css


def test_results_summary_uses_official_vote_categories():
    js = (STATIC / "app.js").read_text(encoding="utf-8")

    for label in (
        "VOTAÇÃO",
        "Votos a candidatos concorrentes",
        "Votos válidos",
        "Anulados",
        "Sub judice",
        "Nulos",
        "Em branco",
    ):
        assert label in js

    assert "d.valid_votes" in js
    assert "d.void_votes" in js
    assert "d.void_sub_judice_votes" in js
    assert "d.null_votes" in js
    assert "d.blank_votes" in js
