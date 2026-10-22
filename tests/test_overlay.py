"""Overlays: the tool writes only to user data, a copy of a shipped song records which version it
started from, and doctor reports copies that have gone stale and ids that collide."""

import hashlib
import subprocess

import pytest

from liner_notes import cli, doctor, paths
from liner_notes import generate as gen
from liner_notes.bank import read_json, write_json
from tests.helpers_build import iso, make_bank  # noqa: F401
from tests.test_generate import FakeClient, lookup
from tests.test_generate import setup as gen_setup
from tests.test_play import Fake

SID = "ann-artist--first-song"


@pytest.fixture
def shipped(iso, monkeypatch):
    make_bank(iso["home"])
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    return iso["home"] / "songs" / f"{SID}.json"


def doctor_lines():
    out = []
    doctor.run(Fake(), log=out.append)
    return out


def test_review_writes_an_overlay_and_leaves_the_shipped_file_alone(iso, shipped, capsys):
    before = shipped.read_bytes()
    assert cli.main(["review", SID, "--by", "Ann Reader"]) == 0
    assert shipped.read_bytes() == before
    mine = read_json(iso["data"] / "songs" / f"{SID}.json")
    assert mine["review"]["state"] == "reviewed" and mine["review"]["by"] == "Ann Reader"
    assert mine["base"] == {"version": cli.__version__, "sha256": hashlib.sha256(before).hexdigest()}
    out = capsys.readouterr().out
    assert "your review" in out and "maintainer" in out


def test_a_second_review_keeps_the_first_base(iso, shipped):
    cli.main(["review", SID, "--by", "Ann Reader"])
    base = read_json(iso["data"] / "songs" / f"{SID}.json")["base"]
    cli.main(["review", SID, "--undo"])
    mine = read_json(iso["data"] / "songs" / f"{SID}.json")
    assert mine["base"] == base and mine["review"] == {"state": "draft", "by": None, "date": None}


def test_review_needs_by(iso, shipped, capsys):
    before = shipped.read_bytes()
    with pytest.raises(SystemExit) as e:
        cli.main(["review", SID])
    assert "--by" in str(e.value.code)
    assert shipped.read_bytes() == before and not (iso["data"] / "songs").exists()


def test_doctor_flags_a_stale_overlay(iso, shipped):
    cli.main(["review", SID, "--by", "Ann Reader"])
    assert not [x for x in doctor_lines() if SID in x]
    s = read_json(shipped)
    s["notes"]["made"] = "Made in a different room, says the corrected sleeve."
    write_json(shipped, s)
    hits = [x for x in doctor_lines() if SID in x]
    assert hits and hits[0].startswith("warn") and "stale" in hits[0] and "changed since" in hits[0]


def test_doctor_flags_an_id_collision(iso, shipped):
    write_json(iso["data"] / "songs" / f"{SID}.json", {**read_json(shipped), "title": "My Own Song"})
    hits = [x for x in doctor_lines() if SID in x]
    assert hits and hits[0].startswith("warn") and "collision" in hits[0]


def test_doctor_is_quiet_about_new_songs_of_your_own(iso, shipped):
    write_json(iso["data"] / "songs/zed--mine.json", {"id": "zed--mine", "title": "Mine", "artist": "Zed", "year": 2000})
    assert not any("zed--mine" in x for x in doctor_lines())


def test_marking_the_shipped_file_needs_a_checkout(iso, shipped):
    before = shipped.read_bytes()
    with pytest.raises(SystemExit) as e:
        cli.main(["review", SID, "--by", "Maintainer", "--shipped"])
    assert "checkout" in str(e.value.code) and shipped.read_bytes() == before
    subprocess.run(["git", "init", "-q", str(iso["home"])], check=True)
    assert cli.main(["review", SID, "--by", "Maintainer", "--shipped"]) == 0
    assert read_json(shipped)["review"]["by"] == "Maintainer" and "base" not in read_json(shipped)
    assert not (iso["data"] / "songs").exists()


def test_nothing_writes_into_an_installed_package(monkeypatch):
    monkeypatch.delenv("LINER_NOTES_HOME", raising=False)
    monkeypatch.setattr(paths, "checkout", lambda: None)
    target = paths.PACKAGE / "shipped" / "songs" / "zz-overlay-test--never.json"
    try:
        with pytest.raises(PermissionError):
            write_json(target, {"id": "zz-overlay-test--never"})
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


def test_generate_stamps_the_base_on_a_shipped_song_it_copies(tmp_path, monkeypatch):
    home, data = gen_setup(tmp_path, monkeypatch)
    f = home / "songs/massive-attack--protection.json"
    s = read_json(f)
    s["notes"] = {}
    write_json(f, s)
    gen.generate("Test Mix", write=True, budget=5, client=FakeClient(), lookup=lookup, log=lambda *a: None)
    mine = read_json(data / "songs/massive-attack--protection.json")
    assert mine["notes"]["made"] and mine["base"]["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
