import json
import os
import re

import pytest

from liner_notes import build as B
from liner_notes import paths, stage
from liner_notes.bank import Bank, read_json, write_json
from tests.helpers_build import TEMPLATE, iso, make_bank  # noqa: F401


@pytest.fixture
def tpl(tmp_path):
    p = tmp_path / "template.html"
    p.write_text(TEMPLATE)
    return p


def blob(html):
    m = re.search(r"window\.JOURNEY = (.*?);</script>", html, re.S)
    return json.loads(m.group(1))


def test_personal_build_is_atomic_and_writes_stage(iso, tpl):
    root = iso["home"]
    make_bank(root)
    (iso["cache"] / "covers").mkdir()
    (iso["cache"] / "covers/ann-artist--first-song.webp").write_bytes(b"cover")
    out, problems = B.build("test-trip", bank=Bank([root]), template=tpl, log=lambda *a: None)
    assert out == iso["dist"] / "test-trip.html" and not problems
    assert not [p for p in out.parent.iterdir() if p.name.endswith(".tmp")]
    d = blob(out.read_text())
    assert d["stops"][0]["cover"].startswith("data:" + "image/webp") and d["stops"][0]["yt"] == "AAAAAAAAAAA"
    assert (iso["cache"] / "stage/test-trip.stage.json").is_file()


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    f = tmp_path / "page.html"
    f.write_text("old")

    def boom(*a):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        paths.atomic_write(f, "new")
    assert f.read_text() == "old" and [p.name for p in tmp_path.iterdir()] == ["page.html"]


def test_refuses_with_problems_unless_forced(iso, tpl):
    root = iso["home"]
    jdir = make_bank(root)
    j = read_json(jdir / "journey.json")
    j["songs"].append("nobody--nothing")
    write_json(jdir / "journey.json", j)
    out = iso["dist"] / "test-trip.html"
    out.write_text("the page being served")
    with pytest.raises(B.Refused):
        B.build("test-trip", bank=Bank([root]), template=tpl, log=lambda *a: None)
    assert out.read_text() == "the page being served"
    _, problems = B.build("test-trip", bank=Bank([root]), template=tpl, force=True, log=lambda *a: None)
    assert problems and "the page being served" not in out.read_text()


def test_missing_cover_is_not_a_problem(iso, tpl):
    make_bank(iso["home"])
    _, problems = B.build("test-trip", bank=Bank([iso["home"]]), template=tpl, log=lambda *a: None)
    assert problems == []


def test_escaping(iso, tpl):
    root = iso["home"]
    jdir = make_bank(root)
    j = read_json(jdir / "journey.json")
    j["title"] = 'Rock & "Roll" </title><script>alert(1)</script>'
    j["description"] = '<b>bold</b> "quoted"'
    write_json(jdir / "journey.json", j)
    s = read_json(root / "songs/ann-artist--first-song.json")
    s["notes"]["made"] = "Ends the script early </script><!-- and opens a comment <SCRIPT>"
    write_json(root / "songs/ann-artist--first-song.json", s)
    out, _ = B.build("test-trip", bank=Bank([root]), template=tpl, log=lambda *a: None)
    html = out.read_text()
    assert "<title>Rock &amp; &quot;Roll&quot; &lt;/title&gt;&lt;script&gt;" in html
    assert 'content="&lt;b&gt;bold&lt;/b&gt; &quot;quoted&quot;"' in html
    body = html.split("window.JOURNEY = ", 1)[1]
    assert body.count("</script>") == 1 and "<!--" not in html and "<SCRIPT>" not in html
    assert blob(html)["stops"][0]["made"].startswith("Ends the script early </script><!--")
    assert "alert(1)</script>" not in html


def test_public_build_drops_personal(iso, tpl):
    root = iso["home"]
    make_bank(root)
    (iso["cache"] / "covers").mkdir()
    (iso["cache"] / "covers/ann-artist--first-song.webp").write_bytes(b"\xff\xd8\xff cover")
    out, _ = B.build("test-trip", public=True, bank=Bank([root]), template=tpl, log=lambda *a: None)
    assert out.name == "test-trip.public.html"
    html = out.read_text()
    d = blob(html)
    assert all(s["cover"].startswith("data:image/svg+xml") for s in d["stops"])
    assert all(s["yt"] == "" and s["youtube"] == "" for s in d["stops"]) and d["video"] is False
    assert "AAAAAAAAAAA" not in html and "BBBBBBBBBBB" not in html
    assert not (iso["cache"] / "stage/test-trip.stage.json").exists()


def test_live_page(iso, tpl):
    out = B.build_live(template=tpl, log=lambda *a: None)
    assert out == iso["dist"] / "live.html"
    d = blob(out.read_text())
    assert d["slug"] == "live" and d["stops"] == [] and d["eras"] == [] and d["live"] is True


def test_shipped_template_builds(iso):
    if not B.TEMPLATE.exists():
        pytest.skip("no template")
    make_bank(iso["home"])
    out, _ = B.build("test-trip", bank=Bank([iso["home"]]), log=lambda *a: None)
    assert blob(out.read_text())["slug"] == "test-trip"


