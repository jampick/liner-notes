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


def test_got_to_be_real_is_1978_never_a_compilation_year(recorded):
    from liner_notes import resolve

    info = resolve.resolve("Cheryl Lynn", "Got to Be Real")
    assert info.get("year") == 1978, info


@pytest.mark.parametrize(
    ("artist", "title", "year"),
    [
        # main said 1998 and 1982: its top 15 recordings were compilations and a later single
        ("George Benson", "Give Me the Night", 1980),
        ("Earth, Wind & Fire", "Let's Groove", 1981),
        # an earlier album shares the song's name; the song came out on a later album
        ("Queen", "Sheer Heart Attack", 1977),
        ("Led Zeppelin", "Houses of the Holy", 1975),
    ],
)
def test_more_years_the_reviewers_saw_wrong(recorded, artist, title, year):
    from liner_notes import resolve

    info = resolve.resolve(artist, title)
    assert info["year"] == year and info["year_source"] == "musicbrainz" and info["release_group"], info


def _rec(rid, year, kind="Album", secondary=(), title="Lost Song"):
    rg = {"id": f"00000000-0000-0000-0000-{rid:012d}", "title": "x", "primary-type": kind, "secondary-types": list(secondary)}
    rel = {"id": rg["id"], "title": "x", "status": "Official", "date": f"{year}-01-01", "release-group": rg}
    return {
        "id": f"10000000-0000-0000-0000-{rid:012d}",
        "title": title,
        "artist-credit": [{"name": "Some Band", "joinphrase": ""}],
        "releases": [rel],
    }


