"""Counting the panel. Picks and close calls come from panel.json every time and are never stored,
so an edited vote or a removed veto shows at once and there's no second copy to disagree with.

The rule is `rule.yes` yes votes (4 of 5 by default) and no veto. A veto comes from a judge with
`veto: true` and beats any count, so 4 yes votes and a veto are a close call, not a pick. A close
call is any candidate that isn't a pick but was vetoed or came within one vote. An override, with
its reason, beats both.
"""

from ..check import validate
from . import schema

VOTES = ("yes", "no")


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
    if vetoed or yes >= rule["yes"] - 1:
        return "close"
    return "cut"


def count(panel):
    """(picks, close_calls) as song ids, in the order the candidates are listed."""
    rule = panel["rule"]
    picks, close = [], []
    for c in panel["candidates"]:
        v = verdict(c, rule)
        if v == "pick":
            picks.append(c["song"])
        elif v == "close":
            close.append(c["song"])
    return picks, close


def problems(panel):
    """The schema, then what it can't say: every vote from a judge on the panel, with a reason, a
    veto only from a judge who holds one, no song listed twice, a rule that can be met."""
    errs = validate(panel, schema("panel"))
    if errs:
        return errs
    judges = panel["judges"]
    for jid, j in judges.items():
        if not isinstance(j, dict) or not j.get("name"):
            errs.append(f"$.judges.{jid}: needs a name")
    rule = panel["rule"]
    if rule["yes"] > rule["of"]:
        errs.append(f"$.rule: {rule['yes']} yes votes of {rule['of']} can't happen")
    if len(judges) != rule["of"]:
        errs.append(f"$.rule.of: {rule['of']}, but there are {len(judges)} judges")
    seen = set()
    for i, c in enumerate(panel["candidates"]):
        at = f"$.candidates[{i}]"
        if c["song"] in seen:
            errs.append(f"{at}.song: {c['song']} is listed twice")
        seen.add(c["song"])
        for jid, v in c["votes"].items():
            if jid not in judges:
                errs.append(f"{at}.votes.{jid}: {jid} isn't a judge on this panel")
            elif not isinstance(v, dict) or v.get("vote") not in VOTES:
                errs.append(f"{at}.votes.{jid}.vote: must be yes or no")
            elif not (v.get("why") or "").strip():
                errs.append(f"{at}.votes.{jid}.why: every vote needs a reason")
        veto = c.get("veto")
        if veto and not (judges.get(veto["by"]) or {}).get("veto"):
            errs.append(f"{at}.veto.by: {veto['by']} doesn't hold a veto on this panel")
        q = c.get("quote")
        if q and q["j"] not in judges:
            errs.append(f"{at}.quote.j: {q['j']} isn't a judge on this panel")
    return errs
