"""`liner-notes play <short|name>`: what a Stream Deck key does.

    1. Find the playlist: a short name from [keys] in config.toml, or a cliamp playlist name.
       With offline mode on, its "<Name> (offline)" twin when there is one.
    2. If that playlist is already playing, leave the music alone and only bring the page back.
    3. Otherwise load it into the running cliamp, or start cliamp with it, detached, on its own
       workspace without taking focus. Then shuffle off and play from the first song.
    4. Open the journey's page (the live stage when the playlist has no journey) in companion mode
       on the workspace it was on last time, full screen, without taking focus, and give focus back
       to whatever had it.

Nothing waits on cliamp's terminal: it's started in its own session, so the key returns at once.
"""

import json
import os
import shlex
import sys

from .. import paths
from ..ids import slug as slugify
from . import chromium
from .hyprland import PAGE_CLASS, Hypr

OFFLINE_SUFFIX = " (offline)"


class Problem(Exception):
    pass


# ---------------------------------------------------------------- offline switch


def offline_file():
    return paths.state() / "offline"


def is_offline():
    v = os.environ.get("LINER_NOTES_OFFLINE")
    if v is not None:
        return v not in ("", "0", "off", "no")
    return offline_file().exists()


def set_offline(on):
    f = offline_file()
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("on\n")
    else:
        f.unlink(missing_ok=True)


# ---------------------------------------------------------------- which playlist, which page


def playlist_file(name):
    return paths.cliamp_dir() / "playlists" / f"{name}.toml"


def resolve(name, cfg, offline=None):
    """(playlist name, its .toml) for a key or a name, the offline twin when offline mode is on."""
    pl = cfg["keys"].get(name, name)
    offline = is_offline() if offline is None else offline
    base = pl.removesuffix(OFFLINE_SUFFIX)
    if offline and playlist_file(base + OFFLINE_SUFFIX).is_file():
        pl = base + OFFLINE_SUFFIX
    f = playlist_file(pl)
    if not f.is_file() or not f.stat().st_size:
        raise Problem(f'no cliamp playlist named "{pl}" ({f})')
    return pl, f


def page_for(playlist, bank=None):
    """The built page to open: the playlist's journey, or the live stage."""
    from ..bank import Bank
    from ..build import LIVE

    base = playlist.removesuffix(OFFLINE_SUFFIX)
    bank = bank or Bank()
    slug = next((s for s, j in bank.journeys.items() if j.get("title") == base), None) or slugify(base)
    if slug in bank.journeys and (paths.dist() / f"{slug}.html").is_file():
        return slug
    return LIVE


def ensure_live_page(log):
    from ..build import LIVE, Refused, build_live

    if not (paths.dist() / f"{LIVE}.html").is_file():
        try:
            build_live(log=log)
        except (Refused, OSError) as e:
            log(f"liner-notes: couldn't build the live page: {e}")


# ---------------------------------------------------------------- cliamp


class Cliamp:
    def __init__(self, system):
        self.sys = system

    def status(self):
        r = self.sys.run(["cliamp", "status", "--json"], timeout=3)
        d = r.json() if r.ok else None
        return d if isinstance(d, dict) and d.get("ok", True) else None

    def call(self, op, params, timeout=15):
        return self.sys.run(["cliamp", "remote", "call", op, "--params", json.dumps(params), "--wait"], timeout=timeout)

    def load(self, playlist):
        return self.call("load", {"playlist": playlist})

    def from_the_top(self):
        self.sys.run(["cliamp", "shuffle", "off"], timeout=5)
        if not self.call("queue.play", {"index": 0}).ok:
            self.sys.run(["cliamp", "play"], timeout=5)


def playlist_paths(f):
    import tomllib

    try:
        return {t.get("path") for t in tomllib.loads(f.read_text()).get("track") or [] if t.get("path")}
    except (OSError, tomllib.TOMLDecodeError):
        return set()


def playing_from(status, f):
    """True when cliamp is playing a track that's in this playlist file. cliamp's status doesn't name the
    loaded playlist, so the playing track's path is the test."""
    if not status or status.get("state") != "playing":
        return False
    path = (status.get("track") or {}).get("path")
    return bool(path) and path in playlist_paths(f)


def terminal_argv(system, playlist):
    args = ["cliamp", "--no-shuffle", "--playlist", playlist]
    if system.which("omarchy-launch-tui"):
        return ["omarchy-launch-tui", "--app-id=org.omarchy.cliamp", *args]
    if system.which("xdg-terminal-exec"):
        return ["xdg-terminal-exec", "--app-id=org.omarchy.cliamp", "-e", *args]
    for term in ("foot", "kitty", "alacritty", "ghostty"):
        if system.which(term):
            return [term, "-e", *args]
    raise Problem("cliamp isn't running and there's no terminal to start it in")


def cold_start(system, playlist, cfg, log):
    """Start cliamp with the playlist, detached, and wait for it to answer (up to 10 s)."""
    hypr = Hypr(system)
    focus = (hypr.active() or {}).get("address") if hypr.available else None
    system.spawn(terminal_argv(system, playlist))
    player = Cliamp(system)
    end = system.now() + 10
    while system.now() < end:
        if player.status() is not None:
            break
        system.sleep(0.2)
    else:
        log("liner-notes: cliamp didn't answer within 10 s")
    if hypr.available:
        # A window rule usually puts cliamp on its workspace already; if it landed somewhere else, move it there quietly.
        ws = str(cfg["stage"]["cliamp_workspace"])
        for c in hypr.clients():
            if c.get("class") == "org.omarchy.cliamp" and str((c.get("workspace") or {}).get("name")) != ws:
                hypr.move_silent(c["address"], ws)
        if focus:
            hypr.focus(focus)


