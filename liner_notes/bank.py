"""The song bank, the year culture bank and the journeys, read from every data root."""

import json
from pathlib import Path

from . import paths


class SchemaError(ValueError):
    """A song, journey or culture year that doesn't match its schema. Nothing was written."""


def read_json(p):
    return json.loads(Path(p).read_text())


def problems(obj, kind):
    """What's wrong with obj as a `kind` ("song", "journey" or "culture-year"), as a list of strings."""
    from .check import schemas, validate, year_problems

    if kind == "journey" and obj.get("kind") == "remix":
        kind = "remix-journey"
    errs = validate(obj, schemas()[kind])
    return errs + (year_problems(obj) if kind == "song" else [])


def write_checked(p, obj, kind):
    """The one writer for songs, journeys and culture years: the package guard first (never into an
    installed package), then the schema (and, for a song, that its file name is its id), then an
    atomic write. Raises PermissionError or SchemaError and leaves the file alone otherwise."""
    p = paths.guard(p)
    errs = problems(obj, kind)
    if kind == "song" and p.stem != obj.get("id"):
        errs.append(f"file name {p.name} isn't the id {obj.get('id')!r}")
    if errs:
        raise SchemaError(f"not writing {p}: " + "; ".join(errs))
    paths.atomic_write(p, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    return p


def write_json(p, obj):
    """Atomic, and never into an installed package (paths.guard)."""
    paths.atomic_write(p, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


class Bank:
    def __init__(self, roots=None):
        self.roots = [Path(r) for r in (roots or paths.roots())]
        self.songs, self.song_files, self.journeys, self.journey_dirs = {}, {}, {}, {}
        shipped = paths.home().resolve()
        for r in reversed(self.roots):  # lowest priority first, so higher roots overwrite
            mine = r.resolve() != shipped
            for f in sorted((r / "songs").glob("*.json")):
                s = read_json(f)
                # 0.2.0's generate wrote 0 for "no year" in your own songs. In the shipped bank a 0 is a mistake, and check says so.
                if mine and s.get("year") == 0:
                    s["year"], s["year_source"] = None, None
                self.songs[s["id"]] = s
                self.song_files[s["id"]] = f
            for f in sorted((r / "journeys").glob("*/journey.json")):
                j = read_json(f)
                self.journeys[j["slug"]] = j
                self.journey_dirs[j["slug"]] = f.parent

    def years(self, year):
        out = []
        for r in reversed(self.roots):
            f = r / "culture/years" / f"{year}.json"
            if f.exists():
                out = read_json(f).get("items", [])
        return out

    def artists(self):
        out = {}
        for r in reversed(self.roots):
            f = r / "culture/artists.json"
            if f.exists():
                out.update(read_json(f))
        return out

    def journeys_with(self, song_id):
        return [s for s, j in self.journeys.items() if song_id in j["songs"]]
