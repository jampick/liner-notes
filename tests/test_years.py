"""Years and ids are honest (phase 1, item 3).

MusicBrainz answers come from tests/fixtures/musicbrainz, recorded on 2026-10-22 at one request a
second. The recording searches are trimmed to the fields the code reads (ids, titles, credits,
release status, date and release-group type); everything else is as MusicBrainz sent it. Sockets
are patched to fail, so a request with no fixture fails the test instead of reaching the network.
"""

import json
import re
import socket
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from liner_notes import generate as gen
from liner_notes.bank import read_json, write_json
from liner_notes.check import schemas
from liner_notes.ids import slug, song_id

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "musicbrainz"
ID = re.compile(schemas()["song"]["properties"]["id"]["pattern"])


class Answer:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _split(url):
    u = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    q.pop("fmt", None)
    return u.path.removeprefix("/ws/2/"), q


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """No network at all, no waiting, and the MusicBrainz lock file in tmp."""

    def no_socket(*a, **kw):
        raise AssertionError("a test tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("LINER_NOTES_CACHE", str(tmp_path / "cache"))


@pytest.fixture
def recorded(offline, monkeypatch):
    """urlopen answers from the recorded fixtures, matched on path and query."""
    have = {}
    for f in FIXTURES.glob("*.json"):
        d = json.loads(f.read_text())
        have[(d["path"], tuple(sorted(d["query"].items())))] = d["body"]
    asked = []

    def urlopen(req, timeout=None):
        path, q = _split(req.full_url)
        asked.append((path, q))
        k = (path, tuple(sorted(q.items())))
        if k not in have:
            raise AssertionError(f"no recorded MusicBrainz answer for {path} {q}")
        return Answer(json.dumps(have[k]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return asked


def test_got_to_be_real_is_1978_or_unknown_never_a_compilation_year(recorded):
    from liner_notes import resolve

    info = resolve.resolve("Cheryl Lynn", "Got to Be Real")
    assert info.get("year") in (1978, None), info
    assert info.get("year") != 1995


@pytest.mark.parametrize(
    ("artist", "title", "year"), [("George Benson", "Give Me the Night", 1980), ("Earth, Wind & Fire", "Let's Groove", 1981)]
)
def test_more_years_the_reviewer_saw_wrong(recorded, artist, title, year):
    from liner_notes import resolve

    info = resolve.resolve(artist, title)
    assert info["year"] == year and info["year_source"] == "musicbrainz" and info["release_group"]


def test_musicbrainz_retries_are_bounded(offline, monkeypatch):
    from liner_notes import resolve

    calls = []

    def busy(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) > 20:
            raise AssertionError("still retrying after 20 tries")
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", busy)
    with pytest.raises(Exception) as e:
        resolve.mb("recording", query="x")
    assert 2 <= len(calls) <= 5, len(calls)
    assert "503" in str(e.value)


WORKER = textwrap.dedent(
    """
    import sys, time, urllib.request
    sys.path.insert(0, sys.argv[1])
    out = open(sys.argv[2], "w")

    class A:
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=None):
        out.write(repr(time.time()) + "\\n"); out.flush()
        return A()

    urllib.request.urlopen = urlopen
    from liner_notes import resolve
    for _ in range(3):
        resolve.mb("artist", query="x")
    """
)


def test_one_request_a_second_across_processes(tmp_path):
    """Two liner-notes processes share MusicBrainz's one-a-second, not one each."""
    import os

    env = {**os.environ, "LINER_NOTES_CACHE": str(tmp_path / "cache"), "XDG_CACHE_HOME": str(tmp_path / "xdg")}
    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    procs = [subprocess.Popen([sys.executable, str(script), str(ROOT), str(tmp_path / f"t{i}")], env=env) for i in range(2)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    times = sorted(float(x) for i in range(2) for x in (tmp_path / f"t{i}").read_text().split())
    assert len(times) == 6
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert min(gaps) >= 0.99, gaps


TOML = """
[[track]]
path = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
title = "Sailing"
artist = "Christopher Cross"

[[track]]
path = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
title = "Lowdown"
artist = "Boz Scaggs"

[[track]]
path = "https://www.youtube.com/watch?v=CCCCCCCCCCC"
title = "Some Demo Nobody Dated"
artist = "Nobody Famous"

[[track]]
path = "https://www.youtube.com/watch?v=DDDDDDDDDDD"
title = "プラスティック・ラブ"
artist = "竹内まりや"

[[track]]
path = "https://www.youtube.com/watch?v=EEEEEEEEEEE"
title = "真夜中のドア"
artist = "松原みき"
"""

YEARS = {"Sailing": 1979, "Lowdown": 1976, "プラスティック・ラブ": 1984, "真夜中のドア": 1979}


def lookup(artist, title, yt):
    if title not in YEARS:
        return {}
    return {"year": YEARS[title], "year_source": "musicbrainz", "isrc": []}


def setup(tmp_path, monkeypatch, toml=TOML):
    home, data, cliamp = tmp_path / "home", tmp_path / "data", tmp_path / "cliamp"
    (home / "songs").mkdir(parents=True)
    (cliamp / "playlists").mkdir(parents=True)
    (cliamp / "playlists/Night Mix.toml").write_text(toml)
    monkeypatch.setenv("LINER_NOTES_HOME", str(home))
    monkeypatch.setenv("LINER_NOTES_DATA", str(data))
    monkeypatch.setenv("CLIAMP_CONFIG_DIR", str(cliamp))
    return data


def test_an_undated_track_is_written_with_year_null(tmp_path, monkeypatch):
    data = setup(tmp_path, monkeypatch)
    j, _ = gen.generate("Night Mix", lookup=lookup, log=lambda *a: None)
    s = read_json(data / "songs/nobody-famous--some-demo-nobody-dated.json")
    assert s["year"] is None and s.get("year_source") is None
    dated = read_json(data / "songs/christopher-cross--sailing.json")
    assert dated["year"] == 1979 and dated["year_source"] == "musicbrainz"
    # the eras come from the years that were found, and nothing was stretched to fit the undated one
    assert j["eras"][0]["from"] == 1976 and j["eras"][-1]["to"] == 1984


def test_a_japanese_title_gets_a_valid_id(tmp_path, monkeypatch):
    a, b = song_id("竹内まりや", "プラスティック・ラブ"), song_id("松原みき", "真夜中のドア")
    for sid in (a, b):
        assert ID.match(sid), sid
        artist, title = sid.split("--")[:2]
        assert artist and title
    assert a != b
    assert song_id("BTS", "봄날") == "bts--bomnal"
    assert slug("Кино") == "kino"
    assert slug("竹内まりや") == slug("竹内まりや") != slug("松原みき")

    data = setup(tmp_path, monkeypatch)
    gen.generate("Night Mix", lookup=lookup, log=lambda *a: None)
    names = sorted(f.name for f in (data / "songs").glob("*.json"))
    assert "--.json" not in names and len(names) == 5
    assert all(ID.match(n.removesuffix(".json")) for n in names), names


def test_a_japanese_playlist_name_gets_a_journey_slug(tmp_path, monkeypatch):
    data = setup(tmp_path, monkeypatch)
    (tmp_path / "cliamp/playlists/夜のドライブ.toml").write_text(TOML)
    j, _ = gen.generate("夜のドライブ", lookup=lookup, log=lambda *a: None)
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", j["slug"]), j["slug"]
    assert (data / "journeys" / j["slug"] / "journey.json").exists()


def test_a_write_that_breaks_the_schema_is_refused(tmp_path, monkeypatch):
    data = setup(tmp_path, monkeypatch)

    def bad(artist, title, yt):
        return {"year": 1979, "year_source": "musicbrainz", "isrc": ["not an isrc"]}

    with pytest.raises(Exception, match="isrc"):
        gen.generate("Night Mix", lookup=bad, log=lambda *a: None)
    assert not list((data / "songs").glob("*.json")) if (data / "songs").exists() else True
    assert not (data / "journeys").exists()


def test_review_refuses_to_write_a_broken_song(tmp_path, monkeypatch):
    from liner_notes import cli

    home = tmp_path / "home"
    monkeypatch.setenv("LINER_NOTES_HOME", str(home))
    monkeypatch.setenv("LINER_NOTES_DATA", str(tmp_path / "data"))
    f = home / "songs/a--b.json"
    write_json(
        f,
        {
            "id": "a--b",
            "title": "",
            "artist": "A",
            "year": 1980,
            "aliases": {},
            "notes": {},
            "review": {"state": "draft"},
            "provenance": {"ai_generated": False},
        },
    )
    before = f.read_text()
    with pytest.raises((Exception, SystemExit)):
        cli.main(["review", "a--b", "--by", "tester"])
    assert f.read_text() == before
