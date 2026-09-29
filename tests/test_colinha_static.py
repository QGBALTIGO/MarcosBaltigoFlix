from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_colinha_is_wired_into_the_webapp():
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'id="colinhaView"' in html
    assert 'data-view="colinha"' in html
    assert 'id="urnaModal"' in html
    assert '/static/colinha.js' in html


def test_colinha_script_has_all_six_voting_steps():
    js = (STATIC / "colinha.js").read_text(encoding="utf-8")

    for key in (
        "federal",
        "estadual",
        "senador1",
        "senador2",
        "governador",
        "presidente",
    ):
        assert "key:'" + key + "'" in js

    assert "digits:4" in js
    assert "digits:5" in js
    assert js.count("digits:3") >= 2
    assert js.count("digits:2") >= 2


def test_colinha_keeps_poll_backend_available_but_replaces_tab():
    html = (STATIC / "index.html").read_text(encoding="utf-8")

    assert 'id="pollsView"' in html
    assert 'data-view="polls"' not in html