def test_the_recording_search_reads_past_its_first_page(offline, monkeypatch):
    """A famous song's first 100 recordings can all be compilations; the original is on page two."""
    from liner_notes import resolve

    first = [_rec(i, 1990 + i % 30, secondary=["Compilation"]) for i in range(100)]
    second = [_rec(100, 1979, kind="Single")] + [_rec(100 + i, 2000, secondary=["Compilation"]) for i in range(1, 30)]

    def urlopen(req, timeout=None):
        path, q = _split(req.full_url)
        if path == "release-group":
            body = {"count": 0, "release-groups": []}
        elif path == "recording" and q.get("offset", "0") == "0":
            body = {"count": 130, "offset": 0, "recordings": first}
        elif path == "recording" and q.get("offset") == "100":
            body = {"count": 130, "offset": 100, "recordings": second}
        else:
            body = {"id": "x", "isrcs": []}
        return Answer(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert resolve.resolve("Some Band", "Lost Song")["year"] == 1979


def test_musicbrainz_retries_are_bounded(offline, monkeypatch):
    from liner_notes import resolve

    calls = []

    def busy(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) > 20:
            raise AssertionError("still retrying after 20 tries")
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", busy)
    from liner_notes.musicbrainz import MusicBrainzError

    with pytest.raises(MusicBrainzError) as e:
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
    # stamped after the lock is let go, so a busy machine can shave a little off a gap
    assert min(gaps) >= 0.9, gaps


BACKOFF_WORKER = textwrap.dedent(
    """
    import sys, time, urllib.error, urllib.request
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    role, out, other = sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    calls = []

    class A:
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def urlopen(req, timeout=None):
        calls.append(time.time())
        with out.open("a") as f:
            f.write(repr(time.time()) + "\\n")
        if role == "a" and len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {"Retry-After": "3"}, None)
        return A()

    urllib.request.urlopen = urlopen
    if role == "b":
        while not other.exists():  # start right after a's first request got its 503
            time.sleep(0.02)
        time.sleep(0.2)
    from liner_notes import resolve
    resolve.mb("artist", query="x")
    """
)


def test_a_retry_after_holds_back_every_process(tmp_path):
    """One process told to wait 3 s by MusicBrainz makes the others wait too."""
    import os

    env = {**os.environ, "LINER_NOTES_CACHE": str(tmp_path / "cache"), "XDG_CACHE_HOME": str(tmp_path / "xdg")}
    script = tmp_path / "worker.py"
    script.write_text(BACKOFF_WORKER)
    a, b = tmp_path / "a", tmp_path / "b"
    procs = [
        subprocess.Popen([sys.executable, str(script), str(ROOT), r, str(o), str(x)], env=env) for r, o, x in (("a", a, b), ("b", b, a))
    ]
    for p in procs:
        assert p.wait(timeout=60) == 0
    told = float(a.read_text().split()[0])
    asked = float(b.read_text().split()[0])
    assert asked - told >= 2.8, asked - told


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


def test_a_hashed_journey_slug_is_announced_and_can_be_chosen(tmp_path, monkeypatch):
    data = setup(tmp_path, monkeypatch)
    (tmp_path / "cliamp/playlists/城市流行.toml").write_text(TOML)
    said = []
    j, _ = gen.generate("城市流行", lookup=lookup, log=lambda *a: said.append(" ".join(map(str, a))))
    assert j["slug"].startswith("x")
    assert any(j["slug"] in x and "--slug" in x and "城市流行" in x for x in said), said
    j, _ = gen.generate("城市流行", lookup=lookup, log=lambda *a: None, slug="city-pop")
    assert j["slug"] == "city-pop" and (data / "journeys/city-pop/journey.json").exists()


def test_the_matcher_tells_non_latin_songs_apart():
    from liner_notes.ids import key
    from liner_notes.match import Matcher

    assert key("竹内まりや") and key("竹内まりや") != key("松原みき")
    song = {"id": "xb8852d99--purasutikku-rabu", "title": "プラスティック・ラブ", "artist": "竹内まりや", "aliases": {}}
    m = Matcher({song["id"]: song})
    assert m.match({"title": "プラスティック・ラブ", "artist": "竹内まりや"}) == song["id"]
    assert m.match({"title": "真夜中のドア", "artist": "松原みき"}) is None
    assert m.match({"title": "夜に駆ける", "artist": "ヨアソビ"}) is None


def test_undated_songs_are_left_off_the_page_with_a_warning_not_a_refusal(tmp_path, monkeypatch):
    from liner_notes.bank import Bank
    from liner_notes.build import build

    data = setup(tmp_path, monkeypatch)
    gen.generate("Night Mix", lookup=lookup, log=lambda *a: None)
    said = []
    out, problems = build("night-mix", out=tmp_path / "page.html", public=True, log=lambda *a: said.append(" ".join(map(str, a))))
    assert problems == [] and out.exists()
    f = Bank().song_files["nobody-famous--some-demo-nobody-dated"]
    line = [x for x in said if "Some Demo Nobody Dated" in x]
    assert len(line) == 1, said
    assert "isn't on this page" in line[0] and str(f) in line[0]
    assert '"year": <year>' in line[0] and '"year_source": "person"' in line[0]
    assert "Some Demo Nobody Dated" not in out.read_text()
    assert str(data) in str(f)


def test_the_writers_year_wins_and_check_names_both(tmp_path, monkeypatch):
    from liner_notes import check
    from tests.test_generate import FakeClient

    data = setup(tmp_path, monkeypatch, toml=TOML.split("[[track]]", 2)[0] + "[[track]]" + TOML.split("[[track]]", 2)[1])
    gen.generate(
        "Night Mix",
        write=True,
        budget=5,
        client=FakeClient(),
        lookup=lambda a, t, y: {"year": 1980, "year_source": "musicbrainz", "isrc": []},
        log=lambda *a: None,
    )
    s = read_json(data / "songs/christopher-cross--sailing.json")
    assert s["year"] == 1979 and s["year_source"] == "writer" and s["year_musicbrainz"] == 1980  # FakeClient's writer says 1979
    said = []
    check.check(log=lambda *a: said.append(" ".join(map(str, a))))
    warn = [x for x in said if "Sailing" in x and "1979" in x and "1980" in x]
    assert warn and "warning" in warn[0], said
    # a warning, not a problem: the only problem is the test home having no shipped bank
    probs = [x for x in said if x.startswith("problem:")]
    assert len(probs) == 1 and "shipped bank isn't installed" in probs[0], probs


def test_year_source_without_a_year_says_how_to_fix_it():
    from liner_notes.check import year_problems

    (msg,) = year_problems({"year": None, "year_source": "musicbrainz"})
    assert "set a year or set year_source to null" in msg


def test_a_musicbrainz_outage_ends_generate_cleanly(tmp_path, monkeypatch):
    from liner_notes import cli, resolve
    from liner_notes.musicbrainz import MusicBrainzError

    data = setup(tmp_path, monkeypatch)

    def down(artist, title, yt=None):
        raise MusicBrainzError("MusicBrainz didn't answer recording after 5 tries (HTTP 503)")

    monkeypatch.setattr(resolve, "resolve", down)
    with pytest.raises(SystemExit) as e:
        cli.main(["generate", "--from", "Night Mix"])
    assert isinstance(e.value.code, str) and "MusicBrainz" in e.value.code and "nothing was written" in e.value.code
    assert not (data / "songs").exists() and not (data / "journeys").exists()


def test_a_write_that_breaks_the_schema_is_refused(tmp_path, monkeypatch):
    data = setup(tmp_path, monkeypatch)

    def bad(artist, title, yt):
        return {"year": 1979, "year_source": "musicbrainz", "isrc": ["not an isrc"]}

    with pytest.raises(Exception, match="isrc"):
        gen.generate("Night Mix", lookup=bad, log=lambda *a: None)
    assert not list((data / "songs").glob("*.json")) if (data / "songs").exists() else True
    assert not (data / "journeys").exists()


def test_generates_schema_refusal_is_one_problem_a_line_and_owned_as_a_bug(tmp_path, monkeypatch):
    from liner_notes import cli, resolve

    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        resolve, "resolve", lambda artist, title, yt=None: {"year": 1979, "year_source": "musicbrainz", "isrc": ["not an isrc"]}
    )
    with pytest.raises(SystemExit) as e:
        cli.main(["generate", "--from", "Night Mix"])
    msg = e.value.code
    assert isinstance(msg, str) and "bug" in msg and "nothing written" in msg
    assert len([x for x in msg.splitlines() if "isrc" in x]) == 5, msg


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
    with pytest.raises(SystemExit) as e:
        cli.main(["review", "a--b", "--by", "tester"])
    assert f.read_text() == before
    # the review would go to your copy in user data; a refused write leaves no copy behind
    copy = tmp_path / "data/songs/a--b.json"
    assert not copy.exists()
    assert str(copy) in str(e.value.code) and "title" in str(e.value.code)


