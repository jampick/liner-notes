"""A Last.fm user's now-playing scrobble: the fallback for anything that scrobbles and nothing else.

There's no position, so the stage shows the song's notes in order without timing them to the
music. Settings: LINER_NOTES_LASTFM_USER and LINER_NOTES_LASTFM_KEY (an API key from
https://www.last.fm/api/account/create). It polls every 15 seconds, well under Last.fm's limits.
"""

import json
import os
import time
import urllib.parse
import urllib.request

from .. import USER_AGENT
from . import Adapter, NowPlaying

API = "https://ws.audioscrobbler.com/2.0/"


def parse(doc):
    tracks = ((doc or {}).get("recenttracks") or {}).get("track") or []
    if isinstance(tracks, dict):
        tracks = [tracks]
    t = next((t for t in tracks if (t.get("@attr") or {}).get("nowplaying") == "true"), None)
    if not t:
        return None
    ids = {}
    if t.get("mbid"):
        ids["mbid"] = t["mbid"]
    if t.get("url"):
        ids["lastfm_url"] = t["url"]
    return NowPlaying(
        title=t.get("name") or "",
        artist=(t.get("artist") or {}).get("#text", ""),
        album=(t.get("album") or {}).get("#text", ""),
        state="playing",
        source="lastfm",
        ids=ids,
    )


class LastfmAdapter(Adapter):
    name = "lastfm"
    capabilities = frozenset()

    def __init__(self, user=None, key=None):
        self.user = user or os.environ.get("LINER_NOTES_LASTFM_USER")
        self.key = key or os.environ.get("LINER_NOTES_LASTFM_KEY")
        if not (self.user and self.key):
            raise TypeError("Last.fm needs a user and an API key")

    def available(self):
        return True

    def now(self):
        q = urllib.parse.urlencode({"method": "user.getrecenttracks", "user": self.user, "api_key": self.key, "format": "json", "limit": 1})
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{API}?{q}", headers={"User-Agent": USER_AGENT}), timeout=15) as r:
                n = parse(json.loads(r.read()))
        except (OSError, ValueError):
            return None
        if n:
            n.position_at = time.time()
        return n

    def watch(self, interval=15.0):
        yield from super().watch(interval)
