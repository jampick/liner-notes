"""liner-notes: a second screen for cliamp (and any player)."""
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from . import __version__, paths
from .bank import Bank, write_json
from .bridge import PORT


def cmd_serve(a):
    from . import adapters, bridge

    adapter = adapters.pick(a.player) if a.player else adapters.pick()
    bridge.serve(a.port, lan=a.lan, tailscale=a.tailscale, adapter=adapter)


def bridge_up(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/now", timeout=1):
            return True
    except OSError:
        return False


def cmd_open(a):
    """Open a journey's page, starting the bridge first. The default opens it in your usual browser."""
    page = paths.dist() / f"{a.slug}.html"
    if not page.exists():
        sys.exit(f"liner-notes: build it first: liner-notes build {a.slug}")
    if not bridge_up(a.port):
        subprocess.Popen([sys.executable, "-m", "liner_notes.cli", "serve", "--port", str(a.port)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(25):
            if bridge_up(a.port):
                break
            time.sleep(0.2)
    url = f"http://127.0.0.1:{a.port}/p/{a.slug}.html#companion"
    if not (a.app or a.workspace):
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return
    # An app window with its own Chromium profile, so it starts its own process (window rules then apply to it).
    browser = next((b for b in ("chromium", "google-chrome-stable", "google-chrome", "brave") if shutil.which(b)), None)
    if not browser:
        sys.exit("liner-notes: --app needs Chromium or Chrome")
    profile = paths.state() / "chromium"
    cmd = f"{browser} --user-data-dir={profile} --no-first-run --app={url}"
    if a.workspace and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        # Hyprland: place it silently on that workspace, full screen, so whatever has focus keeps it.
        subprocess.run(["hyprctl", "dispatch", f'hl.dsp.exec_cmd("{cmd}", {{ workspace = "{a.workspace} silent", fullscreen = true }})'], capture_output=True)
    else:
        subprocess.Popen(cmd.split(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def cmd_list(a):
    bank = Bank()
    for slug, j in sorted(bank.journeys.items()):
        built = "built" if (paths.dist() / f"{slug}.html").exists() else "not built"
        reviewed = sum(1 for s in j["songs"] if (bank.songs.get(s, {}).get("review") or {}).get("state") == "reviewed")
        print(f"{slug:24} {j['title']:28} {len(j['songs']):3} songs  {reviewed:3} reviewed  {built}")
    print(f"{len(bank.songs)} songs in the bank")


def cmd_build(a):
    from .build import build

    _, problems = build(a.slug, out=a.out, public=a.public, video=a.video)
    return 1 if a.strict and problems else 0


def cmd_media(a):
    from .media import stage

    stage(a.slug, public=a.public)


def cmd_check(a):
    from .check import check

    return check()


def cmd_add(a):
    from .add import add

    add(a.slug, a.provider, a.dry_run, a.force)


def cmd_generate(a):
    from .generate import generate

    if not (a.from_ or (a.provider and a.playlist)):
        sys.exit("liner-notes: give --from <cliamp playlist> or --provider P --playlist ID")
    generate(a.from_ or a.name, a.provider, a.playlist, a.write, a.budget, a.effort)


def cmd_review(a):
    """Mark songs as checked by a person, after you've read them against their sources."""
    bank = Bank()
    for sid in a.songs:
        if sid not in bank.songs:
            sys.exit(f"liner-notes: no song {sid}")
        s = bank.songs[sid]
        s["review"] = {"state": "draft" if a.undo else "reviewed", "by": None if a.undo else a.by, "date": None if a.undo else dt.date.today().isoformat()}
        write_json(bank.song_files[sid], s)
        print(f"{sid}: {s['review']['state']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="liner-notes", description=__doc__)
    ap.add_argument("--version", action="version", version=f"liner-notes {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the bridge between pages and your player")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--player", choices=["cliamp", "mpris", "macos", "smtc", "homeassistant", "lastfm"], help="default: the first one found")
    p.add_argument("--lan", action="store_true", help="also serve your network, behind a pairing token (plain http)")
    p.add_argument("--tailscale", action="store_true", help="serve HTTPS on this machine's ts.net name, behind a pairing token")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("open", help="open a journey's page in companion mode")
    p.add_argument("slug")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--app", action="store_true", help="a Chromium app window with its own profile")
    p.add_argument("--workspace", help="Hyprland: put the app window on this workspace, full screen, without taking focus")
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("list", help="the journeys and how much of each a person has reviewed")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("build", help="build a journey's page")
    p.add_argument("slug")
    p.add_argument("--out")
    p.add_argument("--public", action="store_true", help="for sharing: no cover art, no video")
    p.add_argument("--video", action="store_true", help="personal use only: muted synced music video on the stage")
    p.add_argument("--strict", action="store_true", help="exit 1 when anything is missing")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("media", help="fetch covers and stage pictures into the cache")
    p.add_argument("slug")
    p.add_argument("--public", action="store_true", help="leave out pictures that aren't freely licensed")
    p.set_defaults(fn=cmd_media)

    p = sub.add_parser("check", help="validate songs and journeys")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("add", help="put a journey's playlist into cliamp")
    p.add_argument("slug")
    p.add_argument("--provider", default="youtube")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("generate", help="make a journey from one of your playlists")
    p.add_argument("--from", dest="from_", metavar="PLAYLIST", help="a saved cliamp playlist")
    p.add_argument("--provider")
    p.add_argument("--playlist")
    p.add_argument("--name", help="the journey's title, with --provider")
    p.add_argument("--write", action="store_true", help="write missing notes with the Claude API (costs money)")
    p.add_argument("--budget", type=float, default=2.0, help="USD ceiling for --write (default 2)")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("review", help="mark songs as checked by a person")
    p.add_argument("songs", nargs="+")
    p.add_argument("--by", default=os.environ.get("USER", "owner"))
    p.add_argument("--undo", action="store_true")
    p.set_defaults(fn=cmd_review)

    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
