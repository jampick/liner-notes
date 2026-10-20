"""`liner-notes migrate-personal`: bring the pilot's personal data into the XDG directories.

    liner-notes migrate-personal --from ~/Projects/Music/journeys [--dry-run]

What moves, and where to:

  lyrics/*.json (retimed lyrics and remix alignments)    ~/.local/share/liner-notes/lyrics/
  playlists/<slug>/originals/*.json                     ~/.local/share/liner-notes/remixes/originals/
  playlists/<slug>/standins.json                        ~/.local/share/liner-notes/standins/<slug>.json
  playlists/<slug>/culture-videos.json                  ~/.local/share/liner-notes/culture-videos/<slug>.json
  playlists/<slug>/media/covers/<upload>.webp           ~/.cache/liner-notes/covers/<song id>.webp
  playlists/yacht-house/media/covers/vol<n>.webp        ~/.cache/liner-notes/covers/yacht-house-vol<n>.webp
  playlists/<slug>/media/{culture,artists,depth}/*      ~/.cache/liner-notes/{culture,artists,depth}/
  playlists/<slug>/stage-cache.json                     ~/.cache/liner-notes/pilot/<slug>.stage-cache.json
  dist/<remix slug>.html (the measured tempo shapes)    ~/.cache/liner-notes/profiles/<id>.json
  ~/.cache/journeys/{video,audio,soundcloud}/*          hard links in ~/.cache/liner-notes/{video,audio,soundcloud}/
  ~/.local/state/journeys/{av-latency.json,workspace}   ~/.local/state/liner-notes/
  ~/.local/state/journeys/chromium (the page's profile)  ~/.local/state/liner-notes/chromium, without caches

The Chromium profile carries the page's localStorage for http://127.0.0.1:47391: per-song lyric
offsets, the lyric lead and the theme. It's copied only when the new profile doesn't exist yet and
no Chromium is running on the old one (a live profile can't be copied whole).

Video and audio are hard-linked, never copied (about 3 GB), with a symlink when the two caches sit
on different filesystems. Nothing in the pilot is changed or deleted. A file that already exists
with the same content is left alone, and one that exists with different content is kept and
reported, so a rerun is safe. Every file ends up in a manifest with its SHA-256
(~/.local/state/liner-notes/migrate-personal.json). --dry-run prints the plan and touches nothing.
"""

import datetime as dt
import errno
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .bank import Bank, read_json


@dataclass
class Step:
    what: str
    src: Path
    dest: Path
    how: str = "copy"  # copy | link | profile
    blob: dict | None = None


def sha256(path, known=None):
    p = Path(path)
    st = p.stat()
    if known and known.get("size") == st.st_size and known.get("mtime_ns") == st.st_mtime_ns and known.get("sha256"):
        return known["sha256"]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files(d, pattern="*"):
    d = Path(d)
    return sorted(p for p in d.glob(pattern) if p.is_file()) if d.is_dir() else []


def _upload_to_song(bank):
    out = {}
    for j in bank.journeys.values():
        for sid, pin in (j.get("pins") or {}).items():
            if pin.get("youtube"):
                out.setdefault(pin["youtube"], sid)
    for sid, s in bank.songs.items():
        for yt in (s.get("aliases") or {}).get("youtube") or []:
            out.setdefault(yt, sid)
    return out


