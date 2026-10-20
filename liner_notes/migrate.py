"""Convert a journeys-pilot playlist folder into the song bank, the culture bank and a journey.

    python3 tools/migrate_journey.py ~/Projects/Music/journeys/playlists/downtempo-drift

Old folder: playlist.json, panel.json, tracks.json, eras/era-N.json, culture/era-N.json,
stage-cache.json, media/{covers,eras,culture,artists}/.

It is idempotent and merges: a song already in the bank keeps its notes and review state, and
only gains aliases, alternative titles and culture items it didn't have. Pictures other people own
go to the cache, never into the repo; our own era photographs go into the journey folder.
"""
import datetime as dt
import re
import shutil
from pathlib import Path

from . import paths
from .bank import read_json, write_json
from .ids import key as _key
from .ids import song_id, youtube_id


def key(title):
    """Titles compared without parentheticals, as the pilot's build did: 'I.G.Y. (What a Beautiful World)' is 'I.G.Y.'."""
    t = re.sub(r"\s*\([^)]*\)", "", title or "")
    return _key(t or title)


def _merge_list(old, new, ident):
    seen = {ident(x) for x in old}
    out = list(old)
    for x in new:
        if ident(x) not in seen:
            seen.add(ident(x))
            out.append(x)
    return out


def _culture_ident(it):
    return it.get("wiki") or it.get("title")


def _day(p):
    return dt.date.fromtimestamp(Path(p).stat().st_mtime).isoformat()