def test_stage_data_from_disk_only(iso):
    root = iso["home"]
    make_bank(root)
    c = iso["cache"]
    for sub, name in (
        ("video", "AAAAAAAAAAA.mp4"),
        ("video", "SSSSSSSSSSS.mp4"),
        ("culture", "A_Film.webp"),
        ("artists", "Ann_Artist__singer_.webp"),
    ):
        (c / sub).mkdir(exist_ok=True)
        (c / sub / name).write_bytes(b"x")
    write_json(
        c / "credits.json",
        {"culture/A_Film.webp": {"license": "non-free"}, "artists/Ann_Artist__singer_.webp": {"license": "CC BY-SA 4.0"}},
    )
    write_json(
        iso["data"] / "standins/test-trip.json", {"BBBBBBBBBBB": {"standin": "SSSSSSSSSSS", "standin_offset": 1.5, "standin_rate": 1.01}}
    )
    d = stage.compose("test-trip", Bank([root]), c)
    assert d["tracks"]["AAAAAAAAAAA"]["motion"] is True
    b = d["tracks"]["BBBBBBBBBBB"]
    assert b["motion"] is False and b["standin"] == "SSSSSSSSSSS" and b["standin_offset"] == 1.5 and b["standin_rate"] == 1.01
    assert d["songs"]["First Song"][0]["img"] == "culture/A_Film.webp"
    assert "img" not in d["years"]["1990"][0]
    assert d["artists"] == {"Ann Artist": "artists/Ann_Artist__singer_.webp"}
    assert not any("yt" in it for v in [*d["years"].values(), *d["songs"].values()] for it in v)
    pub = stage.compose("test-trip", Bank([root]), c, public=True)
    assert "img" not in pub["songs"]["First Song"][0] and pub["artists"] and not pub["tracks"]["BBBBBBBBBBB"].get("standin")


def test_stage_reads_pilot_standins(iso):
    make_bank(iso["home"])
    (iso["cache"] / "video").mkdir()
    (iso["cache"] / "video/SSSSSSSSSSS.mp4").write_bytes(b"x")
    write_json(iso["pilot"] / "playlists/test-trip/standins.json", {"AAAAAAAAAAA": {"standin": "SSSSSSSSSSS", "standin_offset": 0}})
    d = stage.compose("test-trip", Bank([iso["home"]]), iso["cache"])
    assert d["tracks"]["AAAAAAAAAAA"]["standin"] == "SSSSSSSSSSS"


def test_build_all_skips_refused(iso, tpl, monkeypatch):
    make_bank(iso["home"])
    make_bank(iso["home"], slug="other-trip", title="Other")
    j = read_json(iso["home"] / "journeys/other-trip/journey.json")
    j["songs"].append("missing--song")
    write_json(iso["home"] / "journeys/other-trip/journey.json", j)
    monkeypatch.setattr(B, "TEMPLATE", tpl)
    assert B.build_all(bank=Bank([iso["home"]]), log=lambda *a: None) == 1
    assert (iso["dist"] / "test-trip.html").exists() and (iso["dist"] / "live.html").exists()
    assert not (iso["dist"] / "other-trip.html").exists()


def test_media_never_asks_youtube(iso, monkeypatch):
    from liner_notes import media

    make_bank(iso["home"])
    s = read_json(iso["home"] / "songs/ann-artist--first-song.json")
    s["aliases"]["apple"] = {"url": "https://music.apple.com/us/album/x/1?i=123"}
    write_json(iso["home"] / "songs/ann-artist--first-song.json", s)
    asked = []

    def get(self, url, tries=5):
        asked.append(url)
        raise OSError("offline")

    monkeypatch.setattr(media.Fetcher, "get", get)
    out = media.stage("test-trip", bank=Bank([iso["home"]]), cache=iso["cache"], log=lambda *a: None)
    assert out == iso["cache"] / "stage/test-trip.stage.json" and out.is_file()
    assert asked and not any("youtube" in u or "ytimg" in u or "googlevideo" in u for u in asked)


def test_placeholders_in_one_pass(iso, tpl):
    root = iso["home"]
    jdir = make_bank(root)
    j = read_json(jdir / "journey.json")
    j["title"] = "Mix __DESCRIPTION__ and __FONTS__ and /*__DATA__*/null"
    write_json(jdir / "journey.json", j)
    out, _ = B.build("test-trip", bank=Bank([root]), template=tpl, log=lambda *a: None)
    html = out.read_text()
    assert "<title>Mix __DESCRIPTION__ and __FONTS__ and /*__DATA__*/null · Liner Notes</title>" in html
    assert blob(html)["title"] == j["title"]


def test_fonts_only_in_public_pages(iso, tpl):
    make_bank(iso["home"])
    bank = Bank([iso["home"]])
    personal, _ = B.build("test-trip", bank=bank, template=tpl, log=lambda *a: None)
    assert "stylesheet" not in personal.read_text() and "fonts.googleapis" not in personal.read_text()
    public, _ = B.build("test-trip", public=True, bank=bank, template=tpl, log=lambda *a: None)
    assert '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral">' in public.read_text()
    live = B.build_live(template=tpl, log=lambda *a: None).read_text()
    assert 'href=""' not in live and "stylesheet" not in live


def test_no_pilot_unless_named(iso, monkeypatch):
    """With the pilot variables unset nothing is read from a pilot, and nothing breaks."""
    from liner_notes.bridge import lyrics, media, route

    for k in ("LINER_NOTES_PILOT", "LINER_NOTES_PILOT_CACHE", "LINER_NOTES_PILOT_STATE"):
        monkeypatch.delenv(k, raising=False)
    assert paths.pilot() is None and paths.pilot_cache() is None and paths.pilot_state() is None
    assert paths.lyric_file("AAAAAAAAAAA") is None and paths.video_file("AAAAAAAAAAA") is None and paths.audio_file("AAAAAAAAAAA") is None
    assert lyrics.retimed_dirs() == [paths.user_data() / "lyrics"]
    assert media.video_dirs() == [paths.cache() / "video"]
    assert route.tables() == [paths.state() / "av-latency.json"]
    make_bank(iso["home"])
    assert stage.standins("test-trip") == {}