def plan(src, bank=None, pilot_cache=None, pilot_state=None):
    """Every step, in order. Reads the pilot, writes nothing."""
    src = Path(src).expanduser()
    pilot_cache = Path(pilot_cache or Path.home() / ".cache/journeys")
    pilot_state = Path(pilot_state or Path.home() / ".local/state/journeys")
    bank = bank or Bank()
    data, cache, state = paths.user_data(), paths.cache(), paths.state()
    by_upload = _upload_to_song(bank)
    steps, unmapped = [], []

    for p in _files(src / "lyrics", "*.json"):
        steps.append(Step("lyrics", p, data / "lyrics" / p.name))
    for folder in sorted(d for d in (src / "playlists").iterdir() if d.is_dir()) if (src / "playlists").is_dir() else []:
        slug = folder.name
        for p in _files(folder / "originals", "*.json"):
            steps.append(Step("remix original", p, data / "remixes" / "originals" / p.name))
        if (folder / "standins.json").is_file():
            steps.append(Step("stand-ins", folder / "standins.json", data / "standins" / f"{slug}.json"))
        if (folder / "culture-videos.json").is_file():
            steps.append(Step("culture video ids", folder / "culture-videos.json", data / "culture-videos" / f"{slug}.json"))
        if (folder / "stage-cache.json").is_file():
            steps.append(Step("stage cache", folder / "stage-cache.json", cache / "pilot" / f"{slug}.stage-cache.json"))
        for p in _files(folder / "media/covers"):
            m = re.fullmatch(r"vol(\d+)\.webp", p.name)
            if m:
                steps.append(Step("cover", p, cache / "covers" / f"{slug}-vol{m.group(1)}.webp"))
            elif by_upload.get(p.stem):
                steps.append(Step("cover", p, cache / "covers" / f"{by_upload[p.stem]}.webp"))
            else:
                unmapped.append(p)
        for sub in ("culture", "artists", "depth"):
            for p in _files(folder / "media" / sub):
                steps.append(Step(sub, p, cache / sub / p.name))
    for page in _files(src / "dist", "*.html"):
        rf = src / "playlists" / page.stem / "remixes.json"
        if rf.is_file():
            for rid, prof in _profiles(page, read_json(rf)).items():
                steps.append(Step("remix profile", page, cache / "profiles" / f"{rid}.json", "profile", prof))
    for sub in ("video", "audio", "soundcloud"):
        for p in _files(pilot_cache / sub):
            steps.append(Step(sub, p, cache / sub / p.name, "link"))
    for name in ("av-latency.json", "workspace"):
        if (pilot_state / name).is_file():
            steps.append(Step("state", pilot_state / name, state / name))
    # Two pilot folders can hold the same picture; keep the first.
    seen, out = set(), []
    for s in steps:
        if s.dest not in seen:
            seen.add(s.dest)
            out.append(s)
    return out, unmapped


def _songs_blob(page):
    html = Path(page).read_text(errors="ignore")
    m = re.search(r"const SONGS = (\[.*?\]);\n", html, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except ValueError:
        return []


def _profiles(page, remixes):
    """Tempo shapes a built remix page carries, keyed by upload id, for the remixes and their originals."""
    orig_of = {r["id"]: (r.get("original") or {}).get("ytid") for r in remixes}
    out = {}
    for s in _songs_blob(page):
        r, o = s.get("remix") or {}, s.get("original") or {}
        if r.get("bpm"):
            out[s["id"]] = {"bpm": r["bpm"], "seconds": r.get("length"), "energy": r.get("energy")}
        oid = orig_of.get(s.get("id"))
        if oid and o.get("energy"):
            out[oid] = {"bpm": o.get("bpm"), "seconds": o.get("length"), "energy": o["energy"]}
    return out


def _same(a, b):
    try:
        if os.path.samefile(a, b):
            return True
    except OSError:
        return False
    sa, sb = Path(a).stat(), Path(b).stat()
    return sa.st_size == sb.st_size and sha256(a) == sha256(b)


def _link(src, dest):
    try:
        os.link(src, dest)
        return "hardlink"
    except OSError as e:
        if e.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK):
            raise
    os.symlink(Path(src).resolve(), dest)
    return "symlink"


CHROMIUM_SKIP = (
    "Singleton*",
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GraphiteDawnCache",
    "GrShaderCache",
    "ShaderCache",
    "component_crx_cache",
    "Safe Browsing",
    "WidevineCdm",
    "WasmTtsEngine",
    "Crashpad",
    "Service Worker",
    "*.tmp",
)


def _running_on(profile, procs=None):
    """Pids of processes started with --user-data-dir=<profile>."""
    flag = f"--user-data-dir={profile}"
    found = []
    for pid, argv in procs() if procs else _procs():
        if flag in argv:
            found.append(pid)
    return found


def _procs():
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                yield int(p.name), (p / "cmdline").read_bytes().decode(errors="replace").split("\0")
            except OSError:
                continue


