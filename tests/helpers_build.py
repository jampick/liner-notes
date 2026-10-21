"""Shared fixtures for the build, migration and CLI tests: every XDG location in tmp."""

import pytest


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Point every place Liner Notes reads or writes at tmp, the pilot fallbacks included."""
    d = {name: tmp_path / name for name in ("data", "cache", "state", "dist", "pilot", "pilot_cache", "pilot_state", "cliamp", "home")}
    for p in d.values():
        p.mkdir()
    monkeypatch.setenv("LINER_NOTES_DATA", str(d["data"]))
    monkeypatch.setenv("LINER_NOTES_CACHE", str(d["cache"]))
    monkeypatch.setenv("LINER_NOTES_STATE", str(d["state"]))
    monkeypatch.setenv("LINER_NOTES_DIST", str(d["dist"]))
    monkeypatch.setenv("LINER_NOTES_PILOT", str(d["pilot"]))
    monkeypatch.setenv("LINER_NOTES_PILOT_CACHE", str(d["pilot_cache"]))
    monkeypatch.setenv("LINER_NOTES_PILOT_STATE", str(d["pilot_state"]))
    monkeypatch.setenv("LINER_NOTES_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("CLIAMP_CONFIG_DIR", str(d["cliamp"]))
    monkeypatch.setenv("LINER_NOTES_SYSTEMD_DIR", str(tmp_path / "systemd"))
    monkeypatch.delenv("LINER_NOTES_OFFLINE", raising=False)
    monkeypatch.delenv("LINER_NOTES_WORKSPACE", raising=False)
    return d


TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="liner-token" content="">
<title>__TITLE__</title>
<meta name="description" content="__DESCRIPTION__">
<link rel="stylesheet" href="__FONTS__">
</head><body><script>window.JOURNEY = /*__DATA__*/null;</script></body></html>
"""


def song(sid, title, artist, year, yt, made="Made in a room.", culture=None):
    return {
        "id": sid,
        "title": title,
        "artist": artist,
        "year": year,
        "year_note": "",
        "album": "",
        "aliases": {"youtube": [yt]},
        "notes": {
            "made": made,
            "context": "It charted.",
            "listen_for": [],
            "leads_to": {},
            "sources": ["https://example.org/a"],
            "unverified": "",
        },
        "culture": culture or [],
        "review": {"state": "draft", "by": None, "date": None},
        "provenance": {"ai_generated": True, "model": "test", "written_by": "test", "written_on": "2026-10-20"},
    }


def make_bank(root, slug="test-trip", title="Test Trip"):
    """A two-song journey with one era, written under root."""
    from liner_notes.bank import write_json

    write_json(
        root / "songs/ann-artist--first-song.json",
        song(
            "ann-artist--first-song",
            "First Song",
            "Ann Artist",
            1990,
            "AAAAAAAAAAA",
            culture=[{"kind": "film", "title": "A Film", "line": "It opened.", "wiki": "A Film"}],
        ),
    )
    write_json(root / "songs/bob-band--second-song.json", song("bob-band--second-song", "Second Song", "Bob Band", 1991, "BBBBBBBBBBB"))
    write_json(
        root / "culture/years/1990.json",
        {"year": 1990, "items": [{"kind": "news", "title": "News", "line": "Things happened.", "wiki": "News Story"}]},
    )
    write_json(root / "culture/artists.json", {"Ann Artist": "Ann Artist (singer)"})
    jdir = root / "journeys" / slug
    (jdir / "media/eras").mkdir(parents=True)
    (jdir / "media/eras/era1.webp").write_bytes(b"RIFF0000WEBPVP8 ")
    write_json(
        jdir / "journey.json",
        {
            "slug": slug,
            "title": title,
            "titleA": "Test",
            "titleB": "Trip",
            "kicker": "2 songs",
            "description": "Two songs.",
            "lede": "",
            "fonts": "https://fonts.googleapis.com/css2?family=Spectral",
            "copy": {"credits": ""},
            "panel": {},
            "eras": [
                {
                    "n": 1,
                    "name": "One",
                    "years": "1990-91",
                    "from": 1990,
                    "to": 1991,
                    "essay": "An essay.",
                    "scene": {},
                    "image": "media/eras/era1.webp",
                }
            ],
            "songs": ["ann-artist--first-song", "bob-band--second-song"],
            "pins": {},
            "threads": [],
            "review": {"state": "draft", "by": None, "date": None},
        },
    )
    return jdir
