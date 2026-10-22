"""Your copies of shipped songs and journeys, kept in user data (overlays, in the code).

The tool never writes into the shipped bank. A review, or `generate --write` filling in a shipped
song's notes, writes a copy to user data, and the bank reads that copy over the shipped one by id.
The copy records `base`, the version it started from: the package version and the SHA-256 of the
shipped file's bytes. That lets `doctor` say when the shipped file has changed since (a fix you
can't see, because your copy hides it), and tell a copy apart from a song of your own that happens
to share an id with a shipped one. Nothing here merges; it reports, and `liner-notes drop` removes
a copy.

On screen these are "your copy"; "overlay" and "base" stay in the code.

A review in a copy counts as yours alone. Only the maintainer marks a shipped song reviewed, in a
git checkout (`review --shipped`).
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


def copy_path(sid):
    return paths.user_data() / "songs" / f"{sid}.json"


def copy_for_writing(sid, bank):
    """(path in user data, song dict) for changing a song without touching the shipped file.

    An existing copy in user data is reused with its base. A shipped song is copied with a new base.
    """
    f = copy_path(sid)
    if f.is_file():
        return f, read_json(f)
    song = json.loads(json.dumps(bank.songs[sid]))
    src = shipped_song(sid)
    if src and bank.song_files.get(sid) == src:
        song["base"] = base_of(src)
    return f, song


def only_review_differs(copy, shipped_file):
    """True when a copy is the shipped song plus a review, nothing else."""

    def strip(d):
        return {k: v for k, v in d.items() if k not in ("review", "base")}

    return bool(shipped_file) and strip(copy) == strip(read_json(shipped_file))


def write(path, obj):
    paths.atomic_write(path, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


def _copies():
    """[(kind, id, your file, the shipped file of the same id)] for every copy in user data."""
    mine, ship = paths.user_data(), paths.home()
    if mine == ship:
        return []
    out = [("song", f.stem, f, ship / "songs" / f.name) for f in sorted((mine / "songs").glob("*.json"))]
    out += [
        ("journey", f.parent.name, f, ship / "journeys" / f.parent.name / "journey.json")
        for f in sorted((mine / "journeys").glob("*/journey.json"))
    ]
    return out


def _state(mine, shipped):
    try:
        doc = read_json(mine)
    except (OSError, ValueError):
        return None
    base = doc.get("base") if isinstance(doc, dict) else None
    if shipped.is_file():
        if not base:
            return "collision"
        if base.get("sha256") != sha256(shipped):
            return "stale"
    elif base:
        return "gone"
    return None


def report():
    """Every copy that needs a person: [(kind, id, 'stale' | 'collision' | 'gone', your file, shipped file)]."""
    out = []
    for kind, ident, mine, shipped in _copies():
        what = _state(mine, shipped)
        if what:
            out.append((kind, ident, what, mine, shipped))
    return out


def describe(kind, ident, what, mine, shipped):
    drop = f"liner-notes drop {ident}"
    if what == "stale":
        return (
            f"{ident}: stale. The shipped {kind} changed after you copied it, and your copy hides the change. "
            f"Compare {mine} with {shipped}, or drop yours with {drop}"
        )
    if what == "collision":
        return f"{ident}: same id as a shipped {kind}, so yours ({mine}) hides it. Rename yours, or drop it with {drop}"
    return (
        f"{ident}: gone. The shipped {kind} it came from isn't shipped any more, so yours ({mine}) is the only one left. "
        f"Nothing to do, unless you'd rather drop it with {drop}"
    )


def drop(ident):
    """Remove your copy of a shipped song or journey. Returns (ok, message)."""
    for kind, f, shipped in (
        ("song", copy_path(ident), paths.home() / "songs" / f"{ident}.json"),
        ("journey", paths.user_data() / "journeys" / ident / "journey.json", paths.home() / "journeys" / ident / "journey.json"),
    ):
        if not f.is_file():
            continue
        try:
            base = read_json(f).get("base")
        except (OSError, ValueError):
            base = None
        if not base and not shipped.is_file():
            return (
                False,
                f"{ident}: {f} is a {kind} of yours alone, not a copy of a shipped one, so drop leaves it. Delete it by hand if you mean to",
            )
        f.unlink()
        if kind == "journey":
            try:
                f.parent.rmdir()
            except OSError:
                pass
        again = f"you're reading the shipped {kind} again" if shipped.is_file() else f"there's no shipped {kind} {ident} any more"
        return True, f"{ident}: dropped your copy, {f}; {again}"
    return False, f"{ident}: there's no copy of it in {paths.user_data()}"


def review_counts(song_ids, bank):
    """(reviewed in the shipped bank, reviewed only in your copies) for some songs."""
    mine = paths.user_data()
    shipped = yours = 0
    for sid in song_ids:
        if ((bank.songs.get(sid) or {}).get("review") or {}).get("state") != "reviewed":
            continue
        f = bank.song_files.get(sid)
        if f and f.is_relative_to(mine) and mine != paths.home():
            yours += 1
        else:
            shipped += 1
    return shipped, yours
