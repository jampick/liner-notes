"""Where now-playing comes from. Every player sits behind the same small interface.

An adapter answers four questions, all optional except the first:

    now()        -> NowPlaying | None      what's playing right now (required)
    watch()      -> iterator of NowPlaying | None, one per change; blocks; returns when the player goes away
    command(op)  -> bool                    toggle, next, prev (capability "control")
    queue(), jump(index), lyrics(), history()   cliamp-only extras (capabilities "jump", "lyrics", "history", "bands")

NowPlaying is the one shape the rest of Liner Notes sees. `position` was true at `position_at`
(Unix seconds); position_now() extrapolates while playing, because most players don't announce
position ticks. `ids` holds whatever identifies the recording: youtube, spotify, path, url, isrc,
mbid, plus the player's own track id.

Adapters, in the order `auto` tries them:
    cliamp         V2 IPC over its Unix socket (events, control, jump, lyrics, history, bands)
    mpris          Linux D-Bus, any MPRIS player (Spotify, mpv, browsers, cliamp too), via busctl
    macos          `media-control stream` (ungive/media-control); documented stub
    smtc           Windows System Media Transport Controls through a helper; documented stub
    homeassistant  a Home Assistant media_player over its WebSocket API (Sonos, Cast, Apple TV, ...)
    lastfm         a Last.fm user's now-playing scrobble; no position, last resort
"""

import time
from dataclasses import asdict, dataclass, field


@dataclass
class NowPlaying:
    title: str
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    position: float = 0.0
    position_at: float = 0.0
    state: str = "playing"  # playing | paused | stopped
    source: str = ""
    ids: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)  # player details that don't identify the track (cliamp's playlist revision)

    def position_now(self, now=None):
        if self.state != "playing" or not self.position_at:
            return self.position
        p = self.position + ((now or time.time()) - self.position_at)
        return min(p, self.duration) if self.duration else p

    def as_track(self):
        """The cliamp-style track dict the matcher reads."""
        return {
            "title": self.title,
            "artist": self.artist,
            "path": self.ids.get("path") or self.ids.get("url") or "",
            "provider_meta": {k: v for k, v in self.ids.items() if k not in ("path", "url")},
        }

    def key(self):
        return (self.title, self.artist, self.ids.get("path") or self.ids.get("url"), self.state)

    def track_key(self):
        """Which recording, whatever the play state."""
        return self.key()[:3]

    def to_dict(self):
        return asdict(self)


class Adapter:
    name = ""
    capabilities = frozenset()
    auto = False

    def available(self):
        return False

    def now(self):
        raise NotImplementedError

    def watch(self, interval=2.0):
        """Default: poll now() and yield on change. Adapters with a push channel override this."""
        last = object()
        while self.available():
            n = self.now()
            k = n.key() if n else None
            if k != last:
                last = k
                yield n
            time.sleep(interval)

    def command(self, op):
        return False

    def queue(self):
        return []

    def jump(self, index):
        return False

    def lyrics(self):
        return None

    def history(self, limit=200):
        return []


def registry():
    from . import cliamp, homeassistant, lastfm, macos, mpris, smtc

    return {
        "cliamp": cliamp.CliampAdapter,
        "mpris": mpris.MprisAdapter,
        "macos": macos.MacAdapter,
        "smtc": smtc.SmtcAdapter,
        "homeassistant": homeassistant.HomeAssistantAdapter,
        "lastfm": lastfm.LastfmAdapter,
    }


def pick(name="auto", **opts):
    """An adapter by name, or the first available one in registry order.

    An adapter picked automatically carries auto = True, and the bridge keeps looking: it moves
    to cliamp as soon as cliamp's socket appears, and off a player that went away."""
    reg = registry()
    if name != "auto":
        return reg[name](**opts)
    for cls in reg.values():
        try:
            a = cls(**opts)
        except TypeError:
            continue  # needs settings (a token, a user name) that auto can't guess
        if a.available():
            a.auto = True
            return a
    a = reg["cliamp"]()
    a.auto = True
    return a
