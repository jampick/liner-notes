"""liner-notes: a second screen for cliamp (and any player)."""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

from . import __version__, config, paths
from .bank import Bank, write_json

PORT = config.PORT


def cmd_serve(a):
    from . import adapters, bridge

    adapter = adapters.pick(a.player) if a.player else adapters.pick()
    bridge.serve(a.port, lan=a.lan, tailscale=a.tailscale, adapter=adapter)


def playing_journey(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/now", timeout=3) as r:
            return json.loads(r.read()).get("journey")
    except (OSError, ValueError):
        return None


def cmd_open(a):
    """Open a journey's page, starting the bridge first. With no slug, the journey that holds the
    playing song, or the live stage."""
    from .build import LIVE
    from .launch import System
    from .launch.play import ensure_bridge, ensure_live_page, open_page

    system = System()
    ensure_bridge(system, a.port, log=print)
    slug = a.slug or playing_journey(a.port) or LIVE
    if slug == LIVE:
        ensure_live_page(print)
    if not (paths.dist() / f"{slug}.html").exists():
        sys.exit(f"liner-notes: build it first: liner-notes build {slug}")
    url = f"http://127.0.0.1:{a.port}/p/{slug}.html#companion"
    if not (a.app or a.workspace):
        system.spawn(["open" if sys.platform == "darwin" else "xdg-open", url])
        return 0
    if a.workspace:
        os.environ["LINER_NOTES_WORKSPACE"] = str(a.workspace)
    open_page(system, slug, config.load(), a.port, print, quiet=True)
    return 0


def cmd_play(a):
    from .launch.play import Problem, play

    try:
        what = play(a.name, port=a.port)
    except Problem as e:
        print(f"liner-notes: {e}", file=sys.stderr)
        return 1
    print(f"{a.name}: {what}")
    return 0


def cmd_offline(a):
    from .launch.play import is_offline, set_offline

    if a.mode in ("on", "off"):
        set_offline(a.mode == "on")
    on = is_offline()
    print(f"offline mode {'on: keys load the (offline) twin of each playlist when there is one' if on else 'off'}")
    return 0


def cmd_watch_page(a):
    from .launch import System
    from .launch.hyprland import Hypr, watch
    from .launch.play import remember_workspace

    try:
        watch(a.address, Hypr(System()).events_socket(), remember_workspace)
    except OSError:
        return 1
    return 0


def cmd_service(a):
    from . import service
    from .launch import System

    fn = {"install": service.install, "uninstall": service.uninstall, "status": service.status}[a.action]
    return fn(System())


def cmd_doctor(a):
    from . import doctor
    from .launch import System

    return doctor.run(System(), songs=a.songs)


def cmd_migrate_personal(a):
    from . import personal

    personal.run(a.from_, dry_run=a.dry_run)
    return 0


def cmd_migrate(a):
    from .migrate import migrate, migrate_remix

    for folder in a.folders:
        if (os.path.exists(os.path.join(os.path.expanduser(folder), "remixes.json"))):
            migrate_remix(folder, a.root)
        else:
            migrate(folder, a.root, a.cache)
    return 0


def cmd_list(a):
    bank = Bank()
    for slug, j in sorted(bank.journeys.items()):
        built = "built" if (paths.dist() / f"{slug}.html").exists() else "not built"
        if j.get("kind") == "remix":
            print(f"{slug:24} {j['title']:28} {len(j.get('remixes') or []):3} remixes  {built}")
            continue
        reviewed = sum(1 for s in j["songs"] if (bank.songs.get(s, {}).get("review") or {}).get("state") == "reviewed")
        print(f"{slug:24} {j['title']:28} {len(j['songs']):3} songs  {reviewed:3} reviewed  {built}")
    print(f"{len(bank.songs)} songs in the bank")


def cmd_build(a):
    from .build import Refused, build, build_all

    if a.all:
        return 1 if build_all(public=a.public, force=a.force) else 0
    if not a.slug:
        sys.exit("liner-notes: name a journey, or --all")
    try:
        _, problems = build(a.slug, out=a.out, public=a.public, video=a.video, force=a.force)
    except Refused as e:
        print(f"liner-notes: {e}", file=sys.stderr)
        return 1
    return 1 if a.strict and problems else 0


def cmd_media(a):
    from .media import stage

    stage(a.slug, public=a.public)


def cmd_depth(a):
    from .depth import ensure

    r = ensure()
    if r:
        print(f"{r[0]} new depth maps, {r[1]} pictures")
    return 0 if r else 3


def cmd_check(a):
    from .check import check, publish

    return publish() if a.publish else check()


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


def parser():
    ap = argparse.ArgumentParser(prog="liner-notes", description=__doc__)
    ap.add_argument("--version", action="version", version=f"liner-notes {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the bridge between pages and your player")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--player", choices=["cliamp", "mpris", "macos", "smtc", "homeassistant", "lastfm"], help="default: the first one found")
    p.add_argument("--lan", action="store_true", help="also serve your network, behind a pairing token (plain http)")
    p.add_argument("--tailscale", action="store_true", help="serve HTTPS on this machine's ts.net name, behind a pairing token")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("play", help="play a cliamp playlist in order and open its page (for a key)")
    p.add_argument("name", help="a short name from [keys] in config.toml, or a playlist name")
    p.add_argument("--port", type=int, default=PORT)
    p.set_defaults(fn=cmd_play)

    p = sub.add_parser("offline", help="make play load each playlist's (offline) twin")
    p.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    p.set_defaults(fn=cmd_offline)

    p = sub.add_parser("open", help="open a journey's page in companion mode")
    p.add_argument("slug", nargs="?", help="default: the journey that has the playing song")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--app", action="store_true", help="a Chromium app window with its own profile")
    p.add_argument("--workspace", help="Hyprland: put the app window on this workspace, full screen, without taking focus")
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("service", help="run the bridge as a systemd user service")
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.set_defaults(fn=cmd_service)

    p = sub.add_parser("doctor", help="what's installed and running, and what each journey can show here")
    p.add_argument("--songs", action="store_true", help="every song, with what it's missing")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("migrate-personal", help="copy the journeys pilot's lyrics, pictures and caches into the XDG dirs")
    p.add_argument("--from", dest="from_", default=str(paths.pilot()), metavar="DIR")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_migrate_personal)

    p = sub.add_parser("migrate", help="convert journeys pilot playlist folders into the bank")
    p.add_argument("folders", nargs="+")
    p.add_argument("--root", help="data root to write into (default: this checkout)")
    p.add_argument("--cache", help="where pictures other people own go (default: the cache)")
    p.set_defaults(fn=cmd_migrate)

    p = sub.add_parser("list", help="the journeys and how much of each a person has reviewed")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("build", help="build a journey's page (or 'live', the stage for any song)")
    p.add_argument("slug", nargs="?")
    p.add_argument("--all", action="store_true", help="every journey, then the live page")
    p.add_argument("--out")
    p.add_argument("--public", action="store_true", help="for sharing: no cover art, no video ids")
    p.add_argument("--video", action="store_true", help="personal use only: muted synced music video on the stage")
    p.add_argument("--force", action="store_true", help="write the page even when something is wrong")
    p.add_argument("--strict", action="store_true", help="exit 1 when anything is missing")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("media", help="fetch covers and stage pictures into the cache")
    p.add_argument("slug")
    p.add_argument("--public", action="store_true", help="leave out pictures that aren't freely licensed")
    p.set_defaults(fn=cmd_media)

    p = sub.add_parser("depth", help="depth maps for the stage pictures (needs the [depth] extra)")
    p.set_defaults(fn=cmd_depth)

    p = sub.add_parser("check", help="validate songs and journeys")
    p.add_argument("--publish", action="store_true", help="also make sure nothing personal is in the repo or a public build")
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

    p = sub.add_parser("watch-page")  # internal: started by play, remembers where the page window is moved
    p.add_argument("address")
    p.set_defaults(fn=cmd_watch_page)
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
