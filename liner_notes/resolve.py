"""Who recorded this, and when: MusicBrainz lookups for generated journeys.

Song ids stay our own slug ("artist--title"). An ISRC names one recording, and a song has many
(the single, the album cut, the 2009 remaster), so ISRCs are kept as a list on the song. A
MusicBrainz recording id is kept too, when we find one.

Two ways in, in order:
  1. /ws/2/url?resource=<YouTube URL>: when MusicBrainz editors have linked that exact upload to
     a recording, this is an exact answer.
  2. A recording search on artist and title, taking the earliest first-release date among
     official releases.

MusicBrainz asks for at most one request a second and a meaningful User-Agent; we send
"liner-notes/<version> ( https://github.com/jampick/liner-notes )" and never an email address.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT
from .ids import clean_title, key

API = "https://musicbrainz.org/ws/2/"
_last = [0.0]


def mb(path, **params):
    wait = 1.05 - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()
    q = urllib.parse.urlencode({**params, "fmt": "json"})
    req = urllib.request.Request(f"{API}{path}?{q}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 503:
            time.sleep(3)
            return mb(path, **params)
        raise


def by_url(url):
    """The recording MusicBrainz editors linked to this URL, or None."""
    d = mb("url", resource=url, inc="recording-rels")
    for rel in (d or {}).get("relations") or []:
        rec = rel.get("recording")
        if rec:
            return rec["id"]
    return None


def recording(mbid):
    return mb(f"recording/{mbid}", inc="isrcs+artist-credits+releases")


def year_of(rec):
    dates = [r.get("date") for r in (rec or {}).get("releases") or [] if r.get("date") and (r.get("status") in (None, "Official"))]
    d = rec.get("first-release-date") if rec else None
    dates += [d] if d else []
    ys = sorted(int(x[:4]) for x in dates if x[:4].isdigit())
    return ys[0] if ys else None


def search(artist, title):
    """Best recording for artist and title: matching title and credit, earliest release."""
    q = f'recording:"{clean_title(title)}" AND artist:"{artist}"'
    d = mb("recording", query=q, limit=15)
    best = None
    for rec in (d or {}).get("recordings") or []:
        credit = "".join(c.get("name", "") + c.get("joinphrase", "") for c in rec.get("artist-credit") or [])
        if key(clean_title(rec.get("title", ""))) != key(clean_title(title)):
            continue
        if key(artist) not in key(credit) and key(credit) not in key(artist):
            continue
        y = year_of(rec)
        if y and (best is None or y < best[0]):
            best = (y, rec)
    return best[1] if best else None


def resolve(artist, title, youtube=None):
    """{mbid, year, isrc: [...]} as far as MusicBrainz knows, or {} when it doesn't."""
    rec = None
    if youtube:
        mbid = by_url(f"https://www.youtube.com/watch?v={youtube}")
        rec = recording(mbid) if mbid else None
    if rec is None:
        rec = search(artist, title)
        if rec and not rec.get("isrcs"):
            rec = recording(rec["id"]) or rec
    if not rec:
        return {}
    return {"mbid": rec["id"], "year": year_of(rec), "isrc": sorted(set(rec.get("isrcs") or []))}
