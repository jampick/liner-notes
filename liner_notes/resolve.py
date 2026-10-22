"""Who recorded this, and when: MusicBrainz lookups for generated journeys.

Song ids stay our own slug ("artist--title"). An ISRC names one recording, and a song has many
(the single, the album cut, the 2009 remaster), so ISRCs are kept as a list on the song. A
MusicBrainz recording id is kept too, when we find one.

The recording comes from, in order:
  1. /ws/2/url?resource=<YouTube URL>: when MusicBrainz editors have linked that exact upload to
     a recording, this is an exact answer.
  2. The recording on the earliest release found while dating the song (below).

The year is the first release of the song's release group, not the earliest year among loose
recording matches. A famous song has a hundred recordings, and a search's top few are mostly
compilations and remasters (Got to Be Real's top 15 ran 1996 to 2024). So:
  - a release-group search on title and artist finds the single (or EP, or an album of the same
    name), and its first-release-date counts, promo releases included;
  - a recording search (100 results) finds the album the song first came out on, counting only
    releases in a single, EP or album release group that isn't a compilation, live album, remix
    or DJ mix;
  - bootlegs, pseudo-releases, and withdrawn or cancelled releases don't count anywhere.
The earliest of those is the year, and year_source is "musicbrainz". When nothing qualifies the
year is None. Nothing here guesses.

Requests go through musicbrainz.Client: one a second across processes, a few retries, then an error.
"""

import unicodedata

from . import musicbrainz
from .ids import clean_title, key
from .musicbrainz import quote

KINDS = {"single", "ep", "album"}
NOT_ORIGINAL = {"compilation", "live", "remix", "dj-mix", "mixtape/street", "demo", "interview", "spokenword", "audiobook"}
NOT_RELEASED = {"bootleg", "pseudo-release", "withdrawn", "cancelled"}


def mb(path, **params):
    return musicbrainz.get(path, **params)


def _k(s):
    """key(s), or for a name with no Latin letters, its letters and digits casefolded."""
    return key(s) or "".join(c for c in unicodedata.normalize("NFKC", s or "").casefold() if c.isalnum())


def _same_title(a, b):
    ka = _k(clean_title(a))
    return bool(ka) and ka == _k(clean_title(b))


def _credit(ac):
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in ac or [])


def _same_artist(artist, ac):
    ka, kc = _k(artist), _k(_credit(ac))
    return bool(ka and kc) and (ka in kc or kc in ka)


def _original_group(rg):
    """A single, EP or album that isn't a compilation, live album, remix or DJ mix."""
    rg = rg or {}
    if (rg.get("primary-type") or "").lower() not in KINDS:
        return False
    return not {x.lower() for x in rg.get("secondary-types") or []} & NOT_ORIGINAL


def _released(r):
    return (r.get("status") or "").lower() not in NOT_RELEASED


def _year(date):
    return int(date[:4]) if date and date[:4].isdigit() and int(date[:4]) > 0 else None


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


def from_release_groups(artist, title):
    """[(year, release group id)] for release groups named like the song, by the artist."""
    d = mb("release-group", query=f"releasegroup:{quote(clean_title(title))} AND artist:{quote(artist)}", limit=25)
    out = []
    for rg in (d or {}).get("release-groups") or []:
        if not (_same_title(rg.get("title", ""), title) and _same_artist(artist, rg.get("artist-credit")) and _original_group(rg)):
            continue
        if rg.get("releases") and not any(_released(r) for r in rg["releases"]):
            continue
        y = _year(rg.get("first-release-date"))
        if y:
            out.append((y, rg["id"]))
    return out


def from_recordings(artist, title):
    """[(year, release group id, recording)] for each qualifying release of a matching recording."""
    d = mb("recording", query=f"recording:{quote(clean_title(title))} AND artist:{quote(artist)}", limit=100)
    out = []
    for rec in (d or {}).get("recordings") or []:
        if not (_same_title(rec.get("title", ""), title) and _same_artist(artist, rec.get("artist-credit"))):
            continue
        for r in rec.get("releases") or []:
            rg = r.get("release-group") or {}
            y = _year(r.get("date"))
            if y and _released(r) and _original_group(rg):
                out.append((y, rg.get("id"), rec))
    return out


def dating(artist, title):
    """{year, release_group, recording} for the song's first release, or {} when nothing qualifies."""
    recs = sorted(from_recordings(artist, title), key=lambda x: x[0])
    found = [(y, g) for y, g in from_release_groups(artist, title)] + [(y, g) for y, g, _r in recs]
    if not found:
        return {}
    year, group = min(found, key=lambda x: x[0])
    # The recording on that release group if the search saw one, else the earliest one it saw.
    rec = next((r for _y, g, r in recs if g == group), recs[0][2] if recs else None)
    return {"year": year, "release_group": group, "recording": rec}


def resolve(artist, title, youtube=None):
    """{year, year_source, release_group, mbid, isrc: [...]} as far as MusicBrainz knows.

    year and year_source are None when MusicBrainz has no qualifying release."""
    rec = None
    if youtube:
        mbid = by_url(f"https://www.youtube.com/watch?v={youtube}")
        rec = recording(mbid) if mbid else None
    dated = dating(artist, title)
    if rec is None and dated.get("recording"):
        rec = dated["recording"]
        if not rec.get("isrcs"):
            rec = recording(rec["id"]) or rec
    out = {"year": dated.get("year"), "year_source": "musicbrainz" if dated.get("year") else None, "isrc": []}
    if dated.get("release_group"):
        out["release_group"] = dated["release_group"]
    if rec:
        out["mbid"] = rec["id"]
        out["isrc"] = sorted(set(rec.get("isrcs") or []))
    return out
