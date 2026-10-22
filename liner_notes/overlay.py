"""Overlays: your copies of shipped songs and journeys, kept in user data.

The tool never writes into the shipped bank. A review, or `generate --write` filling in a shipped
song's notes, writes a copy to user data, and the bank reads that copy over the shipped one by id.
The copy records `base`, the version it started from: the package version and the SHA-256 of the
shipped file's bytes. That lets `doctor` say when the shipped file has changed since (a fix you
can't see, because your copy hides it), and tell a copy apart from a song of your own that happens
to share an id with a shipped one. Nothing here merges; it reports.

A review in an overlay counts as yours alone. Only the maintainer marks a shipped song reviewed,
in a git checkout (`review --shipped`).
"""

import hashlib
import json
from pathlib import Path

from . import __version__, paths
from .bank import read_json


def sha256(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def shipped_song(sid):
    f = paths.home() / "songs" / f"{sid}.json"
    return f if f.is_file() else None


def base_of(shipped_file):
    return {"version": __version__, "sha256": sha256(shipped_file)}


def copy_for_writing(sid, bank):
    """(path in user data, song dict) for changing a song without touching the shipped file.

    An existing copy in user data is reused with its base. A shipped song is copied with a new base.
    """
    f = paths.user_data() / "songs" / f"{sid}.json"
    if f.is_file():
        return f, read_json(f)
    song = json.loads(json.dumps(bank.songs[sid]))
    src = shipped_song(sid)
    if src and bank.song_files.get(sid) == src:
        song["base"] = base_of(src)
    return f, song


def write(path, obj):
    paths.atomic_write(path, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def _report(kind, mine, shipped, ident):
    """(kind, id, problem) for one overlay file against the shipped one, or None."""
    try:
        doc = read_json(mine)
    except (OSError, ValueError):
        return None
    base = doc.get("base") if isinstance(doc, dict) else None
    if shipped.is_file():
        if not base:
            return (kind, ident, "collision")
        if base.get("sha256") != sha256(shipped):
            return (kind, ident, "stale")
    elif base:
        return (kind, ident, "gone")
    return None


def report():
    """Every overlay that needs a person: [(kind, id, 'stale' | 'collision' | 'gone')]."""
    mine, ship = paths.user_data(), paths.home()
    if mine == ship:
        return []
    out = []
    for f in sorted((mine / "songs").glob("*.json")):
        out.append(_report("song", f, ship / "songs" / f.name, f.stem))
    for f in sorted((mine / "journeys").glob("*/journey.json")):
        out.append(_report("journey", f, ship / "journeys" / f.parent.name / "journey.json", f.parent.name))
    return [r for r in out if r]


WHY = {
    "stale": "stale: your copy started from an older shipped version, and the shipped file has changed since. "
    "Your copy hides the change; compare them and keep what you want",
    "collision": "collision: yours has the same id as a shipped one and no base, so yours hides the shipped one",
    "gone": "the shipped version your copy started from isn't shipped any more",
}
