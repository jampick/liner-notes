import json
import os
import shlex
import signal
import socket
import sys
import threading
import time

import pytest

from liner_notes import config, service
from liner_notes.launch import Result, System
from liner_notes.launch import play as P
from liner_notes.launch.hyprland import PAGE_CLASS, Hypr, lua_str, watch
from tests.helpers_build import iso, make_bank  # noqa: F401

TOML = '[[track]]\npath = "https://www.youtube.com/watch?v={a}"\ntitle = "One"\n\n[[track]]\npath = "https://www.youtube.com/watch?v={b}"\ntitle = "Two"\n'


class Fake(System):
    """cliamp, hyprctl, systemctl, chromium and the clock, all pretend. Every call is logged."""

    def __init__(self, running=True, playing=None, clients=None, active="0xgame", bridge=True):
        self.env = {"HYPRLAND_INSTANCE_SIGNATURE": "sig", "XDG_RUNTIME_DIR": "/run/user/1000", "PATH": "/usr/bin"}
        self.running, self.playing, self.bridge = running, playing, bridge
        self.clients_ = list(clients or [])
        self.active_ = active
        self.calls, self.spawned, self.t = [], [], 0.0
        self.procs = {c["pid"]: c.pop("_cmd") for c in self.clients_ if "_cmd" in c}
        self.have = {"cliamp", "hyprctl", "chromium", "systemctl", "omarchy-launch-tui"}

    def cliamp_log(self):
        return [c[1:] for c in self.calls if c[0] == "cliamp"]

    def dispatches(self):
        return [c[2] for c in self.calls if c[:2] == ["hyprctl", "dispatch"]]

    def run(self, argv, timeout=10):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "cliamp":
            if not self.running:
                return Result(1)
            if argv[1] == "status":
                return Result(
                    0, json.dumps({"ok": True, "state": "playing" if self.playing else "stopped", "track": {"path": self.playing or ""}})
                )
            return Result(0, "{}")
        if argv[0] == "hyprctl":
            if argv[1] == "clients":
                return Result(0, json.dumps(self.clients_))
            if argv[1] == "activewindow":
                return Result(0, json.dumps({"address": self.active_} if self.active_ else {}))
            if argv[1] == "dispatch" and argv[2].startswith("hl.dsp.exec_cmd("):
                pid = 4242
                cmd = shlex.split(json.loads(argv[2][len("hl.dsp.exec_cmd(") : argv[2].index('", {') + 1]))
                self.procs[pid] = cmd
                url = next(a for a in cmd if a.startswith("--app=")).split("/p/")[1].split("#")[0]
                self.clients_.append({"address": "0xnew", "class": f"{PAGE_CLASS}{url}-Default", "pid": pid, "workspace": {"name": "2"}})
            if argv[1] == "dispatch" and argv[2].startswith("hl.dsp.window.close("):
                addr = argv[2].split('"address:')[1].split('"')[0]
                self.clients_ = [c for c in self.clients_ if c["address"] != addr]
            return Result(0, "ok")
        return Result(0, "")

    def spawn(self, argv):
        self.spawned.append(list(argv))
        if argv[0] == "omarchy-launch-tui":
            self.running = True
        return 9000 + len(self.spawned)

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.have else None

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t

    def http_ok(self, url, timeout=1):
        return self.bridge

    def cmdline(self, pid):
        return self.procs.get(pid, [])

    def pids(self):
        return []


def setup_lists(iso, journey=True):
    pl = iso["cliamp"] / "playlists"
    pl.mkdir()
    (pl / "Test Trip.toml").write_text(TOML.format(a="AAAAAAAAAAA", b="BBBBBBBBBBB"))
    (pl / "Test Trip (offline).toml").write_text('[[track]]\npath = "/home/u/.cache/journeys/audio/AAAAAAAAAAA.webm"\ntitle = "One"\n')
    (pl / "No Notes.toml").write_text(TOML.format(a="CCCCCCCCCCC", b="DDDDDDDDDDD"))
    if journey:
        make_bank(iso["home"])
        (iso["dist"] / "test-trip.html").write_text("page")
    from liner_notes.bank import Bank

    return Bank([iso["home"]])


