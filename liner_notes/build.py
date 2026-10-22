"""Build one HTML page from a journey.

    liner-notes build <slug> [--public] [--out FILE] [--force]
    liner-notes build --all [--public]
    liner-notes build live                the generic live stage, for playlists with no journey

The page template wants one data object (eras, stops, judges, express). This module composes it
from journeys/<slug>/journey.json and the songs it names. Stops are ordered by release year,
keeping the journey's order within a year, and numbered from 1; threads are written by song id
and become stop numbers here.

A personal build (the default) goes to ~/.local/share/liner-notes/dist with covers from the cache,
and writes the stage data next to it. A --public build is for sharing: no covers, no video ids, no
stage data, nothing that isn't ours. A journey with problems (a song missing from the bank, a stop
outside every era) isn't written unless you pass --force, and every write is atomic, so the page
the bridge is serving is never half a file.
"""

import base64
import html as htmllib
import json
import re
from pathlib import Path

from . import paths
from .bank import Bank

TEMPLATE = paths.PACKAGE / "web" / "template.html"
DATA_MARK = "/*__DATA__*/null"
LIVE = "live"


class Refused(Exception):
    """The build found problems and wasn't forced."""


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def lettered(title, artist):
    """A stand-in cover: the song's initials on a quiet square, for public builds."""
    initials = "".join(w[0] for w in title.split()[:2] if w[:1].isalnum()).upper() or "?"
    hue = sum(map(ord, artist)) % 360
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 600'><rect width='600' height='600' fill='hsl({hue} 22% 22%)'/>"
        f"<text x='300' y='350' font-family='Georgia,serif' font-size='190' fill='hsl({hue} 30% 82%)' text-anchor='middle'>"
        f"{htmllib.escape(initials)}</text></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def youtube_of(j, s):
    return ((j.get("pins") or {}).get(s["id"]) or {}).get("youtube") or ((s.get("aliases") or {}).get("youtube") or [None])[0]