def migrate(folder, root=None, cache=None, log=print):
    folder = Path(folder).expanduser().resolve()
    root = Path(root or paths.home())
    cache = Path(cache or paths.cache())
    meta = read_json(folder / "playlist.json")
    panel = read_json(folder / "panel.json")
    tracks = read_json(folder / "tracks.json")
    slug = meta["slug"]
    jdir = root / "journeys" / slug

    track_by = {key(t["title"]): t for t in tracks}
    writer = []  # (era doc, stop) in writer order
    era_docs = []
    for f in sorted((folder / "eras").glob("era-*.json")):
        doc = read_json(f)
        era_docs.append((f, doc))
        for s in doc["stops"]:
            writer.append((f, s))

    # Song ids: from the writers' official title and credit, which correct the panel's working titles.
    ids_by_key, n_to_id, created, merged = {}, {}, 0, 0
    for f, s in writer:
        t = track_by.get(key(s["title"]))
        if not t:
            log(f"  skip: no track for writer title {s['title']!r}")
            continue
        sid = song_id(s["artist"], s["title"])
        sfile = root / "songs" / f"{sid}.json"
        if sfile.exists():
            have = read_json(sfile)
            if have.get("year") and s.get("year") and abs(int(have["year"]) - int(s["year"])) > 1 and key(have["title"]) != key(s["title"]):
                sid = f"{sid}--{s['year']}"
                sfile = root / "songs" / f"{sid}.json"
        ids_by_key[key(s["title"])] = sid
        ids_by_key[key(t["title"])] = sid
        n_to_id[s["n"]] = sid

        yt = t.get("ytid") or youtube_id(t.get("youtube"))
        aliases = {"youtube": [yt] if yt else []}
        if t.get("preview") or t.get("apple"):
            aliases["apple"] = {"preview": t.get("preview") or "", "url": t.get("apple") or ""}
        if key(t["title"]) != key(s["title"]):
            aliases["titles"] = [t["title"]]
        if key(t["artist"]) != key(s["artist"]):
            aliases["artists"] = [t["artist"]]

        song = read_json(sfile) if sfile.exists() else None
        if song is None:
            created += 1
            song = {
                "id": sid, "title": s["title"], "artist": s["artist"], "year": int(s["year"]),
                "year_note": s.get("year_note") or "", "album": s.get("album") or "",
                "aliases": aliases,
                "notes": {
                    "made": s.get("made", ""), "context": s.get("context", ""),
                    "listen_for": s.get("listen_for", []), "leads_to": s.get("leads_to") or {},
                    "sources": s.get("sources", []), "unverified": s.get("unverified", ""),
                },
                "culture": [],
                "review": {"state": "draft", "by": None, "date": None},
                "provenance": {"ai_generated": True, "model": "Claude (Anthropic), research agents with web search",
                               "written_by": f"journeys pilot era writer ({slug}, {f.stem})", "written_on": _day(f)},
            }
        else:
            merged += 1
            al = song.setdefault("aliases", {})
            al["youtube"] = _merge_list(al.get("youtube") or [], aliases["youtube"], lambda x: x)
            if "apple" in aliases and not (al.get("apple") or {}).get("preview"):
                al["apple"] = aliases["apple"]
            for k in ("titles", "artists"):
                if k in aliases:
                    al[k] = _merge_list(al.get(k) or [], aliases[k], key)
        write_json(sfile, song)

        cover = folder / "media/covers" / f"{yt}.webp"
        dest = cache / "covers" / f"{sid}.webp"
        if cover.exists() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cover, dest)

    # Culture: years into the year bank, per-song items into the songs, artists into one map.
    artists = {}
    for f in sorted((folder / "culture").glob("era-*.json")):
        doc = read_json(f)
        for year, items in (doc.get("years") or {}).items():
            yf = root / "culture/years" / f"{year}.json"
            have = read_json(yf) if yf.exists() else {"year": int(year), "items": []}
            have["items"] = _merge_list(have["items"], items, _culture_ident)
            write_json(yf, have)
        for title, items in (doc.get("songs") or {}).items():
            sid = ids_by_key.get(key(title))
            if not sid:
                log(f"  culture: no song for {title!r}")
                continue
            sfile = root / "songs" / f"{sid}.json"
            song = read_json(sfile)
            song["culture"] = _merge_list(song.get("culture") or [], items, _culture_ident)
            write_json(sfile, song)
        artists.update(doc.get("artists") or {})
    if artists:
        af = root / "culture/artists.json"
        have = read_json(af) if af.exists() else {}
        for k, v in artists.items():
            have.setdefault(k, v)
        write_json(af, dict(sorted(have.items())))

    for sub in ("culture", "artists"):
        src = folder / "media" / sub
        for p in src.glob("*.webp") if src.exists() else []:
            dest = cache / sub / p.name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
    sc = folder / "stage-cache.json"
    if sc.exists():
        hf = cache / "http.json"
        have = read_json(hf) if hf.exists() else {}
        for k, v in read_json(sc).items():
            have.setdefault(k, v)
        write_json(hf, have)

    # The journey.
    eras = []
    for _f, doc in era_docs:
        e = doc["era"]
        n = int(e["n"])
        lo, hi = meta["eraBounds"][n - 1]
        img = folder / "media/eras" / f"era{n}.webp"
        if img.exists():
            (jdir / "media/eras").mkdir(parents=True, exist_ok=True)
            shutil.copy2(img, jdir / "media/eras" / img.name)
        eras.append({"n": n, "name": e["name"], "years": e["years"], "from": lo, "to": hi, "essay": e["essay"],
                     "scene": e["scene"], "sources": e.get("sources", []), "image": f"media/eras/era{n}.webp"})

    # Release year, then the panel's order within a year: the order the page numbers the stops in, and
    # the order the cliamp playlist plays them in.
    year_of = {n_to_id[s["n"]]: int(s["year"]) for _f, s in writer if s["n"] in n_to_id}
    panel_order = list(dict.fromkeys(ids_by_key[key(t["title"])] for t in tracks if key(t["title"]) in ids_by_key))
    order = sorted(panel_order, key=lambda sid: (year_of.get(sid, 0), panel_order.index(sid)))
    pins = {}
    for t in tracks:
        sid = ids_by_key.get(key(t["title"]))
        yt = t.get("ytid") or youtube_id(t.get("youtube"))
        if sid and yt:
            pins[sid] = {"youtube": yt}
    quotes = {}
    for title, q in panel["quotes"].items():
        sid = ids_by_key.get(key(title))
        if sid:
            quotes[sid] = {"j": q["j"], "q": q["q"], **({"dissent": True} if q.get("dissent") else {})}
        else:
            log(f"  panel: no song for quote {title!r}")
    threads = []
    for _f, s in writer:
        frm = n_to_id.get(s["n"])
        for th in s.get("threads", []):
            to = n_to_id.get(th.get("to"))
            if frm and to and to != frm and not any(x["from"] == frm and x["to"] == to for x in threads):
                threads.append({"from": frm, "to": to, "kind": th.get("kind", ""), "note": th.get("note", "")})

    journey = {
        "slug": slug, "title": meta["title"], "titleA": meta["titleA"], "titleB": meta["titleB"],
        "kicker": meta["kicker"], "description": meta["description"], "lede": meta["lede"],
        "fonts": meta["fonts"], "copy": meta["copy"],
        "panel": {
            "judges": panel["judges"],
            "express": {"note": panel["express"]["note"], "songs": [ids_by_key[key(t)] for t in panel["express"]["titles"] if key(t) in ids_by_key]},
            "quotes": quotes,
        },
        "eras": eras, "songs": order, "pins": pins, "threads": threads,
        "review": {"state": "draft", "by": None, "date": None},
    }
    write_json(jdir / "journey.json", journey)
    if (folder / "CORRECTIONS.md").exists():
        shutil.copy2(folder / "CORRECTIONS.md", jdir / "CORRECTIONS.md")
    _review_todo(jdir, journey, root)
    log(f"{slug}: {len(order)} songs ({created} new, {merged} already in the bank), {len(eras)} eras, {len(threads)} threads")
    return journey


