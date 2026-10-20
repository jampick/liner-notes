import os

from liner_notes import cli, doctor
from liner_notes.bank import Bank, write_json
from tests.helpers_build import iso, make_bank  # noqa: F401
from tests.test_play import Fake


def test_port_owner_from_proc(tmp_path):
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    head = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (proc / "net/tcp").write_text(
        head + "   0: 0100007F:B91F 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 424242 1\n"
    )
    (proc / "net/tcp6").write_text(head)
    (proc / "321/fd").mkdir(parents=True)
    os.symlink("socket:[424242]", proc / "321/fd/5")
    (proc / "321/cmdline").write_bytes(b"/usr/bin/python3\0-m\0liner_notes\0serve\0")
    assert doctor.port_owner(47391, proc) == (321, ["/usr/bin/python3", "-m", "liner_notes", "serve", ""])
    assert doctor.port_owner(47392, proc) is None


def test_coverage_reads_retimed_lyrics_and_disk_video(iso):
    make_bank(iso["home"])
    write_json(iso["data"] / "lyrics/AAAAAAAAAAA.json", {"lines": []})
    write_json(iso["pilot"] / "lyrics/BBBBBBBBBBB.json", {"lines": []})
    (iso["cache"] / "video").mkdir()
    (iso["cache"] / "video/SSSSSSSSSSS.mp4").write_bytes(b"v")
    write_json(iso["data"] / "standins/test-trip.json", {"BBBBBBBBBBB": {"standin": "SSSSSSSSSSS"}})
    (iso["cache"] / "covers").mkdir()
    (iso["cache"] / "covers/ann-artist--first-song.webp").write_bytes(b"c")
    rows = doctor.coverage("test-trip", Bank([iso["home"]]))
    a, b = (h for _, h in rows)
    assert a == {"notes": "written", "cover": "cache", "video": "", "lyrics": "retimed"}
    assert b == {"notes": "written", "cover": "", "video": "stand-in", "lyrics": "retimed (pilot)"}


def test_doctor_runs_without_touching_anything(iso, monkeypatch):
    make_bank(iso["home"])
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    out = []
    f = Fake()
    doctor.run(f, songs=True, log=out.append)
    text = "\n".join(out)
    assert "test-trip" in text and "First Song" in text and "port 47391" in text
    assert all(c[0] in ("cliamp", "systemctl") for c in f.calls) and not f.spawned
    assert not any(c[:2] in (["cliamp", "remote"], ["cliamp", "play"]) for c in f.calls)


def test_cli_offline_and_build_live(iso, capsys, monkeypatch):
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    assert cli.main(["offline", "on"]) == 0 and (iso["state"] / "offline").exists()
    assert "offline mode on" in capsys.readouterr().out
    assert cli.main(["offline", "off"]) == 0 and not (iso["state"] / "offline").exists()
    if cli.paths.PACKAGE.joinpath("web/template.html").exists():
        assert cli.main(["build", "live"]) == 0 and (iso["dist"] / "live.html").exists()
