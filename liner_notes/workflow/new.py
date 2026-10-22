"""`liner-notes new`: start a journey in user data with its journey.toml and nothing else.

An idea gets a panel, because there's no list without one. A playlist (--from) doesn't, since a
panel arguing over a list you already chose is pretend; --panel adds one anyway.
"""

import datetime as dt
import re
import tomllib
from pathlib import Path

from ..ids import slug as slugify
from ..ids import song_id
from . import journeytoml
from .journeytoml import Problem

SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
EXTINF = re.compile(r"#EXTINF:[^,]*,(.*)")


def tracks_from(source):
    """[{title, artist}] from an .m3u or a cliamp .toml file, or a saved cliamp playlist by name."""
    from ..generate import tracks_from_toml

    f = Path(source).expanduser()
    if not f.is_file():
        return tracks_from_toml(source)
    text = f.read_text(errors="replace")
    if f.suffix.lower() == ".toml":
        try:
            return tomllib.loads(text).get("track") or []
        except tomllib.TOMLDecodeError as e:
            raise Problem(f"{f} isn't a cliamp playlist: {e}") from None
    out = []
    for line in text.splitlines():
        m = EXTINF.match(line.strip())
        if m:
            artist, _, title = m.group(1).partition(" - ")
            out.append({"artist": artist.strip(), "title": title.strip()} if title else {"artist": "", "title": artist.strip()})
    return out


def songs_of(tracks):
    """(song ids, "Artist - Title" names) in playlist order, each song once."""
    from ..generate import tidy

    ids, names = [], []
    for t in tracks:
        title, artist = tidy(t)
        sid = song_id(artist, title)
        if sid not in ids:
            ids.append(sid)
            names.append(f"{artist} - {title}" if artist else title)
    return ids, names


def new(title, slug=None, source=None, with_panel=None):
    """Write journeys/<slug>/journey.toml in user data. Returns its document."""
    from ..bank import Bank

    title = (title or "").strip()
    if not title:
        raise Problem("a journey needs a title")
    slug = slug or slugify(title)
    if not SLUG.fullmatch(slug or ""):
        raise Problem(f"--slug takes lowercase letters, digits and single hyphens, like city-pop; {slug!r} isn't one")
    if (journeytoml.folder(slug) / journeytoml.NAME).exists():
        raise Problem(f"{slug} is already in the making: liner-notes status {slug}")
    if slug in Bank().journeys:
        raise Problem(f"there's already a journey called {slug}; pick another with --slug")
    if source:
        ids, names = songs_of(tracks_from(source))
        if not ids:
            raise Problem(f"no songs in {source}")
        seed = {"from": Path(source).name, "songs": ids, "tracks": names}
    else:
        seed = {"idea": title}
    doc = {
        "schema": journeytoml.VERSION,
        "slug": slug,
        "title": title,
        "panel": bool(with_panel) if source else with_panel is not False,
        "created": dt.date.today().isoformat(),
        "seed": seed,
        "stages": {},
    }
    journeytoml.save(doc)
    return doc
