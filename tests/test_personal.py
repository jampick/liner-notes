import json
import os

from liner_notes import personal
from liner_notes.bank import Bank, read_json, write_json
from tests.helpers_build import iso, make_bank  # noqa: F401


def pilot_tree(base):
    """A small copy of the pilot's layout, with made-up content."""
    src, pc, ps = base / "journeys", base / "pc", base / "ps"
    write_json(src / "lyrics/AAAAAAAAAAA.json", {"lines": [{"t": 1.0, "text": "la la"}]})
    write_json(src / "lyrics/RRRRRRRRRRR.json", {"lines": [{"t": 2.0, "text": "remixed"}], "remix": {"events": []}})
    f = src / "playlists/test-trip"
    write_json(f / "standins.json", {"BBBBBBBBBBB": {"standin": "SSSSSSSSSSS", "standin_offset": 1}})
    write_json(f / "culture-videos.json", {"A Film": {"yt": "CCCCCCCCCCC", "start": 0}})
    write_json(f / "stage-cache.json", {"wiki:A Film": "https://example.org/a.jpg"})
    for sub, name in (("covers", "AAAAAAAAAAA.webp"), ("covers", "ZZZZZZZZZZZ.webp"), ("culture", "A_Film.webp"),
                      ("artists", "Ann.webp"), ("depth", "ab12.png")):
        (f / "media" / sub).mkdir(parents=True, exist_ok=True)
        (f / "media" / sub / name).write_bytes(name.encode())
    h = src / "playlists/test-house"
    write_json(h / "remixes.json", [{"id": "RRRRRRRRRRR", "title": "First Song (Remix)", "original": {"slug": "test-trip", "ytid": "AAAAAAAAAAA"}}])
    write_json(h / "originals/old-song.json", {"lines": [{"t": 0, "text": "old"}]})
    (h / "media/covers").mkdir(parents=True)
    (h / "media/covers/vol1.webp").write_bytes(b"vol1")
    (src / "dist").mkdir()
    songs = [{"id": "RRRRRRRRRRR", "remix": {"bpm": 124.0, "length": 300.0, "energy": [0.5]},
              "original": {"bpm": 100.0, "length": 240.0, "energy": [0.4]}}]
    (src / "dist/test-house.html").write_text("<script>\nconst SONGS = " + json.dumps(songs) + ";\n</script>")
    for sub, name in (("video", "AAAAAAAAAAA.mp4"), ("audio", "AAAAAAAAAAA.webm"), ("soundcloud", "123.m4a")):
        (pc / sub).mkdir(parents=True, exist_ok=True)
        (pc / sub / name).write_bytes(b"media " + name.encode())
    ps.mkdir()
    (ps / "workspace").write_text("4\n")
    write_json(ps / "av-latency.json", {"sink": 0.42})
    return src, pc, ps


def snapshot(*dirs):
    return sorted(str(p) for d in dirs for p in d.rglob("*"))


def test_migrate_personal(iso, tmp_path):
    make_bank(iso["home"])
    src, pc, ps = pilot_tree(tmp_path)
    bank = Bank([iso["home"]])
    before = snapshot(src, pc, ps)

    # A dry run plans everything and writes nothing anywhere.
    log = []
    m = personal.run(src, dry_run=True, bank=bank, pilot_cache=pc, pilot_state=ps, log=log.append)
    assert m["planned"] >= 15 and any("ZZZZZZZZZZZ" in x for x in log)
    assert snapshot(iso["data"], iso["cache"], iso["state"]) == [] and snapshot(src, pc, ps) == before

    m = personal.run(src, bank=bank, pilot_cache=pc, pilot_state=ps, log=lambda *a: None)
    d, c, s = iso["data"], iso["cache"], iso["state"]
    assert (d / "lyrics/AAAAAAAAAAA.json").is_file() and (d / "lyrics/RRRRRRRRRRR.json").is_file()
    assert (d / "standins/test-trip.json").is_file() and (d / "culture-videos/test-trip.json").is_file()
    assert (d / "remixes/originals/old-song.json").is_file()
    assert (c / "covers/ann-artist--first-song.webp").read_bytes() == b"AAAAAAAAAAA.webp"
    assert (c / "covers/test-house-vol1.webp").is_file() and not (c / "covers/ZZZZZZZZZZZ.webp").exists()
    assert (c / "culture/A_Film.webp").is_file() and (c / "artists/Ann.webp").is_file() and (c / "depth/ab12.png").is_file()
    assert (c / "pilot/test-trip.stage-cache.json").is_file()
    assert read_json(c / "profiles/RRRRRRRRRRR.json") == {"bpm": 124.0, "seconds": 300.0, "energy": [0.5]}
    assert read_json(c / "profiles/AAAAAAAAAAA.json")["bpm"] == 100.0
    assert (s / "workspace").read_text() == "4\n" and (s / "av-latency.json").is_file()
    # Video and audio are hard links to the pilot's files, not copies.
    for sub, name in (("video", "AAAAAAAAAAA.mp4"), ("audio", "AAAAAAAAAAA.webm"), ("soundcloud", "123.m4a")):
        assert os.path.samefile(c / sub / name, pc / sub / name)
        assert (c / sub / name).stat().st_nlink == 2

    mf = read_json(s / "migrate-personal.json")
    dests = {e["dest"] for e in mf["files"]}
    assert len(dests) == len(mf["files"]) == m["counts"]["copied"] + m["counts"]["hardlink"] + m["counts"]["profile"]
    every = {str(p) for p in (*d.rglob("*"), *c.rglob("*"), s / "workspace", s / "av-latency.json") if p.is_file()}
    assert every == dests
    assert all(len(e["sha256"]) == 64 for e in mf["files"])
    assert mf["skipped"] and "ZZZZZZZZZZZ" in mf["skipped"][0]
    assert snapshot(src, pc, ps) == before  # the pilot is untouched

    # A second run changes nothing and reports every file as already there.
    again = personal.run(src, bank=bank, pilot_cache=pc, pilot_state=ps, log=lambda *a: None)
    assert again["counts"] == {"same": len(mf["files"])}

    # A file edited on the new side is kept, never overwritten.
    write_json(d / "lyrics/AAAAAAAAAAA.json", {"lines": [{"t": 1.2, "text": "la la"}]})
    out = []
    third = personal.run(src, bank=bank, pilot_cache=pc, pilot_state=ps, log=out.append)
    assert third["counts"]["differs"] == 1 and read_json(d / "lyrics/AAAAAAAAAAA.json")["lines"][0]["t"] == 1.2
    assert any("kept" in x for x in out)


def test_link_falls_back_to_symlink(tmp_path, monkeypatch):
    import errno

    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x")

    def cross(*args):
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(os, "link", cross)
    assert personal._link(a, b) == "symlink" and b.is_symlink() and b.read_bytes() == b"x"
