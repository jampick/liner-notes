"""Overlays: the tool writes only to user data, a copy of a shipped song records which version it
started from, and doctor reports copies that have gone stale and ids that collide."""

import hashlib
import subprocess

import pytest

from liner_notes import cli, doctor, overlay, paths
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


def tilde(p):
    h = str(paths.Path.home())
    return str(p).replace(h + "/", "~/", 1)


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
    assert mine["base"]["version"] == cli.__version__ and mine["base"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert mine["base"]["content"] == overlay.content_hash(read_json(shipped))
    out = capsys.readouterr().out
    assert "your review" in out and "maintainer" in out


def test_a_second_review_keeps_the_first_base(iso, shipped):
    cli.main(["review", SID, "--by", "Ann Reader"])
    base = read_json(iso["data"] / "songs" / f"{SID}.json")["base"]
    cli.main(["review", SID, "--by", "Bo Reader"])
    mine = read_json(iso["data"] / "songs" / f"{SID}.json")
    assert mine["base"] == base and mine["review"]["by"] == "Bo Reader"


def test_undo_without_a_copy_makes_none(iso, shipped, capsys):
    before = shipped.read_bytes()
    assert cli.main(["review", SID, "--undo"]) == 0
    assert "nothing to undo" in capsys.readouterr().out
    assert shipped.read_bytes() == before and not (iso["data"] / "songs").exists()


def test_undo_drops_a_copy_that_only_held_the_review(iso, shipped, capsys):
    cli.main(["review", SID, "--by", "Ann Reader"])
    assert cli.main(["review", SID, "--undo"]) == 0
    assert not (iso["data"] / "songs" / f"{SID}.json").exists()
    assert "shipped song again" in capsys.readouterr().out


def test_undo_keeps_a_copy_with_other_changes(iso, shipped, capsys):
    mine = iso["data"] / "songs" / f"{SID}.json"
    cli.main(["review", SID, "--by", "Ann Reader"])
    s = read_json(mine)
    s["notes"]["made"] = "My own rewrite of the notes."
    write_json(mine, s)
    capsys.readouterr()
    cli.main(["review", SID, "--undo"])
    s = read_json(mine)
    assert s["review"] == {"state": "draft", "by": None, "date": None} and s["notes"]["made"].startswith("My own")
    assert "stays" in capsys.readouterr().out


def test_undo_drops_a_stale_copy_that_only_held_the_review(iso, shipped, capsys):
    cli.main(["review", SID, "--by", "Ann Reader"])
    s = read_json(shipped)
    s["title"] = "First Song (corrected)"
    write_json(shipped, s)
    capsys.readouterr()
    cli.main(["review", SID, "--undo"])
    assert not (iso["data"] / "songs" / f"{SID}.json").exists()
    out = capsys.readouterr().out
    assert "shipped song again" in out and "other changes" not in out


def test_undo_on_an_old_stale_copy_doesnt_claim_other_changes(iso, shipped, capsys):
    mine = iso["data"] / "songs" / f"{SID}.json"
    cli.main(["review", SID, "--by", "Ann Reader"])
    c = read_json(mine)
    del c["base"]["content"]  # made before base.content existed
    write_json(mine, c)
    s = read_json(shipped)
    s["title"] = "First Song (corrected)"
    write_json(shipped, s)
    capsys.readouterr()
    cli.main(["review", SID, "--undo"])
    out = capsys.readouterr().out
    assert mine.exists() and "other changes" not in out and "can't tell" in out and f"liner-notes drop {SID}" in out


def test_drop_moves_your_copy_aside(iso, shipped, capsys):
    import datetime as dt

    cli.main(["review", SID, "--by", "Ann Reader"])
    assert cli.main(["drop", SID]) == 0
    assert not (iso["data"] / "songs" / f"{SID}.json").exists()
    kept = iso["data"] / "dropped" / dt.date.today().isoformat() / "songs" / f"{SID}.json"
    assert read_json(kept)["review"]["by"] == "Ann Reader"
    out = capsys.readouterr().out
    assert "shipped song again" in out and str(kept) in out
    assert cli.main(["drop", SID]) == 1
    assert "no copy" in capsys.readouterr().out


def test_drop_drops_a_journey_copy(iso, shipped):
    j = iso["data"] / "journeys/test-trip/journey.json"
    write_json(j, read_json(iso["home"] / "journeys/test-trip/journey.json"))
    assert cli.main(["drop", "test-trip"]) == 0 and not j.exists()
    assert list((iso["data"] / "dropped").glob("*/journeys/test-trip/journey.json"))


def test_drop_twice_in_a_day_keeps_both(iso, shipped):
    for by in ("Ann Reader", "Bo Reader"):
        cli.main(["review", SID, "--by", by])
        cli.main(["drop", SID])
    kept = sorted((iso["data"] / "dropped").glob(f"*/songs/{SID}*.json"))
    assert sorted(read_json(k)["review"]["by"] for k in kept) == ["Ann Reader", "Bo Reader"]


def test_drop_leaves_a_song_of_your_own(iso, shipped, capsys):
    f = iso["data"] / "songs/zed--mine.json"
    write_json(f, {"id": "zed--mine", "title": "Mine", "artist": "Zed", "year": 2000})
    assert cli.main(["drop", "zed--mine"]) == 1
    assert f.exists() and "yours alone" in capsys.readouterr().out


def test_review_of_your_own_song_says_nothing_about_a_maintainer(iso, shipped, capsys):
    write_json(
        iso["data"] / "songs/zed--mine.json",
        {
            "id": "zed--mine",
            "title": "Mine",
            "artist": "Zed",
            "year": 2000,
            "aliases": {},
            "notes": {},
            "review": {"state": "draft"},
            "provenance": {"ai_generated": False},
        },
    )
    cli.main(["review", "zed--mine", "--by", "Ann Reader"])
    out = capsys.readouterr().out
    assert "reviewed by Ann Reader" in out and "maintainer" not in out and "shipped" not in out


def test_review_in_a_checkout_points_at_shipped(iso, shipped, capsys):
    subprocess.run(["git", "init", "-q", str(iso["home"])], check=True)
    cli.main(["review", SID, "--undo"])
    assert "--shipped" not in capsys.readouterr().out
    cli.main(["review", SID, "--by", "Ann Reader"])
    assert "If you maintain the bank, review --shipped" in capsys.readouterr().out
    cli.main(["review", SID, "--undo"])
    assert "--shipped" not in capsys.readouterr().out


def test_check_counts_your_reviews_apart(iso, shipped, capsys):
    from liner_notes import check

    cli.main(["review", SID, "--by", "Ann Reader"])
    out = []
    check.check(log=lambda *a: out.append(" ".join(map(str, a))))
    assert "test-trip: 2 songs, 2 written, 0 reviewed by a person, plus 1 in your copies" in out
    capsys.readouterr()
    cli.main(["list"])
    assert "  0 reviewed (+1 yours)  not built" in capsys.readouterr().out


def test_review_needs_by(iso, shipped, capsys):
    before = shipped.read_bytes()
    with pytest.raises(SystemExit) as e:
        cli.main(["review", SID])
    assert "--by" in str(e.value.code) and "your copy" in str(e.value.code)
    assert shipped.read_bytes() == before and not (iso["data"] / "songs").exists()


def test_doctor_flags_a_stale_overlay(iso, shipped):
    cli.main(["review", SID, "--by", "Ann Reader"])
    assert not [x for x in doctor_lines() if SID in x]
    s = read_json(shipped)
    s["notes"]["made"] = "Made in a different room, says the corrected sleeve."
    write_json(shipped, s)
    hits = [x for x in doctor_lines() if SID in x]
    assert hits and hits[0].startswith("warn your copy") and "stale" in hits[0] and "changed after" in hits[0]
    assert f"liner-notes drop {SID}" in hits[0] and tilde(shipped) in hits[0]


def test_doctor_flags_an_id_collision(iso, shipped):
    write_json(iso["data"] / "songs" / f"{SID}.json", {**read_json(shipped), "title": "My Own Song"})
    hits = [x for x in doctor_lines() if SID in x]
    assert hits and hits[0].startswith("warn your copy") and "same id as a shipped song" in hits[0]
    assert f"liner-notes drop {SID}" in hits[0] and "overlay" not in hits[0] and "base" not in hits[0]


def test_doctor_tags_a_copy_whose_song_is_gone(iso, shipped):
    cli.main(["review", SID, "--by", "Ann Reader"])
    shipped.unlink()
    hits = [x for x in doctor_lines() if SID in x]
    assert hits and hits[0].startswith("info your copy") and f"{SID}: gone." in hits[0]


def test_doctor_writes_home_as_a_tilde(iso, shipped, monkeypatch):
    monkeypatch.setenv("HOME", str(iso["home"].parent))
    cli.main(["review", SID, "--by", "Ann Reader"])
    s = read_json(shipped)
    s["title"] = "Changed"
    write_json(shipped, s)
    lines = doctor_lines()
    hit = next(x for x in lines if SID in x)
    assert f"~/data/songs/{SID}.json" in hit and f"~/home/songs/{SID}.json" in hit
    assert not any(str(iso["home"].parent) + "/" in x for x in lines)


def test_doctor_skips_copies_when_the_bank_is_missing(iso, shipped, tmp_path, monkeypatch):
    cli.main(["review", SID, "--by", "Ann Reader"])
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("LINER_NOTES_HOME", str(empty))
    lines = doctor_lines()
    assert any(x.startswith("FAIL bank") for x in lines) and not [x for x in lines if SID in x]


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


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A package in a site-packages with no checkout around it, and LINER_NOTES_HOME at a checkout."""
    pkg = tmp_path / "site-packages" / "liner_notes"
    (pkg / "shipped" / "songs").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    (checkout / "songs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    monkeypatch.setattr(paths, "PACKAGE", pkg)
    monkeypatch.setenv("LINER_NOTES_HOME", str(checkout))
    return pkg / "shipped" / "songs" / "a--b.json"


def test_nothing_writes_into_an_installed_package(installed):
    from liner_notes import overlay

    for write in (write_json, overlay.write, lambda p, o: paths.atomic_write(p, "{}")):
        with pytest.raises(PermissionError):
            write(installed, {"id": "a--b"})
        assert not installed.exists()


def test_write_json_is_atomic(tmp_path, monkeypatch):
    seen = []
    real = paths.atomic_write
    monkeypatch.setattr(paths, "atomic_write", lambda p, data: seen.append(p) or real(p, data))
    write_json(tmp_path / "x.json", {"a": 1})
    assert seen == [tmp_path / "x.json"] and read_json(tmp_path / "x.json") == {"a": 1}


def test_generate_stamps_the_base_on_a_shipped_song_it_copies(tmp_path, monkeypatch):
    home, data = gen_setup(tmp_path, monkeypatch)
    f = home / "songs/massive-attack--protection.json"
    s = read_json(f)
    s["notes"] = {}
    write_json(f, s)
    gen.generate("Test Mix", write=True, budget=5, client=FakeClient(), lookup=lookup, log=lambda *a: None)
    mine = read_json(data / "songs/massive-attack--protection.json")
    assert mine["notes"]["made"] and mine["base"]["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
