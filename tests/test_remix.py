import json
import re

import pytest

from liner_notes import build as B
from liner_notes import remix
from liner_notes.bank import Bank, write_json
from tests.helpers_build import iso, make_bank  # noqa: F401

LINES = [{"t": 10.0, "text": "first line"}, {"t": 14.0, "text": "second line"}, {"t": 18.0, "text": "third line"}]
EVENTS = [
    {"line": 0, "text": "first line", "chop": False, "times": [30.0, 45.0, 60.0], "end": 70.0, "orig": 10.0},
    {"line": 1, "text": "second line", "chop": True, "times": [80.0], "end": 83.0, "orig": 14.0},
]


def remix_journey(d):
    root = d["home"]
    make_bank(root)
    write_json(
        root / "journeys/test-house/journey.json",
        {
            "slug": "test-house",
            "kind": "remix",
            "title": "Test House",
            "titleA": "Test",
            "titleB": "House",
            "kicker": "2 remixes",
            "description": "Remixes.",
            "songs": [],
            "panel": {"quotes": {"First Song": {"who": "A Judge", "quote": "Keeps the hook."}}},
            "remixes": [
                {
                    "title": "First Song (Club Mix)",
                    "song": "First Song",
                    "artist": "Ann Artist",
                    "path": "https://www.youtube.com/watch?v=RRRRRRRRRRR",
                    "id": "RRRRRRRRRRR",
                    "remixer": "DJ Test",
                    "label": "Club Mix",
                    "volume": 1,
                    "original": {"song": "ann-artist--first-song", "youtube": "AAAAAAAAAAA"},
                },
                {
                    "title": "Old Song (Edit)",
                    "song": "Old Song",
                    "artist": "Nobody",
                    "path": "https://soundcloud.com/x/y",
                    "id": "123",
                    "remixer": "DJ Test",
                    "label": "Edit",
                    "original": {"file": "old-song.json", "year": 1975},
                },
                {
                    "title": "Unmeasured (Mix)",
                    "song": "Unmeasured",
                    "artist": "Nobody",
                    "path": "https://www.youtube.com/watch?v=UUUUUUUUUUU",
                    "id": "UUUUUUUUUUU",
                    "remixer": "DJ Test",
                    "label": "Mix",
                    "original": {"file": "none.json"},
                },
            ],
        },
    )
    data, cache = d["data"], d["cache"]
    write_json(data / "lyrics/RRRRRRRRRRR.json", {"lines": LINES, "remix": {"events": EVENTS}})
    write_json(data / "lyrics/123.json", {"lines": LINES, "remix": {"events": EVENTS[:1]}})
    write_json(data / "lyrics/AAAAAAAAAAA.json", {"title": "First Song", "upload": 200.0, "lines": LINES + [{"t": 20.0, "text": ""}]})
    write_json(data / "remixes/originals/old-song.json", {"title": "Old Song", "upload": 180.0, "lines": LINES})
    write_json(cache / "profiles/RRRRRRRRRRR.json", {"bpm": 124.0, "seconds": 300.0, "energy": [0.2, 0.9]})
    write_json(cache / "profiles/123.json", {"bpm": 120.0, "seconds": 240.0, "energy": [0.5]})
    write_json(cache / "profiles/AAAAAAAAAAA.json", {"bpm": 250.0, "seconds": 200.0, "energy": [0.4]})
    (cache / "covers").mkdir()
    (cache / "covers/test-house-vol1.webp").write_bytes(b"vol")
    (cache / "covers/ann-artist--first-song.webp").write_bytes(b"cov")
    (cache / "video").mkdir()
    (cache / "video/AAAAAAAAAAA.mp4").write_bytes(b"v")
    return Bank([root])


def test_compose(iso):
    bank = remix_journey(iso)
    songs, skipped = remix.compose("test-house", bank, iso["cache"], log=lambda *a: None)
    assert skipped == ["Unmeasured (Mix)"]
    a, b = songs
    assert a["id"] == "RRRRRRRRRRR" and a["match"] == "First Song (Club Mix)" and a["year"] == 1990
    assert a["original"]["bpm"] == 125.0  # counted in double time, halved
    assert a["original"]["lines"] == LINES and a["video"] == {"id": "AAAAAAAAAAA", "off": 0.0, "rate": 1.0, "kind": None}
    assert a["remix"]["cover"].startswith("data:" + "image/webp") and a["original"]["cover"].startswith("data:" + "image/webp")
    assert a["notes"]["orig"][0] == {"h": "How it was made", "p": "Made in a room."}
    assert a["notes"]["panel"] == [{"h": "A Judge", "p": "Keeps the hook."}]
    heads = [n["h"] for n in a["notes"]["flip"]]
    assert heads[0] == "Same tempo, about 124 BPM" and "Keeps 2 of 3 lines" in heads and "“first line” ×3" in heads
    assert b["year"] == 1975 and b["original"]["bpm"] is None and b["original"]["length"] == 180.0 and b["video"] is None


def test_build_with_and_without_template(iso, tmp_path, monkeypatch):
    bank = remix_journey(iso)
    monkeypatch.setattr(remix, "TEMPLATE", tmp_path / "missing.html")
    out, problems = B.build("test-house", bank=bank, cache=iso["cache"], log=lambda *a: None)
    assert out == iso["dist"] / "test-house.html" and not problems
    html = out.read_text()
    songs = json.loads(re.search(r"const SONGS = (.*?);</script>", html, re.S).group(1))
    assert len(songs) == 2
    t = tmp_path / "remix.html"
    t.write_text("<title>__TITLE__</title><script>const SONGS = /*DATA*/null;\nconst X = 1;</script>")
    monkeypatch.setattr(remix, "TEMPLATE", t)
    out, _ = B.build("test-house", bank=bank, cache=iso["cache"], log=lambda *a: None)
    assert out.read_text().startswith("<title>Test House</title><script>const SONGS = [")
    with pytest.raises(B.Refused):
        B.build("test-house", public=True, bank=bank, log=lambda *a: None)


def test_nothing_measured_is_refused(iso, tmp_path, monkeypatch):
    bank = remix_journey(iso)
    for p in (iso["cache"] / "profiles").iterdir():
        p.unlink()
    monkeypatch.setattr(remix, "measure", lambda p: (_ for _ in ()).throw(ImportError("numpy")))
    with pytest.raises(B.Refused):
        B.build("test-house", bank=bank, cache=iso["cache"], log=lambda *a: None)


def test_measure_caches(iso, monkeypatch):
    (iso["cache"] / "audio").mkdir()
    (iso["cache"] / "audio/RRRRRRRRRRR.webm").write_bytes(b"a")
    calls = []
    monkeypatch.setattr(remix, "measure", lambda p: calls.append(p) or {"bpm": 120.0, "seconds": 1.0, "energy": [1.0]})
    assert remix.profile("RRRRRRRRRRR", iso["cache"])["bpm"] == 120.0
    assert remix.profile("RRRRRRRRRRR", iso["cache"])["bpm"] == 120.0
    assert len(calls) == 1 and (iso["cache"] / "profiles/RRRRRRRRRRR.json").is_file()
