"""`liner-notes check`: validate the bank and the journeys, and say what still needs a person.

The schemas in schemas/ are JSON Schema (2020-12). The validator here covers the part of it they
use (type, required, properties, items, enum, pattern, minimum, minItems, minLength) so `check`
needs nothing outside the standard library. It also checks what a schema can't: every journey
song exists, threads point at songs in the journey, era photographs exist, our own text has no
em dashes, and every written song lists sources.
"""
import json
import re

from . import paths
from .bank import Bank, read_json

TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}


def validate(doc, schema, at="$"):
    errs = []
    t = schema.get("type")
    if t:
        ok = any(isinstance(doc, TYPES[x]) and not (x in ("integer", "number") and isinstance(doc, bool)) for x in (t if isinstance(t, list) else [t]))
        if not ok:
            return [f"{at}: expected {t}"]
    if "enum" in schema and doc not in schema["enum"]:
        errs.append(f"{at}: {doc!r} not one of {schema['enum']}")
    if isinstance(doc, str):
        if "pattern" in schema and not re.search(schema["pattern"], doc):
            errs.append(f"{at}: {doc!r} doesn't match {schema['pattern']}")
        if len(doc) < schema.get("minLength", 0):
            errs.append(f"{at}: empty")
    if isinstance(doc, (int, float)) and "minimum" in schema and doc < schema["minimum"]:
        errs.append(f"{at}: below {schema['minimum']}")
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            if k not in doc:
                errs.append(f"{at}: missing {k}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in doc:
                errs += validate(doc[k], sub, f"{at}.{k}")
    if isinstance(doc, list):
        if len(doc) < schema.get("minItems", 0):
            errs.append(f"{at}: needs at least {schema['minItems']}")
        if "items" in schema:
            for i, x in enumerate(doc):
                errs += validate(x, schema["items"], f"{at}[{i}]")
    return errs


def schemas():
    d = paths.home() / "schemas"
    return {n: read_json(d / f"{n}.schema.json") for n in ("song", "journey", "remix-journey", "culture-year")}


def check(log=print):
    sc = schemas()
    bank = Bank()
    problems, notes = [], []
    for sid, s in bank.songs.items():
        f = bank.song_files[sid]
        problems += [f"{f.name}: {e}" for e in validate(s, sc["song"])]
        if f.stem != sid:
            problems.append(f"{f.name}: id {sid!r} doesn't match the file name")
        n = s.get("notes") or {}
        if n.get("made") and not n.get("sources"):
            problems.append(f"{f.name}: has notes but no sources")
        if "—" in json.dumps(n, ensure_ascii=False):
            problems.append(f"{f.name}: em dash in the notes")
    for r in bank.roots:
        for f in sorted((r / "culture/years").glob("*.json")):
            problems += [f"{f.name}: {e}" for e in validate(read_json(f), sc["culture-year"])]
    for slug, j in bank.journeys.items():
        jdir = bank.journey_dirs[slug]
        if j.get("kind") == "remix":
            problems += [f"{slug}: {e}" for e in validate(j, sc["remix-journey"])]
            for r in j.get("remixes") or []:
                sid = (r.get("original") or {}).get("song")
                if sid and sid not in bank.songs:
                    problems.append(f"{slug}: {r['title']!r} flips {sid}, which isn't in the bank")
                if not sid and not (r.get("original") or {}).get("lyrics"):
                    problems.append(f"{slug}: {r['title']!r} names no original")
            notes.append(f"{slug}: {len(j.get('remixes') or [])} remixes")
            continue
        problems += [f"{slug}: {e}" for e in validate(j, sc["journey"])]
        songs = set(j["songs"])
        for sid in j["songs"]:
            if sid not in bank.songs:
                problems.append(f"{slug}: song {sid} isn't in the bank")
        for th in j.get("threads") or []:
            if th["from"] not in songs or th["to"] not in songs:
                problems.append(f"{slug}: thread {th['from']} -> {th['to']} leaves the journey")
        for e in j["eras"]:
            if e.get("image") and not (jdir / e["image"]).exists():
                problems.append(f"{slug}: era {e['n']} image {e['image']} is missing")
        quotes = (j.get("panel") or {}).get("quotes") or {}
        for sid in quotes:
            if sid not in songs:
                problems.append(f"{slug}: panel quote for {sid}, which isn't in the journey")
        written = [s for s in j["songs"] if (bank.songs.get(s, {}).get("notes") or {}).get("made")]
        reviewed = [s for s in j["songs"] if (bank.songs.get(s, {}).get("review") or {}).get("state") == "reviewed"]
        notes.append(f"{slug}: {len(j['songs'])} songs, {len(written)} written, {len(reviewed)} reviewed by a person")
    for n in notes:
        log(n)
    for p in problems:
        log("problem:", p)
    log(f"{len(bank.songs)} songs, {len(bank.journeys)} journeys, {len(problems)} problems")
    return 1 if problems else 0


# What must never be in the repo or in a page built for sharing: other people's pictures, lyrics
# (retimed ones included), video, and the ids that only exist to drive personal video.
PERSONAL_DIRS = ("lyrics", "remixes", "originals", "media/covers", "media/culture", "media/artists", "media/depth", "standins")
PERSONAL_FILES = ("standins.json", "culture-videos.json", "stage-cache.json", "av-latency.json")
MEDIA_EXT = (".mp4", ".webm", ".m4a", ".opus", ".mp3", ".mkv", ".aac", ".flac")
JPEG_URI = "data:image/" + "jpeg"  # split, so this file doesn't trip its own check


def tree_files(root):
    """Files git would commit: tracked plus untracked-but-not-ignored. Falls back to a walk without git."""
    import subprocess

    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, check=True)
        names = [n for n in r.stdout.decode().split("\0") if n]
        return [root / n for n in names if (root / n).is_file()]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", ".venv", "node_modules", "__pycache__", "dist", ".pytest_cache", ".ruff_cache"}
        return [p for p in root.rglob("*") if p.is_file() and not skip & set(p.relative_to(root).parts)]


