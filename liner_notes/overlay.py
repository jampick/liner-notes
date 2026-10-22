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


def _strip(song):
    return {k: v for k, v in song.items() if k not in ("review", "base")}


def content_hash(song):
    """The song's content apart from its review and base, so a copy can tell whether it was edited."""
    return hashlib.sha256(json.dumps(_strip(song), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def base_of(shipped_file):
    """Where a copy started: the package version, the shipped file's bytes, and the song as copied."""
    return {"version": __version__, "sha256": sha256(shipped_file), "content": content_hash(read_json(shipped_file))}


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
    try:
        return bool(shipped_file) and _strip(copy) == _strip(read_json(shipped_file))
    except (OSError, ValueError):
        return False


def undo_kind(copy, shipped_file):
    """What `review --undo` may do with a copy.

    'review-only': unchanged since it was copied, or the same as the shipped song apart from the
    review, so deleting it loses nothing. 'yours': no base, a song of your own. 'unknown': copied
    before base.content existed and the shipped song has changed since, so the two can't be told
    apart. 'changed': your edits are in it.
    """
    base = copy.get("base")
    if not base:
        return "yours"
    if base.get("content") == content_hash(copy) or only_review_differs(copy, shipped_file):
        return "review-only"
    if not base.get("content") and shipped_file and base.get("sha256") != sha256(shipped_file):
        return "unknown"
    return "changed"


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


def _aside(src, rel):
    """Move src under user data's dropped/<today>/rel, never over an earlier drop. Returns where it went."""
    import datetime as dt
    import shutil

    day = paths.user_data() / "dropped" / dt.date.today().isoformat()
    dest, n = day / rel, 1
    while dest.exists():
        n += 1
        dest = day / rel.with_name(f"{rel.stem}-{n}{rel.suffix}")
    paths.guard(dest).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def drop(ident):
    """Move your copy of a shipped song or journey aside, into dropped/<date>/. Returns (ok, message)."""
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
        if kind == "journey":
            dest = _aside(f.parent, Path("journeys") / ident) / "journey.json"
        else:
            dest = _aside(f, Path("songs") / f.name)
        what = "your copy" if base else f"your own {kind} (it had the same id as a shipped one)"
        again = f"you're reading the shipped {kind} again" if shipped.is_file() else f"there's no shipped {kind} {ident} any more"
        return True, f"{ident}: moved {what} to {dest}; {again}"
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
