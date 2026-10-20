"""What's playing, which song and journey that is, and which player to ask.

Library holds the bank and the matcher and reloads them when a song or journey file changes.
Now follows the player: cliamp's event stream when cliamp is there, a slow poll otherwise, and the
Clock for the position. In auto mode it keeps looking for a better player: it moves to cliamp as
soon as cliamp's socket appears (a key that starts the bridge and cliamp together used to leave the
bridge on MPRIS for good), and off a player that went away.

Which journey a song belongs to: the loaded playlist's journey when the playlist is known (cliamp's
snapshot doesn't carry a name, so `liner-notes play` writes it to state()/playlist; an "(offline)"
twin counts as its online list), else the journey that holds most of the loaded queue, else the
first journey in the bank with the song.
"""

import logging
import os
import re
import threading
import time
from pathlib import Path

from .. import __version__, adapters, cliamp, paths
from ..bank import Bank
from ..ids import slug as slugify
from ..match import Matcher
from .clock import Clock
from .route import Latency

log = logging.getLogger("liner_notes.bridge")
OFFLINE = re.compile(r"\s*\(offline\)\s*$", re.I)


def playlist_slug(name):
    return slugify(OFFLINE.sub("", name or "")) or None


class Library:
    """The bank and matcher, reloaded when a song or journey file changes (checked every 5 s at most)."""

    def __init__(self, roots=None, every=5.0):
        self.roots, self.every = roots, every
        self.lock = threading.Lock()
        self.at, self.busy = time.monotonic(), False
        self.state = self._load()

    def _stamp(self):
        files = [p for r in (self.roots or paths.roots()) for d in ("songs", "journeys") for p in (Path(r) / d).rglob("*.json")]
        return (len(files), max((p.stat().st_mtime for p in files), default=0))

    def _load(self):
        stamp = self._stamp()
        bank = Bank(self.roots)
        return bank, Matcher(bank.songs), stamp

    def get(self):
        """(bank, matcher). Every `every` seconds one caller checks the files and, if they changed,
        builds the new bank outside the lock; everyone else keeps using the current pair meanwhile."""
        with self.lock:
            bank, matcher, stamp = self.state
            due = not self.busy and time.monotonic() - self.at > self.every
            if due:
                self.busy, self.at = True, time.monotonic()
        if due:
            try:
                if self._stamp() != stamp:
                    new = self._load()
                    with self.lock:
                        self.state = new
                    bank, matcher = new[0], new[1]
            except (OSError, ValueError) as e:
                log.warning("bank reload failed, keeping the old one: %s", e)
            finally:
                with self.lock:
                    self.busy = False
        return bank, matcher

    @property
    def bank(self):
        return self.state[0]

    def journey_for(self, song, playlist=None, hint=None):
        """The loaded playlist's journey if it has the song, then the queue's journey, then any."""
        bank, _ = self.get()
        if not song:
            return None
        for slug in (playlist_slug(playlist), hint):
            if slug and slug in bank.journeys and song in bank.journeys[slug]["songs"]:
                return slug
        have = bank.journeys_with(song)
        return have[0] if have else None

    def queue_journey(self, tracks):
        """The journey that holds the most of these tracks (at least 3, or half of a short queue); None on a tie."""
        bank, matcher = self.get()
        ids = {matcher.match(t) for t in tracks} - {None}
        counts = sorted(((len(ids & set(j["songs"])), slug) for slug, j in bank.journeys.items()), reverse=True)
        if not counts or (len(counts) > 1 and counts[0][0] == counts[1][0]):
            return None
        n, best = counts[0]
        return best if n and n >= min(3, max(1, len(tracks) // 2)) else None


class PlaylistFile:
    """state()/playlist: the name of the cliamp playlist `liner-notes play` loaded, re-read when it changes."""

    def __init__(self, path=None):
        self.path, self.stamp, self.name = path, None, None

    def get(self):
        p = Path(self.path or paths.state() / "playlist")
        try:
            st = p.stat()
        except OSError:
            return None
        if (st.st_mtime_ns, st.st_size) != self.stamp:
            self.stamp = (st.st_mtime_ns, st.st_size)
            try:
                self.name = p.read_text().strip()[:200] or None
            except OSError:
                self.name = None
        return self.name


class Now:
    """The latest now-playing, kept fresh by the adapter's events and the Clock's sampler."""

    def __init__(self, lib, adapter, latency=None, watch_every=2.0, repick_every=10.0, playlist_file=None):
        self.lib, self.adapter = lib, adapter
        self.watch_every, self.repick_every = watch_every, repick_every
        self.np, self.version, self.gen = None, 0, 0
        self.track = None
        self.cond = threading.Condition()
        self.qrev, self.qjourney, self.qbusy = None, None, False
        self.playlist_file = playlist_file or PlaylistFile()
        self.clock = Clock(self._sample, latency or Latency().get, interval=self._interval(adapter))
        self.repicked = time.monotonic()
        self.closed = threading.Event()
        threading.Thread(target=self._watch, name="liner-notes-watch", daemon=True).start()

    @staticmethod
    def _interval(adapter):
        return 0.2 if adapter.name == "cliamp" else 1.0  # other players cost a process per look

    # --- state -----------------------------------------------------------------------------
    def _update(self, np):
        with self.cond:
            track = np.track_key() if np else None
            if track != self.track:
                self.track, self.gen = track, self.gen + 1
            changed = (np.key() if np else None) != (self.np.key() if self.np else None)
            self.np = np
            if changed:
                self.version += 1
                self.cond.notify_all()
        if np and np.meta.get("playlist_revision") is not None and np.meta["playlist_revision"] != self.qrev:
            self._queue_hint(np.meta["playlist_revision"])

    def _queue_hint(self, rev):
        with self.cond:
            if self.qbusy or self.closed.is_set():
                return
            self.qbusy = True

        def work():
            try:
                self.qjourney = self.lib.queue_journey(self.adapter.queue())
            except Exception as e:
                log.debug("queue lookup failed: %s", e)
            finally:
                self.qrev, self.qbusy = rev, False

        threading.Thread(target=work, name="liner-notes-queue", daemon=True).start()

    def _sample(self):
        np = self.adapter.now()
        self._update(np)
        return np

    def fresh(self):
        """(NowPlaying or None, track generation), read from the player now."""
        try:
            np = self.adapter.now()
        except Exception:
            np = None
        self._update(np)
        with self.cond:
            return np, self.gen

    # --- following the player --------------------------------------------------------------
    def switch(self, new):
        log.info("player: %s -> %s", self.adapter.name, new.name)
        new.auto = True
        self.adapter = new
        self.clock.interval = self._interval(new)
        self.clock.poke()

    def repick(self, force=False):
        """In auto mode: cliamp as soon as its socket is there; otherwise a new pick when the player went away."""
        cur = self.adapter
        if not cur.auto:
            return
        if cur.name != "cliamp" and os.path.exists(cliamp.socket_path()):
            return self.switch(adapters.registry()["cliamp"]())
        if force or time.monotonic() - self.repicked >= self.repick_every:
            self.repicked = time.monotonic()
            try:
                if cur.available():
                    return
                new = adapters.pick()
            except Exception:
                return
            if new.name != cur.name:
                self.switch(new)

    def close(self):
        """Stop following the player (tests start many bridges in one process)."""
        self.closed.set()
        self.clock.close()

    def _watch(self):
        while not self.closed.is_set():
            ad = self.adapter
            try:
                if ad.name == "cliamp" and ad.available():
                    for np in ad.watch(timeout=60):
                        if self.closed.is_set() or self.adapter is not ad:
                            break
                        self._update(np)
                        self.clock.poke()
                else:
                    self._update(ad.now())
            except Exception:  # the player went away or isn't there yet; look again shortly
                pass
            self.repick()
            self.closed.wait(self.watch_every)

    # --- answers ---------------------------------------------------------------------------
    def payload(self):
        r = self.clock.now()
        base = {"version": __version__, "source": self.adapter.name, "can": sorted(self.adapter.capabilities)}
        if not r:
            return {"ok": False, "reason": "no-player", **base}
        np = r["np"]
        if np.state == "stopped":
            return {"ok": False, "reason": "stopped", **base, "title": np.title, "artist": np.artist}
        _, m = self.lib.get()
        song = m.match(np.as_track())
        playlist = np.ids.get("cliamp_playlist") or self.playlist_file.get()
        return {
            "ok": True,
            **base,
            "source": np.source or self.adapter.name,
            "state": np.state,
            "title": np.title,
            "artist": np.artist,
            "album": np.album,
            "yt": np.ids.get("youtube"),
            "song": song,
            "journey": self.lib.journey_for(song, playlist, self.qjourney),
            "playlist": playlist,
            "position": round(r["position"], 3),
            "duration": np.duration,
            "epoch": r["epoch"],
            "moving": r["moving"],
            "latency": r["latency"],
        }

    def wait(self, version, timeout=15):
        with self.cond:
            self.cond.wait_for(lambda: self.version != version, timeout)
            return self.version