def chromium_step(pilot_state, procs=None):
    """(src, dest, why not) for the page's Chromium profile; why not is None when it should be copied."""
    src, dest = Path(pilot_state) / "chromium", paths.state() / "chromium"
    if not src.is_dir():
        return src, dest, "no pilot profile"
    if dest.exists() or dest.is_symlink():
        return src, dest, "the new profile already exists"
    if _running_on(src, procs):
        return src, dest, "Chromium is running on the pilot profile; close the page and run this again"
    return src, dest, None


def copy_profile(src, dest):
    """Copy a Chromium profile without its caches or locks, all or nothing. Returns the files copied."""
    tmp = dest.with_name(f".{dest.name}.tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns(*CHROMIUM_SKIP))
    os.replace(tmp, dest)
    return sorted(p for p in dest.rglob("*") if p.is_file() and not p.is_symlink())


def run(src, dry_run=False, bank=None, pilot_cache=None, pilot_state=None, log=print, procs=None):
    """Carry out the plan. Returns the manifest (a dict)."""
    steps, unmapped = plan(src, bank, pilot_cache, pilot_state)
    csrc, cdest, cwhy = chromium_step(pilot_state or Path.home() / ".local/state/journeys", procs)
    mf = paths.state() / "migrate-personal.json"
    old = {e["dest"]: e for e in (read_json(mf).get("files") if mf.is_file() else [])}
    if dry_run:
        for s in steps:
            log(f"{'link' if s.how == 'link' else 'copy'}  {s.what:18} {s.src} -> {s.dest}")
        for p in unmapped:
            log(f"skip  cover with no song in the bank: {p}")
        log(f"copy  chromium profile   {csrc} -> {cdest}" if not cwhy else f"skip  chromium profile: {cwhy}")
        log(f"plan: {len(steps)} files, nothing written (dry run)")
        return {"files": [], "dry_run": True, "planned": len(steps)}
    entries, counts = [], {}
    for s in steps:
        status = _apply(s)
        counts[status] = counts.get(status, 0) + 1
        if status == "differs":
            log(f"kept  {s.dest} (it differs from {s.src}; delete it to take the pilot's)")
        st = s.dest.lstat()
        prev = old.get(str(s.dest))
        entries.append(
            {
                "what": s.what,
                "src": str(s.src),
                "dest": str(s.dest),
                "how": status,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "sha256": sha256(s.dest, prev) if s.dest.is_file() else None,
            }
        )
    for p in unmapped:
        log(f"skip  cover with no song in the bank: {p}")
    if cwhy:
        if cwhy != "no pilot profile":
            log(f"skip  chromium profile: {cwhy}")
    else:
        files = copy_profile(csrc, cdest)
        counts["profile copy"] = len(files)
        for f in files:
            st = f.stat()
            entries.append(
                {
                    "what": "chromium profile",
                    "src": str(csrc / f.relative_to(cdest)),
                    "dest": str(f),
                    "how": "copied",
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "sha256": sha256(f),
                }
            )
        log(f"copied the page's Chromium profile ({len(files)} files) to {cdest}")
    manifest = {
        "from": str(Path(src).expanduser()),
        "date": dt.datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "files": entries,
        "skipped": [str(p) for p in unmapped],
    }
    paths.atomic_write(mf, json.dumps(manifest, indent=1))
    log(f"{len(entries)} files: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) + f". Manifest: {mf}")
    return manifest


def _apply(s):
    """One step. Returns what happened: copied, hardlink, symlink, profile, same or differs."""
    s.dest.parent.mkdir(parents=True, exist_ok=True)
    if s.how == "profile":
        blob = json.dumps(s.blob)
        if s.dest.exists():
            return "same" if s.dest.read_text() == blob else "differs"
        paths.atomic_write(s.dest, blob)
        return "profile"
    if s.dest.exists() or s.dest.is_symlink():
        return "same" if _same(s.src, s.dest) else "differs"
    if s.how == "link":
        return _link(s.src, s.dest)
    tmp = s.dest.with_name(f".{s.dest.name}.tmp")
    shutil.copy2(s.src, tmp)
    os.replace(tmp, s.dest)
    return "copied"