def cfg():
    c = config.load()
    c["keys"] = {**c["keys"], "trip": "Test Trip", "none": "No Notes"}
    return c


def test_key_loads_in_order_and_opens_page_silently(iso):
    bank = setup_lists(iso)
    f = Fake(playing="https://www.youtube.com/watch?v=ZZZZZZZZZZZ")
    assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "loaded"
    ops = f.cliamp_log()
    assert ops[0] == ["status", "--json"]
    assert ops[1] == ["remote", "call", "load", "--params", json.dumps({"playlist": "Test Trip"}), "--wait"]
    assert ops[2] == ["shuffle", "off"]
    assert ops[3] == ["remote", "call", "queue.play", "--params", json.dumps({"index": 0}), "--wait"]
    d = f.dispatches()
    ex = next(x for x in d if x.startswith("hl.dsp.exec_cmd("))
    assert 'workspace = "2 silent", fullscreen = true' in ex
    assert "remote-debugging" not in ex and "--user-data-dir=" in ex and "/p/test-trip.html#companion" in ex
    assert d[-1] == 'hl.dsp.focus({ window = "address:0xgame" })'
    assert any(s[-2:] == ["watch-page", "0xnew"] for s in f.spawned)
    assert not any(s[0] == "omarchy-launch-tui" for s in f.spawned)


def test_same_playlist_playing_leaves_music_alone(iso):
    bank = setup_lists(iso)
    f = Fake(playing="https://www.youtube.com/watch?v=BBBBBBBBBBB")
    assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "already playing"
    assert f.cliamp_log() == [["status", "--json"]]
    assert any(x.startswith("hl.dsp.exec_cmd(") for x in f.dispatches())  # the page comes back


def test_our_page_already_open_is_left_alone(iso):
    bank = setup_lists(iso)
    profile = iso["state"] / "chromium"
    ours = {
        "address": "0xours",
        "class": f"{PAGE_CLASS}downtempo-drift.html-Default",
        "pid": 77,
        "workspace": {"name": "2"},
        "_cmd": ["chromium", f"--user-data-dir={profile}"],
    }
    f = Fake(playing="https://www.youtube.com/watch?v=ZZZZZZZZZZZ", clients=[ours])
    P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank)
    assert f.dispatches() == []  # no close, no new window, no focus change: the page switches journeys itself
    assert any(s[-1] == "0xours" for s in f.spawned)


def test_foreign_page_is_replaced_on_its_workspace(iso):
    bank = setup_lists(iso)
    pilot = {
        "address": "0xold",
        "class": f"{PAGE_CLASS}yacht-rock.html-Default",
        "pid": 55,
        "workspace": {"name": "7"},
        "_cmd": ["chromium", "--user-data-dir=/home/u/.local/state/journeys/chromium"],
    }
    f = Fake(playing=None, clients=[pilot])
    P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank)
    d = f.dispatches()
    assert d[0] == 'hl.dsp.window.close({ window = "address:0xold" })'
    assert 'workspace = "7 silent"' in d[1]
    assert (iso["state"] / "workspace").read_text() == "7\n"


def test_offline_twin(iso):
    bank = setup_lists(iso)
    P.set_offline(True)
    assert P.is_offline() and (iso["state"] / "offline").exists()
    f = Fake(playing="https://www.youtube.com/watch?v=ZZZZZZZZZZZ")
    P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank)
    assert f.cliamp_log()[1][4] == json.dumps({"playlist": "Test Trip (offline)"})
    assert "/p/test-trip.html" in next(x for x in f.dispatches() if x.startswith("hl.dsp.exec_cmd("))
    # A playlist without a twin plays as it is.
    assert P.resolve("none", cfg())[0] == "No Notes"
    P.set_offline(False)
    assert not P.is_offline()


