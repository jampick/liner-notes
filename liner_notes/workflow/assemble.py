"""`liner-notes assemble`: the stage files become the journey.json the page reads, in the journey's
work folder. The bank doesn't look there, so nothing is live until `liner-notes build <slug>`.

Two kinds of refusal. Sign-off (a stage that's a draft, stale or edited) stops it unless you pass
--force, and the message gives the accept command for each. Broken content stops it with or without
--force: a stage not started or broken, a song not in the bank, a year in dates.json the bank
disagrees with, an era running backwards or with no essay, a song outside every era. The page or
`check` would choke on those.

Picks are counted from panel.json here, as status counts them; close calls stay out of the journey.
"""

from ..bank import SchemaError, write_checked
from ..generate import DEFAULT_COPY
from . import journeytoml
from .journeytoml import Problem
from .stages import DATES, ERAS, FRONT, PANEL, RANK, Ctx, stage_status

SIGN_OFF = ("draft", "stale", "edited")


def years_label(a, b):
    if a == b:
        return str(a)
    if a // 100 == b // 100:
        return f"{a}–{b % 100:02d}"
    return f"{a}–{b}"


def disagreements(ctx):
    """(song, dates row, bank song) where dates.json gives a year the bank doesn't have."""
    out = []
    for r in (ctx.read(DATES.file) or {}).get("songs") or []:
        s = ctx.bank.songs.get(r.get("song"))
        if s and r.get("year") is not None and r["year"] != s.get("year"):
            out.append((r["song"], r, s))
    return out


def check(ctx, stages=None):
    """(holds, problems, warnings). holds are stages waiting on sign-off; problems are broken
    content that never assembles."""
    stages = stages or [stage_status(ctx, s) for s in ctx.stages()]
    holds, problems, warnings = [], [], []
    for st in stages:
        if st["state"] in SIGN_OFF:
            holds.append(st)
        if st["state"] == "not started":
            problems.append(f"{st['name']} is not started: {st['next']}")
        elif st["state"] == "broken":
            problems.append(
                f"{st['name']} is broken, {st['file']}: "
                + "; ".join(st["problems"])
                + f". Fix it, then liner-notes accept {ctx.slug} {st['name']}"
            )
        elif st["problems"]:
            problems.append(f"{st['name']}: " + "; ".join(st["problems"]))
    if problems:
        return holds, problems, warnings
    slug, bank = ctx.slug, ctx.bank
    songs = ctx.songs or []
    if not songs:
        problems.append("the journey has no songs")
    for sid in songs:
        if sid not in bank.songs:
            problems.append(f"{sid} isn't in the song bank yet")
    for _sid, r, s in disagreements(ctx):
        if RANK.get(r.get("source"), 0) >= RANK.get(s.get("year_source"), 0):
            problems.append(
                f"{s['title']!r}: dates.json says {r['year']} ({r.get('source')}), the bank says {s.get('year')} "
                f"({s.get('year_source') or 'no source'}); liner-notes dates-apply {slug} writes {r['year']} into your copy of the song"
            )
        else:
            warnings.append(
                f"{s['title']!r}: dates.json says {r['year']}, the bank's {s.get('year')} ({s['year_source']}) outranks it and the page uses that"
            )
    eras = (ctx.read(ERAS.file) or {}).get("eras") or []
    for n, e in enumerate(eras, 1):
        if e["from"] > e["to"]:
            problems.append(f"era {n} ({e['name']}) runs backwards, {e['from']} to {e['to']}")
        img = e.get("image") or ""
        if img and (not img.startswith("media/") or ".." in img.split("/")):
            problems.append(
                f"era {n} ({e['name']}) names the picture {img}; pictures go in media/ in {ctx.dir}, "
                f'with "image": "media/<file>" in eras.json, since build copies only that folder'
            )
        elif img and not (ctx.dir / img).is_file():
            problems.append(f"era {n} ({e['name']}) names the picture {e['image']}, and {ctx.dir / e['image']} isn't there")
        elif not e.get("image"):
            warnings.append(f"era {n} ({e['name']}) has no picture, and build refuses a page without one unless you --force it")
        if not (e.get("essay") or "").strip() or not e.get("scene"):
            problems.append(f"era {n} ({e['name']}) has no essay or scene yet: liner-notes prompt {slug} eras --era {n}")
    problems += quote_problems(ctx, eras, warnings)
    for sid in songs:
        s = bank.songs.get(sid)
        if not s:
            continue
        if not s.get("year"):
            warnings.append(f"{s['title']!r} has no year in the bank, so the page leaves it off")
        elif eras and not any(e["from"] <= s["year"] <= e["to"] for e in eras):
            problems.append(f"{s['title']!r} ({s['year']}) is outside every era")
    return holds, problems, warnings


