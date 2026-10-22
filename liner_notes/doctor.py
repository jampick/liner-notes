"""`liner-notes doctor`: what's installed, what's running, and how much of each journey this machine can show.

It only looks: no network, no playback, nothing started. The coverage table counts, per journey,
the songs with notes, a cover in the cache, moving pictures on disk (the upload's own video or a
stand-in), and retimed lyrics (the files the bridge serves before asking cliamp or LRCLIB). --songs
lists every song with what it's missing.
"""

import importlib.util
import os
import platform
import sys
from pathlib import Path

from . import __version__, config, paths

OK, WARN, BAD = "ok  ", "warn", "FAIL"


def _n(k, word):
    return f"{k} {word}" + ("" if k == 1 else "s")


def port_owner(port, proc=Path("/proc")):
    """(pid, argv) of the process listening on a loopback TCP port, from /proc, or None."""
    want = f":{port:04X}"
    inodes = set()
    for name in ("net/tcp", "net/tcp6"):
        try:
            rows = (proc / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) > 9 and f[1].endswith(want) and f[3] == "0A":
                inodes.add(f[9])
    if not inodes:
        return None
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            for fd in (p / "fd").iterdir():
                link = os.readlink(fd)
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    return int(p.name), (p / "cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
    return 0, []  # listening, but not one of ours to look at


def coverage(slug, bank, cache=None):
    """Rows of (song, has) for one journey, where has maps notes/cover/video/lyrics to a short source or ''."""
    from .stage import has_video, standins

    cache = Path(cache or paths.cache())
    j = bank.journeys[slug]
    si = standins(slug)
    rows = []
    for sid in j["songs"]:
        s = bank.songs.get(sid) or {"id": sid, "title": sid, "artist": ""}
        yt = ((j.get("pins") or {}).get(sid) or {}).get("youtube") or ((s.get("aliases") or {}).get("youtube") or [None])[0]
        lyr = paths.lyric_file(yt) if yt else None
        stand = (si.get(yt) or {}).get("standin") if yt else None
        rows.append(
            (
                s,
                {
                    "notes": "written" if (s.get("notes") or {}).get("made") else "",
                    "cover": "cache" if (cache / "covers" / f"{sid}.webp").is_file() else "",
                    "video": "own" if has_video(cache, yt) else ("stand-in" if stand and has_video(cache, stand) else ""),
                    "lyrics": ("retimed" if lyr.parent == paths.user_data() / "lyrics" else "retimed (pilot)") if lyr else "",
                },
            )
        )
    return rows


def remix_coverage(slug, bank, cache=None):
    cache = Path(cache or paths.cache())
    out = []
    for r in bank.journeys[slug].get("remixes") or []:
        lf = paths.lyric_file(r["id"])
        out.append(
            (
                r,
                {
                    "alignment": "yes" if lf else "",
                    "profile": "yes" if (cache / "profiles" / f"{r['id']}.json").is_file() else "",
                    "audio": "yes" if paths.audio_file(r["id"]) else "",
                },
            )
        )
    return out


def run(system, songs=False, log=print):
    from . import cliamp
    from .bank import Bank
    from .launch import chromium
    from .launch.play import is_offline
    from .service import unit_path

    bad = 0

    def line(st, what, detail=""):
        nonlocal bad
        bad += st == BAD
        log(f"{st} {what:22} {detail}")

    cfg = config.load()
    line(OK, "liner-notes", f"{__version__} on Python {platform.python_version()} ({sys.executable})")
    exe = system.which("cliamp")
    if exe:
        v = system.run(["cliamp", "--version"], timeout=3)
        line(OK, "cliamp", f"{exe} {(v.stdout or '').strip().splitlines()[-1] if v.ok and v.stdout.strip() else ''}".strip())
    else:
        line(WARN, "cliamp", "not on PATH (serve --player mpris works with other players)")
    sock = Path(cliamp.socket_path())
    line(
        OK if sock.is_socket() else WARN,
        "cliamp IPC socket",
        f"{sock}" + ("" if sock.is_socket() else " (not there: cliamp isn't running)"),
    )
    browser = chromium.find(system, cfg["stage"].get("browser") or "")
    line(OK if browser else WARN, "browser", browser or "no Chromium or Chrome; play opens your default browser instead")
    hy = system.env.get("HYPRLAND_INSTANCE_SIGNATURE")
    line(
        OK if hy else WARN, "hyprland", "found, pages open silently on their workspace" if hy else "not found, pages open as plain windows"
    )

    owner = port_owner(config.PORT)
    if owner is None:
        line(WARN, f"port {config.PORT}", "nothing listening: the bridge isn't running")
    else:
        pid, argv = owner
        who = " ".join(a for a in argv if a)[:120] or "another user's process"
        kind = (
            "pilot bridge"
            if "engine/bridge.py" in who
            else "liner-notes"
            if "liner_notes" in who or "liner-notes" in who
            else "something else"
        )
        line(OK if kind == "liner-notes" else WARN, f"port {config.PORT}", f"{kind}, pid {pid}: {who}")
    up = unit_path()
    if up.is_file():
        act = system.run(["systemctl", "--user", "is-active", "liner-notes.service"], timeout=5)
        line(OK if act.ok else WARN, "service", f"{up} ({(act.stdout or '').strip() or 'inactive'})")
    else:
        line(WARN, "service", "not installed (liner-notes service install)")
    for name, p in (
        ("data", paths.user_data()),
        ("cache", paths.cache()),
        ("state", paths.state()),
        ("pages", paths.dist()),
        ("config", paths.config_file()),
    ):
        line(OK if p.exists() else WARN, name, f"{p}" + ("" if p.exists() else " (not there yet)"))
    shipped = paths.home()
    n_songs = len(list((shipped / "songs").glob("*.json")))
    n_journeys = len(list((shipped / "journeys").glob("*/journey.json")))
    if n_songs:
        line(OK, "bank", f"{_n(n_songs, 'song')} and {_n(n_journeys, 'journey')} shipped in {shipped}")
    else:
        line(
            BAD,
            "bank",
            f"no songs in {shipped / 'songs'}. The shipped song bank is missing from this install, so every journey "
            "comes up empty. Reinstall liner-notes, or point LINER_NOTES_HOME at liner_notes/shipped in a checkout.",
        )
    from . import overlay

    for kind, ident, what in overlay.report():
        line(WARN, f"{kind} overlay", f"{ident}: {overlay.WHY[what]}")
    line(OK, "offline mode", "on: keys load the (offline) twins" if is_offline() else "off")
    have = [m for m in ("numpy", "onnxruntime", "PIL") if importlib.util.find_spec(m)]
    from .depth import model_path

    line(
        OK if len(have) == 3 and model_path().is_file() else WARN,
        "depth maps",
        "ready"
        if len(have) == 3 and model_path().is_file()
        else f"optional: [depth] extra ({', '.join(have) or 'none'} installed), model {model_path()}",
    )
    dangling = [
        q
        for sub in ("video", "audio", "soundcloud")
        if (paths.cache() / sub).is_dir()
        for q in (paths.cache() / sub).iterdir()
        if q.is_symlink() and not q.exists()
    ]
    if dangling:
        line(WARN, "media links", f"{len(dangling)} links in {paths.cache()} point at files that are gone, first {dangling[0].name}")
    pilot = paths.pilot()
    pilot_lyrics = len(list((pilot / "lyrics").glob("*.json"))) if pilot and (pilot / "lyrics").is_dir() else 0
    ours = len(list((paths.user_data() / "lyrics").glob("*.json"))) if (paths.user_data() / "lyrics").is_dir() else 0
    if pilot_lyrics and ours < pilot_lyrics:
        line(WARN, "pilot data", f"{pilot_lyrics} lyric files still only in {pilot} (liner-notes migrate-personal)")

    bank = Bank()
    log("")
    log(f"{'journey':18} {'songs':>5} {'notes':>6} {'covers':>7} {'video':>6} {'stand-in':>8} {'lyrics':>7}  page")
    for slug in sorted(bank.journeys):
        j = bank.journeys[slug]
        built = "built" if (paths.dist() / f"{slug}.html").is_file() else "not built"
        if j.get("kind") == "remix":
            rows = remix_coverage(slug, bank)
            n = len(rows)
            log(
                f"{slug:18} {n:>5} remixes: {sum(1 for _, h in rows if h['alignment'])} aligned, "
                f"{sum(1 for _, h in rows if h['profile'])} measured, {sum(1 for _, h in rows if h['audio'])} with audio  {built}"
            )
            continue
        rows = coverage(slug, bank)
        c = {k: sum(1 for _, h in rows if h[k]) for k in ("notes", "cover", "lyrics")}
        own = sum(1 for _, h in rows if h["video"] == "own")
        si = sum(1 for _, h in rows if h["video"] == "stand-in")
        log(f"{slug:18} {len(rows):>5} {c['notes']:>6} {c['cover']:>7} {own:>6} {si:>8} {c['lyrics']:>7}  {built}")
        if songs:
            for s, h in rows:
                miss = [k for k, v in h.items() if not v]
                log(
                    f"    {s['title'][:40]:40} {', '.join(f'{k}: {v}' for k, v in h.items() if v)}"
                    + (f"  (no {', '.join(miss)})" if miss else "")
                )
    return 1 if bad else 0
