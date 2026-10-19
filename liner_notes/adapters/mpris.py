"""Any MPRIS player on Linux (Spotify, mpv, Strawberry, browsers, cliamp itself), through busctl.

busctl ships with systemd, so this stays stdlib. MPRIS has no position ticks: Position (in
microseconds) is read when something changes and extrapolated in between, and a Seeked signal
means "read it again".

Which player: LINER_NOTES_MPRIS (a bus name suffix such as "spotify"), else the one that is
playing, preferring a dedicated player over a browser.
"""
import os
import select
import shutil
import subprocess
import time
import urllib.parse

from ..ids import youtube_id
from . import Adapter, NowPlaying

PREFIX = "org.mpris.MediaPlayer2."
OBJ = "/org/mpris/MediaPlayer2"
IFACE = "org.mpris.MediaPlayer2.Player"
BROWSERS = ("chromium", "chrome", "firefox", "brave", "vivaldi", "edge", "opera", "plasma-browser-integration")


def _busctl(*args, timeout=3):
    try:
        r = subprocess.run(["busctl", "--user", "--json=short", *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        import json

        return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def players():
    out = _busctl("list")
    if not out:
        return []
    rows = out[0] if isinstance(out[0], list) else out
    return [r["name"] for r in rows if str(r.get("name", "")).startswith(PREFIX)]


def parse_metadata(meta, status="Playing", position_us=0, bus=""):
    """busctl's JSON for the Metadata a{sv} (plus status and position) to NowPlaying."""
    d = (meta or {}).get("data") or {}

    def v(k, default=None):
        return (d.get(k) or {}).get("data", default)

    artist = v("xesam:artist") or []
    url = v("xesam:url") or ""
    ids = {}
    if url.startswith("file://"):
        ids["path"] = urllib.parse.unquote(url[7:])
    elif url:
        ids["url"] = url
        if youtube_id(url):
            ids["youtube"] = youtube_id(url)
    trackid = v("mpris:trackid") or ""
    if trackid:
        ids["mpris_trackid"] = trackid
    if "/com/spotify/track/" in trackid:
        ids["spotify_id"] = trackid.rsplit("/", 1)[1]
    elif url.startswith("https://open.spotify.com/track/"):
        ids["spotify_id"] = url.rsplit("/", 1)[1].split("?")[0]
    if bus:
        ids["mpris_player"] = bus[len(PREFIX):]
    title = v("xesam:title") or ""
    if not title:
        return None
    return NowPlaying(title=title, artist=", ".join(artist) if isinstance(artist, list) else str(artist), album=v("xesam:album") or "",
                      duration=(v("mpris:length") or 0) / 1e6, position=(position_us or 0) / 1e6, position_at=time.time(),
                      state={"Playing": "playing", "Paused": "paused"}.get(status, "stopped"), source="mpris", ids=ids)


class MprisAdapter(Adapter):
    name = "mpris"
    capabilities = frozenset({"events", "control"})

    def __init__(self, player=None):
        self.want = player or os.environ.get("LINER_NOTES_MPRIS")
        self.bus = None

    def available(self):
        return bool(shutil.which("busctl")) and bool(self._choose())

    def _get(self, bus, prop):
        out = _busctl("get-property", bus, OBJ, IFACE, prop)
        return out[0] if out else None

    def _choose(self):
        names = players()
        if self.want:
            names = [n for n in names if n[len(PREFIX):].startswith(self.want)]
        if not names:
            return None
        if self.bus in names and len(names) == 1:
            return self.bus

        def rank(n):
            st = (self._get(n, "PlaybackStatus") or {}).get("data")
            return (st != "Playing", any(b in n.lower() for b in BROWSERS), n)

        self.bus = sorted(names, key=rank)[0]
        return self.bus

    def now(self):
        bus = self._choose()
        if not bus:
            return None
        return parse_metadata(self._get(bus, "Metadata"), (self._get(bus, "PlaybackStatus") or {}).get("data", "Stopped"),
                              (self._get(bus, "Position") or {}).get("data", 0), bus)

    def watch(self, interval=5.0):
        """Re-read on any PropertiesChanged or Seeked from an MPRIS object, and every few seconds anyway."""
        try:
            mon = subprocess.Popen(["busctl", "--user", "--json=short", "monitor", f"--match=type='signal',path='{OBJ}'"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            yield from super().watch(interval)
            return
        try:
            while self.available():
                yield self.now()  # every wake, since a Seeked moves the position without changing the song; consumers dedupe
                r, _, _ = select.select([mon.stdout], [], [], interval)
                if r and not mon.stdout.readline():
                    break
                time.sleep(0.05)
        finally:
            mon.kill()

    def command(self, op):
        method = {"toggle": "PlayPause", "next": "Next", "prev": "Previous"}.get(op)
        bus = self._choose()
        if not (method and bus):
            return False
        return subprocess.run(["busctl", "--user", "call", bus, OBJ, IFACE, method], capture_output=True, timeout=3).returncode == 0
