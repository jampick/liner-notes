"""`liner-notes check`: validate the bank and the journeys, and say what still needs a person.

The schemas in liner_notes/schemas/ are JSON Schema (2020-12). The validator here covers the part of it they
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
        ok = any(
            isinstance(doc, TYPES[x]) and not (x in ("integer", "number") and isinstance(doc, bool))
            for x in (t if isinstance(t, list) else [t])
        )
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
    d = paths.schemas_dir()
    return {n: read_json(d / f"{n}.schema.json") for n in ("song", "journey", "remix-journey", "culture-year")}


def check(log=print):
    sc = schemas()
    bank = Bank()
    problems, notes = [], []
    if not any((paths.home() / "songs").glob("*.json")):
        problems.append(f"the shipped bank isn't installed: no songs in {paths.home() / 'songs'}")
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
                if not sid and not (r.get("original") or {}).get("file"):
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
# (retimed ones included), audio and video, and the ids that only exist to drive personal video.
# Binaries work on an allowlist: our own era pictures are the only images the repo carries.
PERSONAL_DIRS = ("lyrics", "remixes", "originals", "media/covers", "media/culture", "media/artists", "media/depth", "standins")
PERSONAL_FILES = ("standins.json", "culture-videos.json", "stage-cache.json", "av-latency.json")
MEDIA_EXT = {
    ".mp4",
    ".webm",
    ".m4a",
    ".m4v",
    ".opus",
    ".ogg",
    ".oga",
    ".mp3",
    ".mkv",
    ".mov",
    ".avi",
    ".aac",
    ".flac",
    ".wav",
    ".aiff",
    ".lrc",
}
IMAGE_EXT = {".webp", ".jpg", ".jpeg", ".png", ".gif", ".avif", ".bmp", ".tif", ".tiff", ".heic", ".ico"}
TEXT_EXT = {
    ".html",
    ".htm",
    ".json",
    ".js",
    ".mjs",
    ".md",
    ".css",
    ".txt",
    ".py",
    ".toml",
    ".yml",
    ".yaml",
    ".csv",
    ".tsv",
    ".svg",
    ".service",
    "",
}
ERA_IMAGE = re.compile(r"(?:liner_notes/shipped/)?journeys/[a-z0-9-]+/media/eras/era\d+\.webp")
# README screenshots: shot from public builds (no covers), photos only where the licence is free and credited.
SCREENSHOT = re.compile(r"docs/screenshots/[a-z0-9-]+\.(webp|gif)")
RASTER_URI = re.compile(r"data:image/(?:jpe?g|png|gif|avif|bmp)\b", re.I)
WEBP_URI = re.compile(r"data:image/(?:webp)\b", re.I)
LRC_LINE = re.compile(r"^\s*\[\d{1,2}:\d{2}(?:[.:]\d+)?\]", re.M)
LRC_ANY = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d+)?\]\s*\S")
LYRIC_KEYS = {"lyrics", "syncedLyrics", "plainLyrics", "syncedlyrics", "plainlyrics"}
STANDIN_KEYS = {"standin", "standin_offset", "standin_rate"}


def tree_files(root):
    """Files git would commit: tracked plus untracked-but-not-ignored."""
    import subprocess

    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True)
    if r.returncode:
        raise SystemExit(f"liner-notes: check --publish needs a git checkout, and {root} isn't one")
    names = [n for n in r.stdout.decode().split("\0") if n]
    return [root / n for n in names if (root / n).is_file()]


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def json_problems(obj):
    """Lyrics or stand-ins anywhere in a JSON document, whatever the file is called."""
    out = set()
    for d in _walk(obj):
        if LYRIC_KEYS & set(d):
            out.add("lyrics")
        lines = d.get("lines")
        if isinstance(lines, list) and any(isinstance(x, dict) and any(isinstance(v, str) for v in x.values()) for x in lines):
            out.add("lyric lines")
        if STANDIN_KEYS & set(d):
            out.add("stand-in video ids")
    return sorted(out)


def text_problems(text):
    out = []
    if RASTER_URI.search(text):
        out.append("an inline JPEG, PNG or GIF picture (covers and photos are other people's)")
    if WEBP_URI.search(text):
        out.append("an inline WebP picture")
    if len(LRC_LINE.findall(text)) >= 2:
        out.append("timed lyric lines")
    return out


def _magic_webp(p):
    with open(p, "rb") as f:
        head = f.read(12)
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _magic_gif(p):
    with open(p, "rb") as f:
        return f.read(6) in (b"GIF87a", b"GIF89a")


def scan_tree(root):
    """Problems with what the repo would publish."""
    from pathlib import Path

    root = Path(root)
    out = []
    for p in tree_files(root):
        rel = p.relative_to(root).as_posix()
        ext = p.suffix.lower()
        if any(f"/{d}/" in "/" + rel for d in PERSONAL_DIRS):
            out.append(f"{rel}: personal data (covers, pictures, lyrics, stand-ins) never goes in the repo")
        elif p.name in PERSONAL_FILES or ext in MEDIA_EXT:
            out.append(f"{rel}: personal media, lyrics or video ids")
        elif ext in IMAGE_EXT:
            if rel.startswith("tests/fixtures/") and ext == ".webp":
                continue
            shot = SCREENSHOT.fullmatch(rel)
            if shot and (_magic_gif(p) if shot.group(1) == "gif" else _magic_webp(p)):
                continue
            if not (ERA_IMAGE.fullmatch(rel) and _magic_webp(p)):
                out.append(
                    f"{rel}: the only pictures the repo carries are WebP era pictures at liner_notes/shipped/journeys/<slug>/media/eras/era<n>.webp"
                )
        elif ext in TEXT_EXT and p.stat().st_size < 50_000_000:
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            found = text_problems(text)
            if ext == ".json":
                try:
                    found += json_problems(json.loads(text))
                except ValueError:
                    pass
            out += [f"{rel}: {f}" for f in found]
        else:
            out.append(f"{rel}: a file type the publish check doesn't know; add it to check.py if it's ours")
    return out


def scan_page(html, journey=None, bank=None):
    """Problems with a page built for sharing."""
    out = []
    m = re.search(r"window\.JOURNEY = (.*?);</script>", html, re.S)
    try:
        data = json.loads(m.group(1)) if m else {}
    except ValueError:
        data = {}
    # Our own era pictures are the one inline WebP a public page may carry.
    rest = html
    for e in data.get("eras") or []:
        if e.get("image"):
            rest = rest.replace(e["image"], "")
    if RASTER_URI.search(rest):
        out.append("an inline JPEG, PNG or GIF picture")
    if WEBP_URI.search(rest):
        out.append("an inline WebP picture that isn't an era picture")
    # The template's own code builds /v/<id>.mp4 at run time; only a concrete path, or one in the data, counts.
    if re.search(r"/v/[\w-]{11}\.mp4", html) or re.search(r"\.mp4\b", json.dumps(data)):
        out.append("a video path")
    if LRC_ANY.search(html):
        out.append("timed lyric lines")
    if re.search(r'"(?:lines|lyrics|syncedLyrics|plainLyrics)"\s*:', html, re.I):
        out.append("lyric lines")
    if re.search(r'"standin(?:_offset|_rate)?"\s*:', html):
        out.append("stand-in video ids")
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

    home = paths.checkout()
    if not home:
        log(f"liner-notes: check --publish checks a git checkout before you push, and {paths.home()} isn't one")
        return 2
    problems = [f"tree: {p}" for p in scan_tree(home)]
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