def test_offline_env_overrides(iso, monkeypatch):
    setup_lists(iso)
    monkeypatch.setenv("LINER_NOTES_OFFLINE", "1")
    assert P.resolve("Test Trip", cfg())[0] == "Test Trip (offline)"
    monkeypatch.setenv("LINER_NOTES_OFFLINE", "0")
    assert P.resolve("Test Trip", cfg())[0] == "Test Trip"


def test_no_journey_opens_live_page(iso):
    bank = setup_lists(iso)
    f = Fake(playing=None)
    P.play("none", f, cfg(), 47421, log=lambda *a: None, bank=bank)
    assert "/p/live.html#companion" in next(x for x in f.dispatches() if x.startswith("hl.dsp.exec_cmd("))
    assert (iso["dist"] / "live.html").is_file()


def test_unknown_playlist(iso):
    setup_lists(iso)
    with pytest.raises(P.Problem):
        P.play("nope", Fake(), cfg(), 47421, log=lambda *a: None)


def test_cold_start_is_detached_and_fast(iso, tmp_path):
    """cliamp isn't running: its terminal is started in its own session and never waited on."""
    bank = setup_lists(iso)

    class Cold(Fake):
        def spawn(self, argv):
            if argv[0] == "omarchy-launch-tui":
                self.spawned.append(list(argv))
                # A terminal that never exits on its own, like foot holding cliamp.
                self.term = System(env=dict(os.environ)).spawn([sys.executable, "-c", "import time; time.sleep(60)"])
                self.running = True
                return self.term
            return super().spawn(argv)

        def sleep(self, s):
            time.sleep(min(s, 0.01))
            self.t += s

    f = Cold(running=False, active="0xgame")
    t0 = time.monotonic()
    try:
        assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "started"
        assert time.monotonic() - t0 < 5
        term = f.spawned[0]
        assert term[:2] == ["omarchy-launch-tui", "--app-id=org.omarchy.cliamp"] and term[-3:] == [
            "--no-shuffle",
            "--playlist",
            "Test Trip",
        ]
        assert os.getpgid(f.term) == f.term  # its own session and process group
        ops = [o for o in f.cliamp_log() if o[0] != "status"]
        assert ops == [["shuffle", "off"], ["remote", "call", "queue.play", "--params", '{"index": 0}', "--wait"]]
        assert f.dispatches()[-1] == 'hl.dsp.focus({ window = "address:0xgame" })'
    finally:
        os.kill(f.term, signal.SIGKILL)
        os.waitpid(f.term, 0)


def test_cold_start_moves_cliamp_to_its_workspace(iso):
    bank = setup_lists(iso)
    f = Fake(running=False, clients=[{"address": "0xcli", "class": "org.omarchy.cliamp", "pid": 1, "workspace": {"name": "5"}}])
    P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank)
    assert 'hl.dsp.window.move({ window = "address:0xcli", workspace = "3", follow = false })' in f.dispatches()


def test_bridge_started_by_service_or_detached(iso):
    f = Fake(bridge=False)
    P.ensure_bridge(f, 47421, log=lambda *a: None)
    assert f.spawned[0][-4:] == ["liner_notes", "serve", "--port", "47421"] and f.spawned[0][0] == sys.executable
    (iso["home"].parent / "systemd").mkdir()
    (iso["home"].parent / "systemd/liner-notes.service").write_text("[Unit]\n")
    g = Fake(bridge=False)
    P.ensure_bridge(g, 47421, log=lambda *a: None)
    assert ["systemctl", "--user", "start", "liner-notes.service"] in g.calls and not g.spawned


