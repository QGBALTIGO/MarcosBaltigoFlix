from app.tse import parse_ea20


def fixture():
    return {
        "ele": 21270,
        "t": 1,
        "f": "s",
        "tpabr": "br",
        "cdabr": "br",
        "dg": "28/09/2026",
        "hg": "15:59:55",
        "idg": 12345,
        "dv": "s",
        "dt": "28/09/2026",
        "ht": "15:59:50",
        "tf": "n",
        "and": "p",
        "md": "n",
        "esae": "n",
        "mnae": [],
        "carg": [{
            "cd": 1,
            "nmn": "Presidente",
            "agr": [{
                "par": [
                    {"n": 10, "sg": "AAA", "nm": "Partido A", "cand": [{"n": 10, "sqcand": 1, "nm": "Candidato A", "nmu": "A", "seq": 1, "e": "n", "st": "", "dvt": "Válido", "vap": 600, "pvap": 60.0, "pvapn": 60.0, "vs": [{"tp": "v", "nmu": "Vice A", "sgp": "AAA"}]}]},
                    {"n": 20, "sg": "BBB", "nm": "Partido B", "cand": [{"n": 20, "sqcand": 2, "nm": "Candidato B", "nmu": "B", "seq": 2, "e": "n", "st": "", "dvt": "Válido", "vap": 400, "pvap": 40.0, "pvapn": 40.0, "vs": []}]},
                ]
            }]
        }],
        "s": {"ts": 100, "st": 75, "snt": 25, "pst": 75.0},
        "e": {"te": 2000, "c": 1200, "pc": 80.0, "a": 300, "pa": 20.0},
        "v": {"tv": 1200, "vv": 1000, "vb": 50, "tvn": 150, "van": 0, "vansj": 0},
    }


def test_parse_ea20_candidates_and_totals():
    r = parse_ea20(fixture(), "br", "https://example.test/result.json")
    assert r.election_code == 21270
    assert r.sections_counted_pct == 75.0
    assert r.valid_votes == 1000
    assert len(r.candidates) == 2
    assert r.candidates[0].ballot_name == "A"
    assert r.candidates[0].party == "AAA"
    assert r.candidates[0].vice_name == "Vice A"
    assert r.candidates[0].votes == 600


def test_math_definition_is_preserved():
    data = fixture()
    data["md"] = "s"
    r = parse_ea20(data, "br")
    assert r.mathematically_defined == "s"


def test_parse_ea20_orders_live_candidates_by_percentage_then_votes():
    data = fixture()
    candidates = data["carg"][0]["agr"][0]["par"]
    first = candidates[0]["cand"][0]
    second = candidates[1]["cand"][0]

    first["pvap"] = 50.0
    first["vap"] = 500
    second["pvap"] = 50.0
    second["vap"] = 650

    result = parse_ea20(data, "br")
    assert [candidate.ballot_name for candidate in result.candidates] == ["B", "A"]
