"""The song bank, the year culture bank and the journeys, read from every data root."""
import json
from pathlib import Path

from . import paths


def read_json(p):
    return json.loads(Path(p).read_text())


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


class Bank:
    def __init__(self, roots=None):
        self.roots = [Path(r) for r in (roots or paths.roots())]
        self.songs, self.song_files, self.journeys, self.journey_dirs = {}, {}, {}, {}
        for r in reversed(self.roots):  # lowest priority first, so higher roots overwrite
            for f in sorted((r / "songs").glob("*.json")):
                s = read_json(f)
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
