"""Build one shareable HTML page from a journey.

    liner-notes build <slug> [--public] [--out FILE]

The page template wants one data object (eras, stops, judges, express). This module composes it
from journeys/<slug>/journey.json and the songs it names. Stops are ordered by release year,
keeping the journey's order within a year, and numbered from 1; threads are written by song id
and become stop numbers here.

Covers come from the cache (`liner-notes media <slug>` fetches them). A --public build leaves
them out and draws a plain lettered square instead, because the covers belong to the labels.
"""
import base64
import html as htmllib
import json
from pathlib import Path

from . import paths
from .bank import Bank

TEMPLATE = paths.PACKAGE / "web" / "template.html"


def data_uri(path, mime):
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def lettered(title, artist):
    """A stand-in cover: the song's initials on a quiet square, for public builds."""
    initials = "".join(w[0] for w in title.split()[:2] if w[:1].isalnum()).upper() or "?"
    hue = sum(map(ord, artist)) % 360
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 600'><rect width='600' height='600' fill='hsl({hue} 22% 22%)'/>"
           f"<text x='300' y='350' font-family='Georgia,serif' font-size='190' fill='hsl({hue} 30% 82%)' text-anchor='middle'>"
           f"{htmllib.escape(initials)}</text></svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def compose(slug, bank=None, public=False, cache=None):
    """Return (data, problems, meta) for the template."""
    bank = bank or Bank()
    cache = Path(cache or paths.cache())
    j = bank.journeys[slug]
    jdir = bank.journey_dirs[slug]
    panel = j.get("panel") or {}
    quotes = panel.get("quotes") or {}
    problems = []

    eras = []
    for e in j["eras"]:
        img = jdir / e["image"] if e.get("image") else None
        eras.append({"n": e["n"], "name": e["name"], "years": e["years"], "from": e["from"], "to": e["to"],
                     "essay": e["essay"], "scene": e["scene"],
                     "image": data_uri(img, "image/webp") if img and img.exists() else ""})
        if not (img and img.exists()):
            problems.append(f"era {e['n']} has no image")

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
        year = int(s["year"])
        era = next((e["n"] for e in eras if e["from"] <= year <= e["to"]), None)
        if era is None:
            problems.append(f"{s['title']!r} year {year} falls outside every era")
            continue
        al = s.get("aliases") or {}
        yt = ((j.get("pins") or {}).get(sid) or {}).get("youtube") or (al.get("youtube") or [None])[0]
        apple = al.get("apple") or {}
        cover = cache / "covers" / f"{sid}.webp"
        if public or not cover.exists():
            if not public:
                problems.append(f"no cover for {s['title']!r} (run liner-notes media {slug})")
            cover_uri = lettered(s["title"], s["artist"])
        else:
            cover_uri = data_uri(cover, "image/webp")
        leads = notes.get("leads_to") or {}
        stops.append({
            "id": sid, "order": order, "title": s["title"], "artist": s["artist"], "year": year,
            "yearNote": s.get("year_note") or "", "album": s.get("album") if (s.get("album") or "").lower() != "single" else "",
            "era": era, "judge": q["j"] if q else None, "quote": q["q"] if q else "", "dissent": bool(q and q.get("dissent")),
            "made": notes.get("made", ""), "context": notes.get("context", ""), "listen": notes.get("listen_for", []),
            "leads": leads if leads.get("title") else None,
            "sources": notes.get("sources", []), "cover": cover_uri,
            "youtube": f"https://www.youtube.com/watch?v={yt}" if yt else "", "yt": yt,
            "preview": apple.get("preview") or None, "apple": apple.get("url") or None,
            "draft": (s.get("review") or {}).get("state") != "reviewed",
        })

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
        "slug": j["slug"], "title": j["title"], "titleA": j["titleA"], "titleB": j["titleB"], "kicker": j["kicker"],
        "lede": j["lede"], "copy": j["copy"], "judges": panel.get("judges") or {},
        "express": {"note": express.get("note", ""), "n": [n_of[x] for x in express.get("songs", []) if x in n_of]},
        "eras": eras, "stops": stops, "product": "Liner Notes",
    }
    return data, problems, j


def render(data, meta):
    html = TEMPLATE.read_text()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (html.replace("__TITLE__", htmllib.escape(meta["title"]) + " · Liner Notes").replace("__DESCRIPTION__", htmllib.escape(meta["description"]))
                .replace("__FONTS__", meta["fonts"]).replace("/*__DATA__*/null", blob))


def build(slug, out=None, public=False, bank=None, cache=None, log=print):
    data, problems, meta = compose(slug, bank, public, cache)
    html = render(data, meta)
    out = Path(out) if out else paths.dist() / f"{slug}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    stops = data["stops"]
    log(f"{out}  {len(html) / 1e6:.2f} MB  {len(stops)} stops  {sum(1 for s in stops if s['preview'])} previews  "
        f"{sum(len(s['threads']) for s in stops)} threads  {sum(1 for s in stops if s['draft'])} draft")
    for p in problems:
        log("  problem:", p)
    return out, problems
