"""A small MusicBrainz web service client that keeps to MusicBrainz's rules.

MusicBrainz asks for at most one request a second from a client and a meaningful User-Agent. We
send "liner-notes/<version> ( https://github.com/jampick/liner-notes )" and never an email address.

One a second holds across processes: the time of the last request lives in a lock file under the
cache folder (musicbrainz.lock), and each request takes the lock, waits out the rest of the second
and writes the new time before it goes. The console, a job worker and a CLI run started together
still queue behind each other. Systems without fcntl (Windows) fall back to one a second per process.

A 503 (MusicBrainz's "slow down"), a 429, a 5xx or a dropped connection is retried a few times
with a growing wait (2, 4, 8, 16 s, or the server's Retry-After when it sends one, capped at a
minute), then the call raises MusicBrainzError. A 404 is an answer, not an error: get() returns None.

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
RETRIES = 4  # tries after the first
BACKOFF = 2.0  # first wait before a retry, doubled each time
RETRY_CODES = (429, 500, 502, 503, 504)

_local = threading.Lock()
_last = [0.0]


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

    def _turn(self):
        """Wait until `gap` has passed since the last request from any liner-notes process, then claim the slot."""
        with _local:
            if fcntl is None:
                wait = _last[0] + self.gap - time.time()
                if wait > 0:
                    time.sleep(min(wait, self.gap))
                _last[0] = time.time()
                return
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock, "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.seek(0)
                    try:
                        last = float(f.read().strip() or 0)
                    except ValueError:
                        last = 0.0
                    wait = last + self.gap - time.time()
                    if wait > 0:
                        time.sleep(min(wait, self.gap))  # min: a garbled or future time can't stall us
                    f.seek(0)
                    f.truncate()
                    f.write(repr(time.time()))
                    f.flush()
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)

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
                time.sleep(min(60.0, wait if wait is not None else self.backoff * 2**attempt))
        raise MusicBrainzError(f"MusicBrainz didn't answer {path} after {self.retries + 1} tries ({why})")


def get(path, **params):
    """One request with a default client. The lock file follows the cache folder at call time."""
    return Client().get(path, **params)