def compose(slug, bank=None, public=False, cache=None, video=False, warnings=None):
    """Return (data, problems, journey) for the template. Missing covers go to `warnings`, not problems."""
    bank = bank or Bank()
    cache = Path(cache or paths.cache())
    warnings = warnings if warnings is not None else []
    j = bank.journeys[slug]
    jdir = bank.journey_dirs[slug]
    panel = j.get("panel") or {}
    quotes = panel.get("quotes") or {}
    problems = []

    eras = []
    for e in j["eras"]:
        img = jdir / e["image"] if e.get("image") else None
        eras.append(
            {
                "n": e["n"],
                "name": e["name"],
                "years": e["years"],
                "from": e["from"],
                "to": e["to"],
                "essay": e["essay"],
                "scene": e["scene"],
                "image": data_uri(img, "image/webp") if img and img.exists() else "",
            }
        )
        if not (img and img.exists()):
            (warnings if j.get("generated") else problems).append(f"era {e['n']} has no image")

    stops = []
    for order, sid in enumerate(j["songs"]):
        s = bank.songs.get(sid)
        if not s:
            problems.append(f"no song {sid!r} in the bank")
            continue
        notes = s.get("notes") or {}
        q = quotes.get(sid)
        if panel.get("judges") and not q:
            problems.append(f"no panel quote for {s['title']!r}")
            continue
        if s.get("year") is None:
            # The page places every stop by year and has no undated shelf yet, so an undated song can't go on it.
            problems.append(f"{s['title']!r} has no year yet; set one with its source in {sid}.json")
            continue
        year = int(s["year"])
        era = next((e["n"] for e in eras if e["from"] <= year <= e["to"]), None)
        if era is None:
            problems.append(f"{s['title']!r} year {year} falls outside every era")
            continue
        yt = youtube_of(j, s)
        apple = (s.get("aliases") or {}).get("apple") or {}
        cover = cache / "covers" / f"{sid}.webp"
        if public or not cover.exists():
            if not public:
                warnings.append(f"no cover for {s['title']!r} (run liner-notes media {slug})")
            cover_uri = lettered(s["title"], s["artist"])
        else:
            cover_uri = data_uri(cover, "image/webp")
        leads = notes.get("leads_to") or {}
        stops.append(
            {
                "id": sid,
                "order": order,
                "title": s["title"],
                "artist": s["artist"],
                "year": year,
                "yearNote": s.get("year_note") or "",
                "album": s.get("album") if (s.get("album") or "").lower() != "single" else "",
                "era": era,
                "judge": q["j"] if q else None,
                "quote": q["q"] if q else "",
                "dissent": bool(q and q.get("dissent")),
                "made": notes.get("made", ""),
                "context": notes.get("context", ""),
                "listen": notes.get("listen_for", []),
                "leads": leads if leads.get("title") else None,
                "sources": notes.get("sources", []),
                "cover": cover_uri,
                # Upload ids drive the personal stage (follow, disk video). A shared file has no use for them.
                "youtube": f"https://www.youtube.com/watch?v={yt}" if yt and not public else "",
                "yt": "" if public else yt,
                "preview": apple.get("preview") or None,
                "apple": apple.get("url") or None,
                "draft": (s.get("review") or {}).get("state") != "reviewed",
            }
        )

    stops.sort(key=lambda s: (s["year"], s["order"]))
    n_of = {}
    for i, s in enumerate(stops, 1):
        s["n"] = i
        n_of[s["id"]] = i
    for s in stops:
        s["threads"], seen = [], set()
        for th in j.get("threads") or []:
            if th["from"] != s["id"]:
                continue
            to = n_of.get(th["to"])
            if to and to != s["n"] and to not in seen:
                seen.add(to)
                s["threads"].append({"to": to, "kind": th.get("kind", ""), "note": th.get("note", "")})
        del s["order"]

    express = panel.get("express") or {}
    data = {
        "slug": j["slug"],
        "title": j["title"],
        "titleA": j["titleA"],
        "titleB": j["titleB"],
        "kicker": j["kicker"],
        "lede": j["lede"],
        "copy": j["copy"],
        "judges": panel.get("judges") or {},
        "express": {"note": express.get("note", ""), "n": [n_of[x] for x in express.get("songs", []) if x in n_of]},
        "eras": eras,
        "stops": stops,
        "product": "Liner Notes",
        # The muted, synced music video is a personal-use opt-in for local builds only: YouTube's developer
        # policies don't allow separating a video from its audio, so a shareable file never carries it.
        "video": bool(video and not public),
        "provenance": provenance(stops),
    }
    copy = dict(data["copy"])
    if DISCLOSURE not in copy.get("credits", ""):
        copy["credits"] = (copy.get("credits", "") + " " + DISCLOSURE).strip()
    data["copy"] = copy
    return data, problems, j


def live_data(video=False):
    """The page with no journey: the generic live stage for whatever is playing."""
    from .generate import DEFAULT_COPY

    data = {
        "slug": LIVE,
        "title": "Liner Notes",
        "titleA": "Liner",
        "titleB": "Notes",
        "kicker": "Now playing",
        "lede": "",
        "copy": dict(DEFAULT_COPY),
        "judges": {},
        "express": {"note": "", "n": []},
        "eras": [],
        "stops": [],
        "product": "Liner Notes",
        "video": bool(video),
        "live": True,
        "provenance": provenance([]),
    }
    meta = {
        "title": "Now playing",
        "description": "Liner Notes: the song that's playing, with its lyrics and the night so far.",
        "fonts": "",
    }
    return data, meta


DISCLOSURE = (
    "An AI model wrote the notes from the sources listed under each song. Songs marked Draft notes haven't been checked by a person yet."
)


def provenance(stops):
    reviewed = sum(1 for s in stops if not s["draft"])
    return {
        "aiGenerated": True,
        "songs": len(stops),
        "reviewedByPerson": reviewed,
        "statement": f"Notes written by an AI model (Claude, Anthropic) from cited sources; {reviewed} of {len(stops)} songs reviewed by a person.",
    }


def script_json(obj, **kw):
    """JSON that is safe inside a <script> element: no '</script>', no '<!--', in any case."""
    return json.dumps(obj, ensure_ascii=False, **kw).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def head_tags(data, meta):
    """Machine-readable AI disclosure (EU AI Act Article 50 applies from 2 August 2026)."""
    from . import __version__

    ld = {
        "@context": "https://schema.org",
        "@type": "CreativeWork",
        "name": meta["title"],
        "description": meta["description"],
        "creativeWorkStatus": "draft" if data["provenance"]["reviewedByPerson"] < data["provenance"]["songs"] else "reviewed",
        "creator": {"@type": "SoftwareApplication", "name": "Liner Notes", "softwareVersion": __version__},
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "aiGenerated", "value": True},
            {"@type": "PropertyValue", "name": "aiDisclosure", "value": data["provenance"]["statement"]},
        ],
    }
    return (
        f'<meta name="generator" content="Liner Notes {htmllib.escape(__version__)}">\n'
        f'<meta name="ai-disclosure" content="{htmllib.escape(data["provenance"]["statement"])}">\n'
        f'<script type="application/ld+json">{script_json(ld)}</script>\n'
    )


