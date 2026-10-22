"""Counting the panel. Picks and close calls come from panel.json every time and are never stored,
so an edited vote or a removed veto shows at once and there's no second copy to disagree with.

The rule is the journey's, set by `liner-notes new` in journey.toml (4 yes votes of 5 and a veto by
default); a panel reply can't change it. A pick has at least `rule.yes` yes votes and no veto. A
veto comes from the judge marked `veto: true` and beats any count, so 4 yes votes and a veto are a
close call, not a pick. A close call is any other candidate that was vetoed or came within one
vote. An override, with its reason, beats both, and is listed so it can't pass unseen.
"""

from ..check import validate
from ..ids import key, song_id
from . import schema

VOTES = ("yes", "no")
DEFAULT_RULE = {"yes": 4, "of": 5, "veto": True}


def yes_count(c):
    return sum(1 for v in (c.get("votes") or {}).values() if isinstance(v, dict) and v.get("vote") == "yes")


def verdict(c, rule):
    """ "pick", "close" or "cut" for one candidate."""
    o = c.get("override") or {}
    if o.get("to") in ("pick", "cut"):
        return o["to"]
    yes = yes_count(c)
    vetoed = bool(c.get("veto"))
    if yes >= rule["yes"] and not vetoed:
        return "pick"
    if vetoed or yes == rule["yes"] - 1:
        return "close"
    return "cut"


def count(panel, rule):
    """{picks, close_calls, overrides}: song ids in the order the candidates are listed, and each
    override with its reason."""
    out = {"picks": [], "close_calls": [], "overrides": []}
    for c in panel["candidates"]:
        v = verdict(c, rule)
        if v == "pick":
            out["picks"].append(c["song"])
        elif v == "close":
            out["close_calls"].append(c["song"])
        if c.get("override"):
            o = c["override"]
            out["overrides"].append({"song": c["song"], "to": o["to"], "why": o["why"], "by": o.get("by", "")})
    return out


def resolve(panel, bank):
    """Fill in each candidate's song id from the bank, by id, then by artist and title. Returns the
    candidates it couldn't place, as problem lines."""
    by_key = {(key(s.get("artist", "")), key(s.get("title", ""))): sid for sid, s in bank.songs.items()}
    errs = []
    for i, c in enumerate(panel.get("candidates") or []):
        if not isinstance(c, dict):
            continue
        sid = c.get("song")
        if sid and sid in bank.songs:
            continue
        guess = song_id(c.get("artist", ""), c.get("title", ""))
        found = guess if guess in bank.songs else by_key.get((key(c.get("artist", "")), key(c.get("title", ""))))
        if found:
            c["song"] = found
        else:
            errs.append(
                f"$.candidates[{i}].song: no song in the bank for {c.get('artist')!r}, {c.get('title')!r} "
                f"(looked for {sid or guess}); every candidate has to be a song the bank already has"
            )
    return errs


def problems(panel, rule, bank=None):
    """The schema, then what it can't say, each as a `$.field: message` line."""
    errs = validate(panel, schema("panel"))
    if errs:
        return errs
    judges = panel["judges"]
    if "rule" in panel and {k: panel["rule"].get(k) for k in ("yes", "of")} != {k: rule[k] for k in ("yes", "of")}:
        errs.append(f"$.rule: the rule is the journey's ({rule['yes']} of {rule['of']}, in journey.toml); a panel can't change it")
    names = {}
    for jid, j in judges.items():
        if not isinstance(j, dict):
            errs.append(f"$.judges.{jid}: a judge is an object with name, title and lens")
            continue
        if not (j.get("name") or "").strip():
            errs.append(f"$.judges.{jid}.name: missing")
        elif j["name"].strip().casefold() in names:
            errs.append(f"$.judges.{jid}.name: {j['name']!r} is also {names[j['name'].strip().casefold()]}'s name")
        else:
            names[j["name"].strip().casefold()] = jid
    if len(judges) != rule["of"]:
        errs.append(f"$.judges: the rule is {rule['yes']} of {rule['of']}, and there are {len(judges)} judges")
    holders = [jid for jid, j in judges.items() if isinstance(j, dict) and j.get("veto")]
    seen = set()
    for i, c in enumerate(panel["candidates"]):
        at = f"$.candidates[{i}]"
        sid = c.get("song")
        if not sid:
            errs.append(f"{at}.song: missing")
        elif sid in seen:
            errs.append(f"{at}.song: {sid} is listed twice")
        elif bank is not None and sid not in bank.songs:
            errs.append(f"{at}.song: {sid} isn't in the song bank")
        seen.add(sid)
        for jid in judges:
            if jid not in c["votes"]:
                errs.append(f"{at}.votes.{jid}: missing; every judge votes on every candidate")
        for jid, v in c["votes"].items():
            if jid not in judges:
                errs.append(f"{at}.votes.{jid}: {jid} isn't a judge on this panel")
            elif not isinstance(v, dict) or v.get("vote") not in VOTES:
                errs.append(f"{at}.votes.{jid}.vote: must be yes or no")
            elif not (v.get("why") or "").strip():
                errs.append(f"{at}.votes.{jid}.why: every vote needs a reason")
        veto = c.get("veto")
        if veto:
            if not rule.get("veto"):
                errs.append(f"{at}.veto: this journey's rule has no veto")
            elif veto["by"] not in holders:
                errs.append(f"{at}.veto.by: {veto['by']} doesn't hold the veto on this panel")
        q = c.get("quote")
        if q and q["j"] not in judges:
            errs.append(f"{at}.quote.j: {q['j']} isn't a judge on this panel")
    return errs