def test_a_bad_slug_is_the_users_to_fix_not_a_bug(tmp_path, monkeypatch):
    from liner_notes import cli

    data = setup(tmp_path, monkeypatch)
    monkeypatch.setattr("liner_notes.resolve.resolve", lambda artist, title, yt=None: lookup(artist, title, yt))
    with pytest.raises(SystemExit) as e:
        cli.main(["generate", "--from", "Night Mix", "--slug", "City Pop"])
    assert "--slug" in str(e.value.code) and "bug" not in str(e.value.code)
    assert not (data / "journeys").exists()


def test_a_copys_base_is_checked_against_the_schema():
    from liner_notes.bank import problems

    song = {"id": "a--b", "title": "B", "artist": "A", "year": 1980, "aliases": {}, "notes": {}, "review": {"state": "draft"}}
    song["provenance"] = {"ai_generated": False}
    good = {"version": "0.3.0.dev0", "sha256": "a" * 64, "content": "b" * 64}
    assert problems({**song, "base": good}, "song") == []
    assert problems({**song, "base": {k: v for k, v in good.items() if k != "content"}}, "song") == []  # copies from before content
    bad = problems({**song, "base": {**good, "sha256": "nope"}}, "song")
    assert any("base.sha256" in x for x in bad), bad
    assert any("base" in x for x in problems({**song, "base": {"sha256": "a" * 64}}, "song"))  # no version


