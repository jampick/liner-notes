import json
from types import SimpleNamespace as NS

from liner_notes import generate as gen
from liner_notes.bank import Bank, read_json, write_json


def reply(song):
    body = {"title": song, "artist": "Christopher Cross", "year": 1979, "year_note": "", "album": "Christopher Cross",
            "made": "Michael Omartian produced it at Warner Bros. Recording Studios — with Cross on a Strat.",
            "context": "It went to number one in 1980.", "listen_for": [{"where": "the intro", "note": "the guitar"}],
            "leads_to": {"artist": "Steely Dan", "title": "Peg", "year": 1977, "why": "Same session circle."},
            "culture": [], "sources": ["https://example.org/sailing"], "unverified": ""}
    return "Here you go.\n```json\n" + json.dumps(body) + "\n```"


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self):
        self.calls = []
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.calls.append(kw)
        title = kw["messages"][0]["content"].split('"')[1]
        usage = NS(input_tokens=2000, output_tokens=1500, cache_read_input_tokens=3000, cache_creation_input_tokens=0,
                   server_tool_use=NS(web_search_requests=4))
        return FakeStream(NS(content=[NS(type="text", text=reply(title))], stop_reason="end_turn", usage=usage, model="claude-opus-5-5"))


def lookup(artist, title, yt):
    return {"year": {"Sailing": 1979, "Ride Like the Wind": 1979, "Steppin' Out": 1982, "Avalon": 1982, "Lowdown": 1976}.get(title, 1980),
            "mbid": "00000000-0000-0000-0000-000000000000", "isrc": ["USWB19900001"]}


TOML = """
[[track]]
path = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
title = "Sailing - 2019 Remaster"
artist = "Christopher Cross"

[[track]]
path = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
title = "Christopher Cross - Ride Like the Wind (Official Video)"
artist = "ChristopherCrossVEVO"

[[track]]
path = "https://www.youtube.com/watch?v=0Rw-UsRPNqc"
title = "Protection"
artist = "Massive Attack"
"""


def setup(tmp_path, monkeypatch):
    home, data, cliamp = tmp_path / "home", tmp_path / "data", tmp_path / "cliamp"
    write_json(home / "songs/massive-attack--protection.json", {"id": "massive-attack--protection", "title": "Protection", "artist": "Massive Attack",
                                                                "year": 1994, "aliases": {"youtube": ["0Rw-UsRPNqc"]}, "notes": {"made": "Already written."},
                                                                "review": {"state": "reviewed"}})
    (cliamp / "playlists").mkdir(parents=True)
    (cliamp / "playlists/Test Mix.toml").write_text(TOML)
    monkeypatch.setenv("LINER_NOTES_HOME", str(home))
    monkeypatch.setenv("LINER_NOTES_DATA", str(data))
    monkeypatch.setenv("CLIAMP_CONFIG_DIR", str(cliamp))
    return home, data


def test_plan_without_writing(tmp_path, monkeypatch):
    home, data = setup(tmp_path, monkeypatch)
    j, spent = gen.generate("Test Mix", lookup=lookup, log=lambda *a: None)
    assert spent == 0
    assert j["songs"] == ["christopher-cross--sailing", "christopher-cross--ride-like-the-wind", "massive-attack--protection"]
    assert j["pins"]["christopher-cross--ride-like-the-wind"] == {"youtube": "BBBBBBBBBBB"}
    assert j["eras"][0]["from"] == 1979 and j["eras"][-1]["to"] == 1994
    s = read_json(data / "songs/christopher-cross--sailing.json")
    assert s["isrc"] == ["USWB19900001"] and s["review"]["state"] == "draft" and not s["notes"]
    # the shipped song was matched, not duplicated
    assert not (data / "songs/massive-attack--protection.json").exists()
    assert "massive-attack--protection" in Bank().journeys["test-mix"]["songs"]


def test_write_with_budget(tmp_path, monkeypatch):
    home, data = setup(tmp_path, monkeypatch)
    client = FakeClient()
    j, spent = gen.generate("Test Mix", write=True, budget=5, client=client, lookup=lookup, log=lambda *a: None)
    assert len(client.calls) == 2  # Protection already had notes
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t["type"] for t in call["tools"]} == {"web_search_20260209", "web_fetch_20260209"}
    s = read_json(data / "songs/christopher-cross--sailing.json")
    assert s["notes"]["sources"] and s["provenance"]["ai_generated"] and s["review"]["state"] == "draft"
    assert "—" not in json.dumps(s, ensure_ascii=False)
    per = (2000 * 4 + 1500 * 20 + 3000 * 0.2) / 1e6 + 4 * 0.01
    assert abs(spent - 2 * per) < 1e-9


def test_budget_stops_early(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    client = FakeClient()
    gen.generate("Test Mix", write=True, budget=0.12, client=client, lookup=lookup, log=lambda *a: None)
    assert len(client.calls) == 1


def test_cut_eras():
    years = [1976, 1977, 1977, 1978, 1979, 1979, 1980, 1981, 1982, 1982, 1983, 1984]
    b = gen.cut_eras(years, most=3)
    assert b[0][0] == 1976 and b[-1][1] == 1984 and len(b) == 3
    assert all(lo <= hi for lo, hi in b) and all(b[i][1] < b[i + 1][0] for i in range(len(b) - 1))
    assert gen.cut_eras([1990, 1990]) == [(1990, 1990)]


def test_generated_journey_builds(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    gen.generate("Test Mix", lookup=lookup, log=lambda *a: None)
    from liner_notes.build import compose

    data, problems, _ = compose("test-mix", public=True)
    assert len(data["stops"]) == 3 and data["judges"] == {}
    assert all(s["quote"] == "" for s in data["stops"])