def quote_problems(ctx, eras, warnings):
    """A panel quote an era writer marked "wrong" holds the journey while panel.json still carries
    those exact words; editing the quote clears it, and --force doesn't skip it. "unconfirmed" is a
    warning."""
    if not ctx.doc["panel"]:
        return []
    p = ctx.read(PANEL.file) or {}
    judges = p.get("judges") or {}
    cands = {c.get("song"): c for c in p.get("candidates") or [] if isinstance(c, dict)}
    out = []
    for n, e in enumerate(eras, 1):
        for row in e.get("panel_check") or []:
            c = cands.get(row.get("song")) or {}
            q = (c.get("quote") or {}).get("q")
            if q is None or q != row.get("quote"):
                continue
            who = (judges.get(row.get("judge")) or {}).get("name") or row.get("judge")
            where = f" ({row['source']})" if row.get("source") else ""
            said = f"{c.get('title')!r}: {who}'s quote {q!r} was marked {row['verdict']} by the era {n} writer: {row.get('note') or 'no note'}{where}"
            if row.get("verdict") == "wrong":
                out.append(f"{said}. Edit the quote in panel.json, then liner-notes accept {ctx.slug} panel")
            elif row.get("verdict") == "unconfirmed":
                warnings.append(said)
    return out


def _quote(c):
    return {k: v for k, v in c["quote"].items() if k in ("j", "q", "dissent")}


def assemble(slug, force=False):
    """Write journey.json in the work folder. Returns {file, skipped, warnings}."""
    ctx = Ctx(journeytoml.load(slug))
    holds, problems, warnings = check(ctx)
    if problems:
        raise Problem(f"not assembling {slug}, even with --force: " + "; ".join(problems), [{"field": "", "message": p} for p in problems])
    if holds and not force:
        lines = [f"{h['name']} is {h['state']}" + (f" ({'; '.join(h['stale'])})" if h["stale"] else "") for h in holds]
        cmds = [f"liner-notes accept {slug} {h['name']}" for h in holds]
        raise Problem(
            f"not assembling {slug}: "
            + ", ".join(lines)
            + ". Once you've looked:\n  "
            + "\n  ".join(cmds)
            + "\n(--force assembles without the sign-off)",
            [{"field": h["name"], "message": f"{h['state']}: {h['next']}"} for h in holds],
        )
    read = ctx.read
    front = read(FRONT.file)
    eras = read(ERAS.file)["eras"]
    songs = ctx.songs
    journey_panel = {}
    if ctx.doc["panel"]:
        p = read(PANEL.file)
        cand = {c["song"]: c for c in p["candidates"]}
        journey_panel = {
            "judges": {jid: {k: j[k] for k in ("name", "title", "lens") if k in j} for jid, j in p["judges"].items()},
            "quotes": {sid: _quote(cand[sid]) for sid in songs},
        }
    express = front.get("express") or {}
    journey_panel["express"] = {"note": express.get("note", ""), "songs": list(express.get("songs") or [])}
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
        "threads": list(front.get("threads") or []),
        "review": {"state": "draft", "by": None, "date": None},
    }
    if front.get("fonts"):
        j["fonts"] = front["fonts"]
    try:
        out = write_checked(ctx.dir / "journey.json", j, "journey")
    except SchemaError as e:
        raise Problem(str(e)) from None
    return {"file": str(out), "skipped": [h["name"] for h in holds], "warnings": warnings}