def unlua(s):
    """Read back a Lua string literal the way Lua would (enough for what lua_str writes)."""
    assert s[0] == s[-1] == '"'
    out, i = [], 1
    while i < len(s) - 1:
        c = s[i]
        if c == "\\":
            n = s[i + 1]
            if n.isdigit():
                out.append(chr(int(s[i + 1 : i + 4])))
                i += 4
                continue
            out.append({"n": "\n"}.get(n, n))
            i += 2
            continue
        assert c != '"', "unescaped quote ends the string early"
        out.append(c)
        i += 1
    return "".join(out)


@pytest.mark.parametrize("text", ["plain", 'a "quoted" name', "back\\slash", "new\nline", "tab\there\x01", "it's /p/x.html#companion"])
def test_lua_strings_round_trip(text):
    assert unlua(lua_str(text)) == text


def test_exec_cmd_quotes_paths_with_spaces(iso, monkeypatch):
    monkeypatch.setenv("LINER_NOTES_STATE", str(iso["state"] / 'my "state" dir'))
    bank = setup_lists(iso)
    f = Fake(playing=None)
    P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank)
    ex = next(x for x in f.dispatches() if x.startswith("hl.dsp.exec_cmd("))
    cmd = unlua(ex[len("hl.dsp.exec_cmd(") : ex.index(", {")])
    argv = shlex.split(cmd)
    assert f'--user-data-dir={iso["state"]}/my "state" dir/chromium' in argv


def test_watch_remembers_moves_until_close(iso):
    a, b = socket.socketpair()
    seen = []
    t = threading.Thread(target=watch, args=("0xabc", None, seen.append), kwargs={"connect": lambda p: b})
    t.start()
    a.sendall(b"openwindow>>abc,2,chromium,x\nmovewindowv2>>abc,4,4\nmovewindowv2>>def,9,9\nmovewindowv2>>abc,6,web\nclosewindow>>abc\n")
    t.join(2)
    a.close()
    assert not t.is_alive() and seen == ["4", "web"]


def test_events_socket_path():
    f = Fake()
    assert str(Hypr(f).events_socket()) == "/run/user/1000/hypr/sig/.socket2.sock"


def test_service_install(iso, tmp_path):
    tpl = tmp_path / "liner-notes.service"
    tpl.write_text("[Service]\nExecStart=@PYTHON@ -m liner_notes serve\nRestart=on-failure\n")
    f = Fake()
    assert service.install(f, template=tpl, python="/opt/py env/bin/python3", log=lambda *a: None) == 0
    unit = (iso["home"].parent / "systemd/liner-notes.service").read_text()
    assert 'ExecStart="/opt/py env/bin/python3" -m liner_notes serve' in unit
    assert f.calls == [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "liner-notes.service"]]
    assert service.install(Fake(), template=tpl, log=lambda *a: None) == 0
    assert f"ExecStart={sys.executable} -m" in (iso["home"].parent / "systemd/liner-notes.service").read_text()
    g = Fake()
    assert service.status(g, log=lambda *a: None) == 0 and g.calls == [["systemctl", "--user", "is-active", "liner-notes.service"]]
    h = Fake()
    service.uninstall(h, log=lambda *a: None)
    assert not (iso["home"].parent / "systemd/liner-notes.service").exists()
    assert h.calls[0] == ["systemctl", "--user", "disable", "--now", "liner-notes.service"]


def test_shipped_unit_template_if_present():
    if not service.TEMPLATE.is_file():
        pytest.skip("the unit ships with the bridge work")
    assert "@PYTHON@" in service.TEMPLATE.read_text()


def test_config_keys_and_overrides(iso):
    assert config.load()["keys"]["yachthouse"] == "Yacht House"
    (iso["home"].parent / "config.toml").write_text('[keys]\nmine = "My List"\n[stage]\ncliamp_workspace = 8\n')
    c = config.load()
    assert c["keys"]["mine"] == "My List" and c["keys"]["drift"] == "Downtempo Drift"
    assert c["stage"]["cliamp_workspace"] == 8 and c["stage"]["workspace"] == 2