FONTS_LINK = re.compile(r"<link\b[^>]*__FONTS__[^>]*>[ \t]*\n?")


def render(data, meta, template=None, public=False):
    """The page: placeholders filled in one pass, so text in one can never be read as another.

    Web fonts are linked only in a --public page, which is opened as a file. A page the bridge serves
    can't load them (its CSP allows fonts from itself only), so it gets no link at all."""
    html = Path(template or TEMPLATE).read_text()
    if DATA_MARK not in html:
        raise Refused(f"{template or TEMPLATE} has no {DATA_MARK} placeholder")
    fonts = meta.get("fonts") or ""
    if not (public and fonts):
        html = FONTS_LINK.sub("", html)
    parts = {
        "__TITLE__": htmllib.escape(meta["title"] + " · Liner Notes"),
        "__DESCRIPTION__": htmllib.escape(meta["description"]),
        "__FONTS__": htmllib.escape(fonts),
        DATA_MARK: script_json(data, separators=(",", ":")),
    }
    html = re.sub("|".join(re.escape(k) for k in parts), lambda m: parts[m.group(0)], html)
    # The head tags go in last, before the template's own <title> (the first one: titles in the data are escaped).
    return html.replace("<title>", head_tags(data, meta) + "<title>", 1)


def write_page(out, html, problems, force, log):
    if problems and not force:
        for p in problems:
            log("  problem:", p)
        raise Refused(f"not writing {out}: {len(problems)} problems (--force writes it anyway)")
    return paths.atomic_write(out, html)


def build(slug, out=None, public=False, bank=None, cache=None, video=False, force=False, log=print, template=None):
    """Build one page. Returns (path, problems). Raises Refused when there are problems and not force."""
    bank = bank or Bank()
    if slug == LIVE:
        return build_live(out, video=video, log=log, template=template), []
    j = bank.journeys.get(slug)
    if j is None:
        raise Refused(f"no journey {slug!r}; liner-notes list shows them")
    if j.get("kind") == "remix":
        from . import remix

        return remix.build(slug, out=out, public=public, bank=bank, cache=cache, force=force, log=log)
    warnings = []
    data, problems, meta = compose(slug, bank, public, cache, video, warnings)
    html = render(data, meta, template, public=public)
    out = Path(out) if out else paths.dist() / (f"{slug}.public.html" if public else f"{slug}.html")
    write_page(out, html, problems, force, log)
    stops = data["stops"]
    log(
        f"{out}  {len(html) / 1e6:.2f} MB  {len(stops)} stops  {sum(1 for s in stops if s['preview'])} previews  "
        f"{sum(len(s['threads']) for s in stops)} threads  {sum(1 for s in stops if s['draft'])} draft"
    )
    for p in problems:
        log("  problem:", p)
    if warnings:
        log(f"  {len(warnings)} notes, first: {warnings[0]}")
    if not public:
        from . import stage

        stage.write(slug, bank=bank, cache=cache, log=log)
    return out, problems


def build_live(out=None, video=False, log=print, template=None):
    data, meta = live_data(video)
    out = Path(out) if out else paths.dist() / f"{LIVE}.html"
    paths.atomic_write(out, render(data, meta, template))
    log(f"{out}  the live stage")
    return out


def build_all(public=False, force=False, log=print, bank=None):
    """Every journey, then the live page. Returns the number that were refused."""
    bank = bank or Bank()
    refused = 0
    for slug in sorted(bank.journeys):
        if public and bank.journeys[slug].get("kind") == "remix":
            log(f"{slug}: a remix stage is built from personal alignments, so it has no public build")
            continue
        try:
            build(slug, public=public, bank=bank, force=force, log=log)
        except Refused as e:
            log(f"{slug}: {e}")
            refused += 1
    if not public:
        build_live(log=log)
    return refused
