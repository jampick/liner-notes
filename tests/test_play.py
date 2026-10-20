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

    def __init__(self, running=True, playing=None, clients=None, active="0xgame", bridge=True, queue=None, state=None, systemctl_ok=True):
        self.env = {"HYPRLAND_INSTANCE_SIGNATURE": "sig", "XDG_RUNTIME_DIR": "/run/user/1000", "PATH": "/usr/bin"}
        self.running, self.playing, self.bridge = running, playing, bridge
        self.queue = queue if queue is not None else ([playing] if playing else [])
        self.state = state or ("playing" if playing else "stopped")
        self.systemctl_ok = systemctl_ok
        self.clients_ = list(clients or [])
        self.active_ = active
        self.calls, self.spawned, self.t = [], [], 0.0
        self.procs = {c["pid"]: c.pop("_cmd") for c in self.clients_ if "_cmd" in c}
        self.have = {"cliamp", "hyprctl", "chromium", "systemctl", "omarchy-launch-tui"}

    def cliamp_log(self):
        return [c[1:] for c in self.calls if c[0] == "cliamp"]

    def ops(self):
        """What changed playback: every cliamp call except the reads (status, queue.list)."""
        return [c for c in self.cliamp_log() if c[0] != "status" and c[:3] != ["remote", "call", "queue.list"]]

    def dispatches(self):
        return [c[2] for c in self.calls if c[:2] == ["hyprctl", "dispatch"]]

    def run(self, argv, timeout=10):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "cliamp":
            if not self.running:
                return Result(1)
            if argv[1] == "status":
                return Result(0, json.dumps({"ok": True, "state": self.state, "track": {"path": self.playing or ""}}))
            if argv[1:4] == ["remote", "call", "queue.list"]:
                if self.queue is False:
                    return Result(1)
                return Result(0, json.dumps({"job": {"state": "succeeded", "result": {"tracks": [{"path": q} for q in self.queue]}}}))
            return Result(0, "{}")
        if argv[0] == "systemctl":
            return Result(0 if self.systemctl_ok else 1, "")
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
    assert f.cliamp_log()[0] == ["status", "--json"]
    ops = f.ops()
    assert ops[0] == ["remote", "call", "load", "--params", json.dumps({"playlist": "Test Trip"}), "--wait"]
    assert ops[1] == ["shuffle", "off"]
    assert ops[2] == ["remote", "call", "queue.play", "--params", json.dumps({"index": 0}), "--wait"]
    assert (iso["state"] / "playlist").read_text() == "Test Trip\n"
    d = f.dispatches()
    ex = next(x for x in d if x.startswith("hl.dsp.exec_cmd("))
    assert 'workspace = "2 silent", fullscreen = true' in ex
    assert "remote-debugging" not in ex and "--user-data-dir=" in ex and "/p/test-trip.html#companion" in ex
    assert d[-1] == 'hl.dsp.focus({ window = "address:0xgame" })'
    assert any(s[-2:] == ["watch-page", "0xnew"] for s in f.spawned)
    assert not any(s[0] == "omarchy-launch-tui" for s in f.spawned)


TRIP = ["https://www.youtube.com/watch?v=AAAAAAAAAAA", "https://www.youtube.com/watch?v=BBBBBBBBBBB"]


def test_same_playlist_playing_leaves_music_alone(iso):
    bank = setup_lists(iso)
    f = Fake(playing=TRIP[1], queue=list(TRIP))
    assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "already playing"
    assert f.ops() == []
    assert any(x.startswith("hl.dsp.exec_cmd(") for x in f.dispatches())  # the page comes back
    assert (iso["state"] / "playlist").read_text() == "Test Trip\n"


def test_same_playlist_paused_is_resumed(iso):
    bank = setup_lists(iso)
    f = Fake(playing=TRIP[0], queue=list(TRIP), state="paused")
    assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "resumed"
    assert f.ops() == [["play"]]
    assert any(x.startswith("hl.dsp.exec_cmd(") for x in f.dispatches())


def test_shared_upload_from_another_playlist_still_loads(iso):
    """The playing track is in this playlist too, but the queue is another playlist: load this one."""
    bank = setup_lists(iso)
    f = Fake(playing=TRIP[1], queue=[TRIP[1], "https://www.youtube.com/watch?v=EEEEEEEEEEE"])
    assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "loaded"
    assert f.ops()[0][2] == "load"


def test_queue_unreadable_falls_back_to_the_playing_track(iso):
    bank = setup_lists(iso)
    f = Fake(playing=TRIP[1], queue=False)
    assert P.play("trip", f, cfg(), 47421, log=lambda *a: None, bank=bank) == "already playing"
    g = Fake(playing="https://www.youtube.com/watch?v=ZZZZZZZZZZZ", queue=False)
    assert P.play("trip", g, cfg(), 47421, log=lambda *a: None, bank=bank) == "loaded"


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
    assert f.ops()[0][4] == json.dumps({"playlist": "Test Trip (offline)"})
    assert (iso["state"] / "playlist").read_text() == "Test Trip (offline)\n"
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
        assert f.ops() == [["shuffle", "off"], ["remote", "call", "queue.play", "--params", '{"index": 0}', "--wait"]]
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
    h = Fake(bridge=False, systemctl_ok=False)
    P.ensure_bridge(h, 47421, log=lambda *a: None)
    assert ["systemctl", "--user", "start", "liner-notes.service"] in h.calls
    assert h.spawned and h.spawned[0][-4:] == ["liner_notes", "serve", "--port", "47421"]


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
    import tomllib

    assert config.load()["keys"] == {}  # nobody's playlists ship as defaults
    assert tomllib.loads(config.EXAMPLE.read_text())["keys"]["drift"] == "Downtempo Drift"
    (iso["home"].parent / "config.toml").write_text('[keys]\nmine = "My List"\n[stage]\ncliamp_workspace = 8\n')
    c = config.load()
    assert c["keys"] == {"mine": "My List"}
    assert c["stage"]["cliamp_workspace"] == 8 and c["stage"]["workspace"] == 2


LUA_CASES = [
    "plain",
    'a "quoted" name',
    "back\\slash",
    "new\nline",
    "tab\there\x01",
    "\x019 digit after",
    "it's /p/x.html#companion",
    "]] [[ ]==]",
    "üñí 日本",
    "\x7f",
    "a\rb",
    "end\\",
]


@pytest.mark.parametrize("lua", ["lua5.4", "lua", "luajit"])
def test_lua_strings_in_real_lua(lua):
    """What Hyprland's Lua reads back is exactly the text we meant."""
    import shutil
    import subprocess

    if not shutil.which(lua):
        pytest.skip(f"{lua} isn't installed")
    for text in LUA_CASES:
        r = subprocess.run([lua, "-e", f"io.write({lua_str(text)})"], capture_output=True, timeout=5)
        assert r.returncode == 0 and r.stdout == text.encode(), (lua, text, r.stdout, r.stderr)