def test_a_year_a_person_set_beats_the_writer(tmp_path, monkeypatch):
    """Trust order: person, then writer, then MusicBrainz."""
    from liner_notes import check
    from tests.test_generate import FakeClient

    data = setup(tmp_path, monkeypatch, toml=TOML.split("[[track]]", 2)[0] + "[[track]]" + TOML.split("[[track]]", 2)[1])
    gen.generate("Night Mix", lookup=lookup, log=lambda *a: None)
    f = data / "songs/christopher-cross--sailing.json"
    write_json(f, {**read_json(f), "year": 1981, "year_source": "person"})
    gen.generate("Night Mix", write=True, budget=5, client=FakeClient(), lookup=lookup, log=lambda *a: None)
    s = read_json(f)
    assert s["year"] == 1981 and s["year_source"] == "person" and s["year_writer"] == 1979  # FakeClient's writer says 1979
    said = []
    check.check(log=lambda *a: said.append(" ".join(map(str, a))))
    warn = [x for x in said if x.startswith("warning:") and "1981" in x and "1979" in x]
    assert warn and str(f) in warn[0], said


def test_generate_shows_which_release_each_year_came_from(tmp_path, monkeypatch, recorded):
    from liner_notes import resolve

    info = resolve.resolve("Queen", "Sheer Heart Attack")
    assert info["release_title"] == "News of the World" and info["release_kind"] == "album"
    setup(tmp_path, monkeypatch)
    said = []

    def found(artist, title, yt):
        return {**lookup(artist, title, yt), "release_title": "Japanese Sunday Soul - Volume 1", "release_kind": "album"}

    gen.generate("Night Mix", lookup=found, log=lambda *a: said.append(" ".join(map(str, a))))
    assert any('1984, from "Japanese Sunday Soul - Volume 1" (album)' in x for x in said), said


def test_a_hand_set_year_outside_the_eras_says_how_to_recut_them(tmp_path, monkeypatch):
    from liner_notes.build import compose

    data = setup(tmp_path, monkeypatch)
    gen.generate("Night Mix", lookup=lookup, log=lambda *a: None)
    f = data / "songs/nobody-famous--some-demo-nobody-dated.json"
    write_json(f, {**read_json(f), "year": 1962, "year_source": "person"})
    _, problems, _ = compose("night-mix", public=True)
    (p,) = [x for x in problems if "Some Demo" in x]
    assert "1962" in p and "liner-notes generate" in p and "re-cut" in p, p