def _lyric_doc(p):
    try:
        d = read_json(p)
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    lines = d.get("lines") if isinstance(d, dict) else None
    return isinstance(lines, list) and any(isinstance(x, dict) and "text" in x for x in lines)


def scan_tree(root):
    """Problems with what the repo would publish."""
    from pathlib import Path

    root = Path(root)
    out = []
    for p in tree_files(root):
        rel = p.relative_to(root).as_posix()
        parts = "/" + rel
        if any(f"/{d}/" in parts for d in PERSONAL_DIRS):
            out.append(f"{rel}: personal data (covers, pictures, lyrics, stand-ins) never goes in the repo")
        elif p.name in PERSONAL_FILES or p.suffix.lower() in MEDIA_EXT:
            out.append(f"{rel}: personal media or video ids")
        elif p.suffix == ".json" and _lyric_doc(p):
            out.append(f"{rel}: looks like a lyrics file")
        elif p.suffix in (".html", ".json", ".js", ".md", ".css", ".txt", ".py") and p.stat().st_size < 50_000_000:
            try:
                if JPEG_URI in p.read_text(errors="ignore"):
                    out.append(f"{rel}: has an inline JPEG (covers are JPEG; our own pictures are WebP)")
            except OSError:
                pass
    return out


def scan_page(html, journey=None, bank=None):
    """Problems with a page built for sharing."""
    out = []
    if JPEG_URI in html:
        out.append("an inline JPEG picture")
    if re.search(r"/v/[\w-]{11}\.mp4|\.mp4\b", html):
        out.append("a video path")
    if re.search(r'"(lines|lyrics)":\s*\[\s*\{', html):
        out.append("lyric lines")
    m = re.search(r"window\.JOURNEY = (.*?);</script>", html, re.S)
    data = json.loads(m.group(1)) if m else {}
    for s in data.get("stops") or []:
        cover = s.get("cover") or ""
        if cover and not cover.startswith("data:image/svg+xml"):
            out.append(f"a cover for {s.get('title')!r}")
        if s.get("yt") or "youtube.com/watch" in (s.get("youtube") or ""):
            out.append(f"a video id for {s.get('title')!r}")
    if journey and bank:
        for sid in journey.get("songs") or []:
            ids = set(((bank.songs.get(sid) or {}).get("aliases") or {}).get("youtube") or [])
            ids.add(((journey.get("pins") or {}).get(sid) or {}).get("youtube") or "")
            for yt in ids - {""}:
                if yt in html:
                    out.append(f"video id {yt}")
    return out


def publish(log=print):
    """`liner-notes check --publish`: the repo tree, then a public build of every journey."""
    import tempfile
    from pathlib import Path

    from .build import Refused, build

    problems = [f"tree: {p}" for p in scan_tree(paths.home())]
    bank = Bank()
    with tempfile.TemporaryDirectory() as d:
        for slug, j in sorted(bank.journeys.items()):
            if j.get("kind") == "remix":
                continue
            out = Path(d) / f"{slug}.html"
            try:
                build(slug, out=out, public=True, bank=bank, force=True, log=lambda *a: None)
            except Refused as e:
                problems.append(f"{slug}: {e}")
                continue
            problems += [f"{slug} public build: {p}" for p in scan_page(out.read_text(), j, bank)]
    for p in problems:
        log("problem:", p)
    log(f"publish check: {len(problems)} problems")
    return 1 if problems else 0