# ---------------------------------------------------------------- the bridge


def unit_installed():
    return (paths.user_systemd_dir() / "liner-notes.service").is_file()


def ensure_bridge(system, port, log):
    url = f"http://127.0.0.1:{port}/now"
    if system.http_ok(url):
        return True
    if unit_installed() and system.which("systemctl"):
        system.run(["systemctl", "--user", "start", "liner-notes.service"], timeout=10)
    else:
        system.spawn([sys.executable, "-m", "liner_notes", "serve", "--port", str(port)])
    end = system.now() + 3
    while system.now() < end:
        if system.http_ok(url):
            return True
        system.sleep(0.2)
    log(f"liner-notes: the bridge on {port} didn't answer; the page will say so until it does")
    return False


# ---------------------------------------------------------------- the page window


def remembered_workspace(cfg):
    w = os.environ.get("LINER_NOTES_WORKSPACE")
    if w:
        return w
    f = paths.state() / "workspace"
    try:
        w = f.read_text().strip()
    except OSError:
        w = ""
    return w or str(cfg["stage"]["workspace"])


def remember_workspace(ws):
    f = paths.state() / "workspace"
    paths.atomic_write(f, f"{ws}\n")


def start_watcher(system, addr):
    """One background `liner-notes watch-page` per page window, which writes down where it's moved to."""
    pidf = paths.state() / "watch-page.pid"
    try:
        pid, was = pidf.read_text().split()
        if was == addr and "watch-page" in system.cmdline(int(pid)):
            return int(pid)
    except (OSError, ValueError):
        pass
    pid = system.spawn([sys.executable, "-m", "liner_notes", "watch-page", addr])
    paths.atomic_write(pidf, f"{pid} {addr}\n")
    return pid


def open_page(system, page, cfg, port, log, quiet=True):
    """Show /p/<page>.html#companion. On Hyprland: one page window of our own, silently placed."""
    ensure_bridge(system, port, log)
    url = f"http://127.0.0.1:{port}/p/{page}.html#companion"
    profile = paths.state() / "chromium"
    browser = chromium.find(system, cfg["stage"].get("browser") or "")
    hypr = Hypr(system)
    if not browser:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        system.spawn([opener, url])
        return None
    argv = chromium.app_argv(browser, profile, url)
    if not hypr.available:
        system.spawn(argv)
        return None

    pages = [c for c in hypr.clients() if str(c.get("class", "")).startswith(PAGE_CLASS)]
    ours = [c for c in pages if chromium.uses_profile(system.cmdline(c.get("pid", 0)), profile)]
    if ours:
        # Our page follows the player by itself (it switches journeys in place), so leave it where it is.
        start_watcher(system, ours[0]["address"])
        if not quiet:
            hypr.focus(ours[0]["address"])
        return ours[0]["address"]

    focus = (hypr.active() or {}).get("address")
    for c in pages:  # another page (the pilot's, or a stray): this one replaces it, on its workspace
        name = (c.get("workspace") or {}).get("name")
        if name and not str(name).startswith("special"):
            remember_workspace(name)
        hypr.close(c["address"])
    # A closing Chromium on our profile would take the new window and drop the placement rule.
    end = system.now() + 5
    while system.now() < end and any(chromium.uses_profile(system.cmdline(p), profile) for p in system.pids()):
        system.sleep(0.2)

    ws = remembered_workspace(cfg)
    hypr.exec_cmd(shlex.join(argv), f"{ws} silent" if quiet else ws, fullscreen=True)
    new = None
    end = system.now() + 5
    while system.now() < end and not new:
        new = next(
            (
                c["address"]
                for c in hypr.clients()
                if str(c.get("class", "")).startswith(PAGE_CLASS) and chromium.uses_profile(system.cmdline(c.get("pid", 0)), profile)
            ),
            None,
        )
        if not new:
            system.sleep(0.2)
    if new:
        start_watcher(system, new)
    # Closing the old page can leave nothing focused; hand focus back to whatever had it.
    if quiet and focus:
        system.sleep(0.5)
        hypr.focus(focus)
    return new


# ---------------------------------------------------------------- the whole key press


def play(name, system=None, cfg=None, port=None, log=print, bank=None):
    """Returns what happened: 'already playing', 'loaded' or 'started'."""
    from .. import config
    from . import System

    cfg = cfg or config.load()
    system = system or System()
    port = port or config.PORT
    playlist, f = resolve(name, cfg)
    page = page_for(playlist, bank)
    if page == "live":
        ensure_live_page(log)
    player = Cliamp(system)
    status = player.status()
    if status is not None and playing_from(status, f):
        open_page(system, page, cfg, port, log)
        return "already playing"
    if status is not None:
        if not player.load(playlist).ok:
            raise Problem(f'cliamp wouldn\'t load "{playlist}"')
        what = "loaded"
    else:
        cold_start(system, playlist, cfg, log)
        what = "started"
    player.from_the_top()
    open_page(system, page, cfg, port, log)
    return what