def test_build_all_skips_a_remix_journey_this_machine_has_no_files_for(tmp_path, monkeypatch):
    import shutil

    from liner_notes import build as B
    from liner_notes import paths
    from liner_notes.bank import Bank

    home = tmp_path / "home"
    shutil.copytree(paths.PACKAGE / "shipped/journeys/yacht-house", home / "journeys/yacht-house")
    monkeypatch.setenv("LINER_NOTES_HOME", str(home))
    monkeypatch.setenv("LINER_NOTES_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("LINER_NOTES_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("LINER_NOTES_PILOT", raising=False)
    monkeypatch.setattr(B, "build_live", lambda **kw: None)
    said = []
    refused = B.build_all(bank=Bank(), log=lambda *a: said.append(" ".join(map(str, a))))
    assert refused == 0, said
    (line,) = [x for x in said if "yacht-house" in x]
    assert "skipped" in line and "migrate-personal" not in line, line
    assert "yacht-house" in Bank().journeys  # still listed


def test_check_names_the_whole_path_and_an_old_year_0_is_undated(tmp_path, monkeypatch):
    from liner_notes import check
    from liner_notes.build import compose

    data = setup(tmp_path, monkeypatch)
    gen.generate("Night Mix", lookup=lookup, log=lambda *a: None)
    f = data / "songs/christopher-cross--sailing.json"
    write_json(f, {**read_json(f), "year": 0})  # what 0.2.0's generate wrote for an undated song
    bad = data / "songs/a--b.json"
    write_json(bad, {"id": "a--b", "title": "", "artist": "A", "year": 1980, "aliases": {}, "notes": {}, "review": {"state": "draft"}})
    bad_doc = read_json(bad)
    bad_doc["provenance"] = {"ai_generated": False}
    write_json(bad, bad_doc)
    said = []
    check.check(log=lambda *a: said.append(" ".join(map(str, a))))
    probs = [x for x in said if x.startswith("problem:")]
    assert any(str(bad) in x and "title" in x for x in probs), probs
    assert not any("sailing" in x for x in probs), probs
    _, problems, _ = compose("night-mix", public=True, warnings=(w := []))
    assert not any("Sailing" in x for x in problems) and any("Sailing" in x and "isn't on this page" in x for x in w)


def test_one_era_is_one_era(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, toml=TOML.split("[[track]]", 2)[0] + "[[track]]" + TOML.split("[[track]]", 2)[1])
    said = []
    gen.generate("Night Mix", lookup=lookup, log=lambda *a: said.append(" ".join(map(str, a))))
    assert any("1 era," in x for x in said) and not any("1 eras" in x for x in said), said


@pytest.mark.parametrize("trouble", ["not json", "cut short"])
def test_a_garbled_musicbrainz_answer_is_a_musicbrainz_error(offline, monkeypatch, trouble):
    import http.client

    from liner_notes import resolve
    from liner_notes.musicbrainz import MusicBrainzError

    class Bad(Answer):
        def read(self):
            if trouble == "cut short":
                raise http.client.IncompleteRead(b"{", 100)
            return b"<html>busy</html>"

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Bad(b""))
    with pytest.raises(MusicBrainzError) as e:
        resolve.mb("recording", query="x")
    assert "MusicBrainz" in str(e.value)


def _song(sid="a--b", **kw):
    return {
        "id": sid,
        "title": "B",
        "artist": "A",
        "year": 1980,
        "aliases": {},
        "notes": {},
        "review": {"state": "draft"},
        "provenance": {"ai_generated": False},
        **kw,
    }


def test_write_checked_refuses_inside_an_installed_package(tmp_path, monkeypatch):
    from liner_notes import paths
    from liner_notes.bank import write_checked

    pkg = tmp_path / "site-packages/liner_notes"
    (pkg / "shipped/songs").mkdir(parents=True)
    monkeypatch.setattr(paths, "PACKAGE", pkg)
    with pytest.raises(PermissionError):
        write_checked(pkg / "shipped/songs/a--b.json", _song(), "song")
    assert not (pkg / "shipped/songs/a--b.json").exists()


def test_write_checked_refuses_a_bad_document_and_leaves_the_old_file(tmp_path):
    from liner_notes.bank import SchemaError, write_checked

    f = tmp_path / "songs/a--b.json"
    write_checked(f, _song(), "song")
    before = f.read_bytes()
    for doc, name in ((_song(title=""), "a--b"), (_song(), "c--d"), (_song(year=1700), "a--b")):
        with pytest.raises(SchemaError):
            write_checked(tmp_path / f"songs/{name}.json", doc, "song")
    assert f.read_bytes() == before and not (tmp_path / "songs/c--d.json").exists()
    with pytest.raises(SchemaError):
        write_checked(tmp_path / "journeys/x/journey.json", {"slug": "Not A Slug", "title": "x"}, "journey")
    assert not (tmp_path / "journeys").exists()


def test_write_checked_is_atomic(tmp_path, monkeypatch):
    import os

    from liner_notes.bank import write_checked

    f = tmp_path / "songs/a--b.json"
    write_checked(f, _song(), "song")
    before = f.read_bytes()

    def boom(*a):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_checked(f, _song(title="New"), "song")
    assert f.read_bytes() == before
    assert sorted(x.name for x in f.parent.iterdir()) == ["a--b.json"]  # no temp file left
