"""Timed lyrics for the playing track, from the best place that has them.

  1. retimed     user_data()/lyrics/<youtube id>.json (a lyric_align run against that upload),
                 then <your pilot checkout>/lyrics/<id>.json when LINER_NOTES_PILOT is set. Read when they change.
  2. cliamp      cliamp's own `lyrics` operation, when its lines are timed.
  3. lrclib      LRCLIB's search, the synced copy closest to the track's length
                 (LINER_NOTES_LRCLIB_URL points it elsewhere, for tests).

cliamp's operation answers for whatever is playing when it finishes, which can be up to 20 s
later. So the playing track is read again after it, and an answer that may belong to another
track is thrown away rather than cached (the bug where song A kept showing song B's lyrics).
The playing track is also read every quarter second while cliamp works, so a change and a change
back during the lookup is caught too. One lookup runs per play of a track however many pages ask,
and a miss is remembered for `ttl` seconds (or until the track plays again).
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path

from .. import USER_AGENT, paths
from ..ids import clean_artist, clean_title, split_upload_title
from . import contract

LRCLIB = "https://lrclib.net"
LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)")


def retimed_dirs():
    pilot = paths.pilot()
    return [paths.user_data() / "lyrics", *([pilot / "lyrics"] if pilot else [])]


def lines_out(rows):
    """Lines as the page reads them: `start` (what the pilot page uses) and `t` (the contract), both in seconds."""
    out = []
    for r in rows or []:
        try:
            out.append(contract.lyric_line(r.get("start") or 0, r.get("text")))
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def synced(lines):
    """Plain lyrics come back with every line at 0:00, which would freeze the page on the last line."""
    return bool(lines) and sum(1 for ln in lines if ln["start"] > 0) >= max(2, len(lines) // 2)


def parse_lrc(text):
    out = []
    for raw in (text or "").splitlines():
        m = LRC_LINE.match(raw)
        if m:
            out.append({"start": int(m.group(1)) * 60 + float(m.group(2)), "text": m.group(3)})
    return lines_out(out)


def lrclib(artist, title, duration, base=None, timeout=8):
    base = (base or os.environ.get("LINER_NOTES_LRCLIB_URL") or LRCLIB).rstrip("/")
    q = urllib.parse.urlencode({"artist_name": artist or "", "track_name": title or ""})
    try:
        req = urllib.request.Request(f"{base}/api/search?{q}", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rows = [x for x in json.load(r) if isinstance(x, dict) and x.get("syncedLyrics")]
    except Exception:
        return None
    if not rows:
        return None
    best = min(rows, key=lambda x: abs((x.get("duration") or 0) - (duration or 0)))
    return parse_lrc(best["syncedLyrics"]) or None


def names(np, song=None):
    """(artist, title) to search by: the bank's when the song is known, else the upload's, cleaned."""
    if song:
        return song.get("artist") or "", song.get("title") or ""
    t, a = split_upload_title(np.title, np.artist)
    return clean_artist(a), clean_title(t)


class Lyrics:
    CLIAMP_TIMEOUT, LRCLIB_TIMEOUT = 20.0, 8.0

    def __init__(self, adapter, current, ttl=180.0, size=256, wait=None, dirs=None, lrclib_url=None, watch_every=0.25):
        """adapter() is the player adapter now; current() is (NowPlaying or None, generation), read fresh."""
        # A page waiting on another page's lookup waits longer than that lookup can take.
        wait = wait if wait is not None else self.CLIAMP_TIMEOUT + self.LRCLIB_TIMEOUT + 7
        self.adapter, self.current, self.ttl, self.size, self.wait = adapter, current, ttl, size, wait
        self.watch_every = watch_every
        self.dirs, self.lrclib_url = dirs, lrclib_url
        self.lock = threading.Lock()
        self.cache = OrderedDict()  # track key -> (expires or None, lines or None, source)
        self.files = OrderedDict()  # path -> ((mtime, size), lines)
        self.inflight = {}

    def retimed(self, yt):
        if not yt or not re.fullmatch(r"[\w-]{11}", yt):
            return None
        for d in self.dirs or retimed_dirs():
            f = Path(d) / f"{yt}.json"
            try:
                st = f.stat()
            except OSError:
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            with self.lock:
                hit = self.files.get(str(f))
            if hit and hit[0] == stamp:
                return hit[1]
            try:
                lines = lines_out(json.loads(f.read_text()).get("lines"))
            except (OSError, ValueError, AttributeError):
                continue
            with self.lock:
                self.files[str(f)] = (stamp, lines)
                while len(self.files) > self.size:
                    self.files.popitem(last=False)
            return lines
        return None

    def _cached(self, key):
        hit = self.cache.get(key)
        if hit is None:
            return None
        expires, lines, source = hit
        if expires is not None and time.monotonic() > expires:
            del self.cache[key]
            return None
        self.cache.move_to_end(key)
        return {"ok": bool(lines), "lines": lines or [], "source": source}

    def get(self, np, gen, song=None):
        """{ok, lines, source} for np, which the caller has checked is the playing track."""
        lines = self.retimed(np.ids.get("youtube"))
        if lines:
            return {"ok": True, "lines": lines, "source": "retimed"}
        key = (np.track_key(), gen)  # one entry per time the track starts, so a bad answer can't outlive that play
        with self.lock:
            hit = self._cached(key)
            if hit is not None:
                return hit
            ev = self.inflight.get(key)
            leader = ev is None
            if leader:
                ev = self.inflight[key] = threading.Event()
        if not leader:
            ev.wait(self.wait)
            with self.lock:
                return self._cached(key) or {"ok": False, "lines": [], "source": None}
        found = None
        try:
            found = self._lookup(np, gen, song)
        finally:
            with self.lock:
                if found is not None:
                    lines, source = found
                    self.cache[key] = (None if lines else time.monotonic() + self.ttl, lines, source)
                    while len(self.cache) > self.size:
                        self.cache.popitem(last=False)
                self.inflight.pop(key, None)
            ev.set()
        if found is None:
            return {"ok": False, "lines": [], "source": None}
        return {"ok": bool(found[0]), "lines": found[0] or [], "source": found[1]}

    def _still(self, np, gen):
        try:
            cur, g = self.current()
        except Exception:
            return False
        return cur is not None and g == gen and cur.track_key() == np.track_key()

    def _cliamp(self, ad, np, gen):
        """cliamp's answer and whether it can be trusted: the playing track is read every
        watch_every seconds while cliamp works, so an A to B to A change in between is seen."""
        box = {}

        def ask():
            try:
                box["res"] = ad.lyrics()
            except Exception:
                box["res"] = None

        t = threading.Thread(target=ask, name="liner-notes-lyrics", daemon=True)
        t.start()
        still = True
        while True:
            t.join(self.watch_every)
            if not self._still(np, gen):
                still = False
            if not t.is_alive():
                return box.get("res"), still

    def _lookup(self, np, gen, song):
        """(lines or None, source), or None when the answer can't be trusted and shouldn't be cached."""
        trusted = True
        ad = self.adapter()
        if "lyrics" in ad.capabilities:
            res, trusted = self._cliamp(ad, np, gen)
            if trusted and res and res.get("ok"):
                lines = lines_out(res.get("lyrics"))
                if synced(lines):
                    return lines, "cliamp"
        artist, title = names(np, song)
        lines = lrclib(artist, title, np.duration, self.lrclib_url, timeout=self.LRCLIB_TIMEOUT)
        if lines:
            return lines, "lrclib"
        return (None, None) if trusted else None