def _review_todo(jdir, journey, root):
    lines = [f"# Review: {journey['title']}", "",
             "Every song here starts as a draft. A draft shows as \"draft notes\" on the stage. Tick a song once",
             "you've checked its facts against the sources, then set `review.state` to `reviewed` in its file",
             "(`liner-notes review <song id>` does both).", ""]
    for sid in journey["songs"]:
        s = read_json(root / "songs" / f"{sid}.json")
        mark = "x" if s.get("review", {}).get("state") == "reviewed" else " "
        lines.append(f"- [{mark}] `{sid}` {s['title']}, {s['artist']} ({s['year']})")
        if s["notes"].get("unverified"):
            lines.append(f"    - writer couldn't confirm: {s['notes']['unverified']}")
    (jdir / "REVIEW.md").write_text("\n".join(lines) + "\n")


def migrate_remix(folder, root=None, log=print):
    """A pilot remix folder (remixes.json, panel.json) into journeys/<slug>/journey.json.

    The remix list, its uploads and the panel are ours and go in the repo. The alignments, the
    originals' lyrics and the Party Pupils covers are personal: migrate-personal copies those."""
    from .bank import Bank

    folder = Path(folder).expanduser().resolve()
    root = Path(root or paths.home())
    slug = folder.name
    bank = Bank([root])
    remixes = []
    for r in read_json(folder / "remixes.json"):
        o = r["original"]
        if o.get("ytid"):
            sid = next((s["id"] for s in bank.songs.values() if o["ytid"] in ((s.get("aliases") or {}).get("youtube") or [])), None)
            if not sid:
                log(f"  {r['title']}: no song in the bank has the upload {o['ytid']}")
            orig = {"song": sid, "youtube": o["ytid"]}
        else:
            orig = {"lyrics": Path(o["lyrics"]).name, "year": o.get("year")}
        remixes.append({k: v for k, v in {**r, "original": orig}.items() if v is not None})
    panel = read_json(folder / "panel.json") if (folder / "panel.json").exists() else {}
    title = " ".join(w.capitalize() for w in slug.split("-"))
    words = title.split()
    journey = {
        "slug": slug, "kind": "remix", "title": title, "titleA": words[0], "titleB": " ".join(words[1:]),
        "kicker": f"{len(remixes)} remixes", "description": f"{title}: {len(remixes)} house remixes, each laid against the record it flips.",
        "songs": [], "remixes": remixes, "panel": {"quotes": panel},
        "review": {"state": "draft", "by": None, "date": None},
    }
    write_json(root / "journeys" / slug / "journey.json", journey)
    log(f"{slug}: {len(remixes)} remixes, {len(panel)} panel quotes")
    return journey
