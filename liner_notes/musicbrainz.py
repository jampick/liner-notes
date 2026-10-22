"""A small MusicBrainz web service client that keeps to MusicBrainz's rules.

MusicBrainz asks for at most one request a second from a client and a meaningful User-Agent. We
send "liner-notes/<version> ( https://github.com/jampick/liner-notes )" and never an email address.

One a second holds across processes: the lock file musicbrainz.lock under the cache folder holds
the time of the last request and a "not before" time. Each request takes the lock, waits out the
rest of the second (or until "not before"), and writes the new time before it goes. The console, a
job worker and a CLI run started together still queue behind each other. Systems without fcntl
(Windows) fall back to the same rule per process.

A 503 (MusicBrainz's "slow down"), a 429, a 5xx or a dropped connection is retried a few times
with a growing wait (2, 4, 8, 16 s, or the server's Retry-After in seconds when it sends one; an
HTTP-date Retry-After falls back to the backoff), capped at a minute, then the call raises
MusicBrainzError. The wait goes into the lock file as "not before", so every process honours it,
since MusicBrainz's 503 asks the whole address to slow down, not one process.
A 404 is an answer, not an error: get() returns None.

    from liner_notes.musicbrainz import Client
    mb = Client()
    mb.get("release-group", query='releasegroup:"Got to Be Real" AND artist:"Cheryl Lynn"', limit=25)
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import USER_AGENT, paths

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

API = "https://musicbrainz.org/ws/2/"
GAP = 1.05  # seconds between requests, with a little room for clock skew
LONGEST = 60.0  # the most any one wait can be, so a garbled lock file can't stall us
RETRIES = 4  # tries after the first
BACKOFF = 2.0  # first wait before a retry, doubled each time
RETRY_CODES = (429, 500, 502, 503, 504)

_local = threading.Lock()
_last = [0.0, 0.0]  # last request, not before: the lock file's two numbers, for systems without fcntl


class MusicBrainzError(RuntimeError):
    """MusicBrainz said no, or didn't answer after every retry."""


def quote(s):
    """s as a Lucene phrase for a search query."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _retry_after(err):
    try:
        return float(err.headers.get("Retry-After"))
    except (TypeError, ValueError, AttributeError):
        return None


class Client:
    def __init__(self, api=API, lock=None, gap=GAP, retries=RETRIES, backoff=BACKOFF, timeout=20, user_agent=USER_AGENT):
        self.api, self.gap, self.retries, self.backoff, self.timeout, self.user_agent = api, gap, retries, backoff, timeout, user_agent
        self.lock = Path(lock) if lock else paths.cache() / "musicbrainz.lock"

    def _shared(self, change):
        """Run change([last, not_before]) under the lock, and save what it leaves in the list."""
        with _local:
            if fcntl is None:
                change(_last)
                return
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    try:
                        times = [float(x) for x in f.read().split()][:2]
                    except ValueError:
                        times = []
                    times += [0.0] * (2 - len(times))
                    change(times)
                    f.seek(0)
                    f.truncate()
                    f.write(f"{times[0]!r} {times[1]!r}")
                    f.flush()
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def _turn(self):
        """Wait for our slot (a gap after the last request from any process, and not before a backoff), then claim it."""

        def take(t):
            wait = max(t[0] + self.gap, t[1]) - time.time()
            if wait > 0:
                time.sleep(min(wait, LONGEST))
            t[0], t[1] = time.time(), 0.0  # the hold is served (a garbled one too, after a minute at most)

        self._shared(take)

    def _hold(self, seconds):
        """Tell every process not to ask before `seconds` from now."""

        def hold(t):
            t[1] = max(t[1], time.time() + min(seconds, LONGEST))

        self._shared(hold)

    def get(self, path, **params):
        """The JSON MusicBrainz returns for path and params, or None for a 404."""
        url = f"{self.api}{path}?{urllib.parse.urlencode({**params, 'fmt': 'json'})}"
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        why = None
        for attempt in range(self.retries + 1):
            self._turn()
            wait = None
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code not in RETRY_CODES:
                    raise MusicBrainzError(f"MusicBrainz answered {e.code} for {path}") from e
                why, wait = f"HTTP {e.code}", _retry_after(e)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                why = str(getattr(e, "reason", None) or e)
            if attempt < self.retries:
                self._hold(wait if wait is not None else self.backoff * 2**attempt)  # the next _turn waits it out
        raise MusicBrainzError(f"MusicBrainz didn't answer {path} after {self.retries + 1} tries ({why})")


def get(path, **params):
    """One request with a default client. The lock file follows the cache folder at call time."""
    return Client().get(path, **params)
