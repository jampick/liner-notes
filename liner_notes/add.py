"""Put a journey's playlist into cliamp.

    liner-notes add yacht-rock                         a saved cliamp playlist of the YouTube uploads
    liner-notes add yacht-rock --provider spotify      find each song on another service (read-only search)

YouTube needs no search: the journey pins the upload for each song, so we write a cliamp
playlist file straight into cliamp's playlists folder (and refuse to overwrite one unless --force).

Any other provider goes through cliamp's own read-only `provider.search`. The matches, with
the ones it couldn't find, are saved to ~/.local/share/liner-notes/matches/<slug>.<provider>.json
for you to look over. Creating the playlist on that service is left to you (or a later --apply),
because it writes to your account.
"""

import json

from . import paths
from .bank import Bank, write_json
from .match import Matcher


def toml_quote(s):
    return json.dumps(s, ensure_ascii=False)


def youtube_toml(journey, bank):
    out, skipped = [], []
    for sid in journey["songs"]:
        s = bank.songs.get(sid)
        yt = ((journey.get("pins") or {}).get(sid) or {}).get("youtube") or ((s or {}).get("aliases", {}).get("youtube") or [None])[0]
        if not (s and yt):
            skipped.append(sid)
            continue
        out.append(
            f"[[track]]\npath = {toml_quote('https://www.youtube.com/watch?v=' + yt)}\ntitle = {toml_quote(s['title'])}\nartist = {toml_quote(s['artist'])}\n"
        )
    return "\n".join(out), skipped


def add(slug, provider="youtube", dry_run=False, force=False, log=print):
    bank = Bank()
    if slug not in bank.journeys:
        raise SystemExit(f"liner-notes: no journey {slug!r}; liner-notes list shows them")
    j = bank.journeys[slug]
    if provider in ("youtube", "yt"):
        text, skipped = youtube_toml(j, bank)
        dest = paths.cliamp_dir() / "playlists" / f"{j['title']}.toml"
        for sid in skipped:
            log(f"  no YouTube upload for {sid}")
        if dry_run:
            log(text)
            return dest
        if dest.exists() and not force:
            raise SystemExit(f"liner-notes: {dest} already exists; --force replaces it")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        log(f"wrote {dest} ({len(j['songs']) - len(skipped)} tracks). In cliamp: L, then pick {j['title']!r}.")
        return dest

    from . import cliamp

    found, missing = [], []
    for sid in j["songs"]:
        s = bank.songs.get(sid)
        if not s:
            continue
        res = (
            cliamp.result("provider.search", {"provider": provider, "query": f"{s['artist']} {s['title']}", "limit": 10}, timeout=30) or {}
        )
        rows = res.get("tracks") or res.get("results") or []
        m = Matcher({sid: s})
        hit = next((r for r in rows if m.match(r) == sid), None)
        (found if hit else missing).append({"song": sid, "track": hit} if hit else sid)
        log(f"  {'found' if hit else 'MISSING'}: {s['title']}, {s['artist']}")
    out = paths.user_data() / "matches" / f"{slug}.{provider}.json"
    if not dry_run:
        write_json(out, {"journey": slug, "provider": provider, "found": found, "missing": missing})
    log(f"{len(found)} of {len(found) + len(missing)} found on {provider}" + ("" if dry_run else f"; saved {out}"))
    return out
