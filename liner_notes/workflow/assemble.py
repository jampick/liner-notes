"""`liner-notes assemble`: the stage files become the journey.json the page reads.

The panel's picks are counted here from panel.json, as status counts them; close calls stay out of
the journey. Each pick gets its quote (or, without one, the first yes vote's reason). A stage that's
empty, stale or unsigned stops it unless you force it, because a journey goes live when it's built.
"""

from ..bank import Bank, SchemaError, write_checked
from ..generate import DEFAULT_COPY
from . import journeytoml, panel, stages
from .journeytoml import Problem


def years_label(a, b):
    if a == b:
        return str(a)
    if a // 100 == b // 100:
        return f"{a}–{b % 100:02d}"
    return f"{a}–{b}"


def _quote(c, judges):
    if c.get("quote"):
        return {k: v for k, v in c["quote"].items() if k in ("j", "q", "dissent")}
    for jid in judges:
        v = c["votes"].get(jid) or {}
        if v.get("vote") == "yes":
            return {"j": jid, "q": v["why"]}
    return None


def assemble(slug, force=False, log=print):
    """Write journeys/<slug>/journey.json in user data. Returns its path."""
    st = stages.status(slug)
    holding = [f"{s['name']} is {s['state']}" for s in st["stages"] if s["state"] != "ok"]
    if holding and not force:
        raise Problem(f"not assembling {slug}: " + ", ".join(holding) + ". liner-notes status shows why; --force assembles anyway")
    doc = journeytoml.load(slug)
    d = journeytoml.folder(slug)
    read = stages._read_json
    front = read(d / "front.json")
    eras = read(d / "eras.json")["eras"]
    dates = {x["song"]: x for x in read(d / "dates.json")["songs"]}
    bank = Bank()

    journey_panel = {}
    if doc["panel"]:
        p = read(d / "panel.json")
        picks, _ = panel.count(p)
        cand = {c["song"]: c for c in p["candidates"]}
        journey_panel = {
            "judges": {jid: {k: j[k] for k in ("name", "title", "lens") if k in j} for jid, j in p["judges"].items()},
            "quotes": {sid: q for sid in picks if (q := _quote(cand[sid], p["judges"]))},
        }
        songs = picks
    else:
        songs = list(doc["seed"].get("songs") or [])
    express = front.get("express") or {}
    journey_panel["express"] = {"note": express.get("note", ""), "songs": [s for s in express.get("songs", []) if s in songs]}

    problems = [f"{sid} isn't in the song bank yet" for sid in songs if sid not in bank.songs]
    for sid in songs:
        y, mine = (bank.songs.get(sid) or {}).get("year"), (dates.get(sid) or {}).get("year")
        if sid not in dates:
            problems.append(f"{sid} has no entry in dates.json")
        elif sid in bank.songs and y != mine:
            log(f"warning: {sid}: dates.json says {mine}, the song bank says {y}; the page uses the bank's year")
    if problems and not force:
        raise Problem(f"not assembling {slug}: " + "; ".join(problems))

    j = {
        "slug": slug,
        "title": front["title"],
        "titleA": front["titleA"],
        "titleB": front["titleB"],
        "kicker": front["kicker"],
        "description": front["description"],
        "lede": front["lede"],
        "copy": {**DEFAULT_COPY, **(front.get("copy") or {})},
        "panel": journey_panel,
        "eras": [
            {
                "n": n,
                "name": e["name"],
                "years": e.get("years") or years_label(e["from"], e["to"]),
                "from": e["from"],
                "to": e["to"],
                "essay": e["essay"],
                "scene": e["scene"],
                **({"image": e["image"]} if e.get("image") else {}),
            }
            for n, e in enumerate(eras, 1)
        ],
        "songs": songs,
        "pins": {},
        "threads": [t for t in front.get("threads") or [] if t["from"] in songs and t["to"] in songs],
        "review": {"state": "draft", "by": None, "date": None},
    }
    if front.get("fonts"):
        j["fonts"] = front["fonts"]
    try:
        return write_checked(d / "journey.json", j, "journey")
    except SchemaError as e:
        raise Problem(str(e)) from None
