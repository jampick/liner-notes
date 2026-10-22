"""The stages of a journey, what each one reads, and where each stands.

For every stage journey.toml keeps the hash of each input as it was when the stage was last
accepted, and the hash of the output then (the sign-off). `status` compares those with the files
as they are now:

  empty      no output yet
  draft      an output nobody has accepted
  stale      an input changed, went missing, or the brief has a new version
  unsigned   the output was edited after sign-off
  ok         none of the above

Nothing reruns on its own. You rerun the stage (`stage --prompt`, then `stage --accept`), or run
`accept <stage>`, which says you looked and it still holds.
"""

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..check import validate
from . import hashing, journeytoml, panel, prompts, schema
from .journeytoml import Problem


@dataclass(frozen=True)
class Stage:
    name: str
    file: str
    brief: str | None  # the prompt file's name; None for a stage with a built-in brief
    schema: str | None  # None for heuristic.md, which is Markdown


HEURISTIC = Stage("heuristic", "heuristic.md", "heuristic", None)
PANEL = Stage("panel", "panel.json", "vote", "panel")
DATES = Stage("dates", "dates.json", None, "dates")
ERAS = Stage("eras", "eras.json", "era", "eras")
FRONT = Stage("front", "front.json", "front", "front")
ALL = (HEURISTIC, PANEL, DATES, ERAS, FRONT)

DATES_BRIEF = """Give each song below the year its recording was first released (single or album, whichever came
first), with where the year came from and how sure you are. The year shown is the bank's first
guess, often from MusicBrainz, and it can be wrong: check it. If you can't find a year, write null;
never guess one. When you change a year, put the old one in corrected_from and a source in url.
"""


def stages_of(doc):
    return [s for s in ALL if s is not PANEL or doc["panel"]]


def by_name(doc, name):
    for s in stages_of(doc):
        if s.name == name:
            return s
    names = ", ".join(s.name for s in stages_of(doc))
    raise Problem(f"{doc['slug']} has no stage {name!r}; its stages are {names}")


def inputs_of(doc, stage):
    """What a stage reads. "seed" is journey.toml's [seed]; "songs" is the song list (the panel's
    picks, or the playlist's songs); the rest are files in the journey folder."""
    return {
        "heuristic": ["seed"],
        "panel": ["seed", "heuristic.md"],
        "dates": ["songs"],
        "eras": ["dates.json", "heuristic.md"],
        "front": ["heuristic.md", "eras.json", "panel.json" if doc["panel"] else "songs"],
    }[stage.name]


def _read_json(f):
    try:
        return json.loads(Path(f).read_text())
    except ValueError as e:
        raise Problem(f"{f} isn't JSON: {e}") from None


def songs(doc):
    """The journey's songs: the panel's picks, counted now, or the playlist's songs. None when the
    panel isn't there yet or doesn't validate."""
    if not doc["panel"]:
        return list(doc["seed"].get("songs") or [])
    f = journeytoml.folder(doc["slug"]) / PANEL.file
    if not f.is_file():
        return None
    try:
        p = _read_json(f)
    except Problem:
        return None
    if panel.problems(p):
        return None
    return panel.count(p)[0]


def input_hash(doc, name):
    if name == "seed":
        return hashing.of_value(doc["seed"])
    if name == "songs":
        s = songs(doc)
        return None if s is None else hashing.of_value(s)
    return hashing.of_file(journeytoml.folder(doc["slug"]) / name)


def output_problems(stage, obj):
    if stage.schema is None:
        return [] if obj.strip() else ["$: the heuristic is empty"]
    if stage is PANEL:
        return panel.problems(obj)
    return validate(obj, schema(stage.schema))


def stage_status(doc, stage):
    d = journeytoml.folder(doc["slug"])
    out = d / stage.file
    rec = doc["stages"].get(stage.name) or {}
    st = {"name": stage.name, "file": stage.file, "state": "ok", "stale": [], "unsigned": False, "waiting": [], "problems": []}
    now = {i: input_hash(doc, i) for i in inputs_of(doc, stage)}
    st["waiting"] = [i for i, h in now.items() if h is None]
    if not out.is_file():
        st["state"] = "empty"
        return st
    if stage.schema:
        try:
            st["problems"] = output_problems(stage, _read_json(out))
        except Problem as e:
            st["problems"] = [str(e)]
    else:
        st["problems"] = output_problems(stage, out.read_text())
    if not rec.get("output"):
        st["state"] = "draft"
        return st
    then = rec.get("inputs") or {}
    for i, h in now.items():
        if h is None:
            st["stale"].append(f"{i} is missing" if i != "songs" else "the panel has no valid picks to read")
        elif then.get(i) != h:
            st["stale"].append(f"{i} changed")
    used = rec.get("prompt")
    if stage.brief and used:
        cur = prompts.current(stage.brief)
        if cur and (cur.id, cur.version) != (used.get("id"), str(used.get("version"))):
            st["stale"].append(f"the {cur.id} brief changed (version {used.get('version')}, now {cur.version})")
    st["unsigned"] = hashing.of_file(out) != rec["output"]
    st["state"] = "stale" if st["stale"] else "unsigned" if st["unsigned"] else "ok"
    return st


def status(slug):
    doc = journeytoml.load(slug)
    out = {
        "slug": slug,
        "title": doc["title"],
        "folder": str(journeytoml.folder(slug)),
        "panel": doc["panel"],
        "stages": [stage_status(doc, s) for s in stages_of(doc)],
        "songs": songs(doc),
    }
    if doc["panel"]:
        f = journeytoml.folder(slug) / PANEL.file
        picks = close = None
        if f.is_file():
            try:
                p = _read_json(f)
                if not panel.problems(p):
                    picks, close = panel.count(p)
            except Problem:
                pass
        out["picks"], out["close_calls"] = picks, close
    out["ready"] = all(s["state"] == "ok" for s in out["stages"])
    return out


def _brief(stage):
    if stage.brief is None:
        return None, DATES_BRIEF
    try:
        p = prompts._load_prompt(stage.brief)
    except prompts.Missing as e:
        raise Problem(f"can't render the {stage.name} stage: {e}") from None
    return p, p.text


def _input_text(doc, name):
    d = journeytoml.folder(doc["slug"])
    if name == "seed":
        return journeytoml.dumps({"title": doc["title"], "seed": doc["seed"]})
    if name == "songs":
        from ..bank import Bank

        bank = Bank()
        cands = {}
        if doc["panel"]:
            cands = {c["song"]: c for c in _read_json(d / PANEL.file)["candidates"]}
        named = dict(zip(doc["seed"].get("songs") or [], doc["seed"].get("tracks") or [], strict=False))
        rows = []
        for sid in songs(doc):
            s = bank.songs.get(sid) or cands.get(sid) or {}
            row = {"song": sid, "title": s.get("title"), "artist": s.get("artist"), "bank_year": s.get("year")}
            if not s and sid in named:
                row["playlist"] = named[sid]
            rows.append({k: v for k, v in row.items() if v is not None})
        return json.dumps(rows, indent=1, ensure_ascii=False)
    return (d / name).read_text().strip()


def render(slug, name):
    """The paste-able prompt for one stage: the brief, the inputs, and what the reply must look like.
    Records which brief was shown in journey.toml, so a later version of it can mark the stage stale."""
    doc = journeytoml.load(slug)
    stage = by_name(doc, name)
    missing = [i for i in inputs_of(doc, stage) if input_hash(doc, i) is None]
    if missing:
        what = ", ".join("the panel's picks (panel.json, valid)" if i == "songs" else i for i in missing)
        raise Problem(f"the {name} stage reads {what}, and it isn't there yet")
    p, brief = _brief(stage)
    parts = [f"# The {name} stage for the journey {doc['title']!r}", "", brief.strip(), "", "## Inputs", ""]
    for i in inputs_of(doc, stage):
        label = {"seed": "The seed (journey.toml)", "songs": "The songs"}.get(i, i)
        parts += [f"### {label}", "", _input_text(doc, i), ""]
    parts += ["## Your reply", ""]
    if stage.schema:
        parts += [
            f"Reply with one JSON document for {stage.file} that matches this JSON Schema, and nothing else:",
            "",
            json.dumps(schema(stage.schema), indent=1, ensure_ascii=False),
        ]
    else:
        parts += [f"Reply with {stage.file} as Markdown, and nothing else."]
    if p:
        doc["stages"].setdefault(name, {})["rendered"] = {"id": p.id, "version": p.version}
        journeytoml.save(doc)
    return "\n".join(parts) + "\n"


FENCE = re.compile(r"^\s*```[\w-]*\s*\n(.*?)\n\s*```\s*$", re.S)


def _record(doc, stage, used):
    d = journeytoml.folder(doc["slug"])
    rec = doc["stages"].setdefault(stage.name, {})
    rec["inputs"] = {i: input_hash(doc, i) for i in inputs_of(doc, stage)}
    rec["output"] = hashing.of_file(d / stage.file)
    rec["accepted"] = dt.date.today().isoformat()
    if used:
        rec["prompt"] = {"id": used["id"], "version": str(used["version"])}
    journeytoml.save(doc)


def _used_brief(stage, rec):
    """The brief an output answers: the one last shown for this stage, else the current one."""
    if not stage.brief:
        return None
    if rec.get("rendered"):
        return rec["rendered"]
    cur = prompts.current(stage.brief)
    return {"id": cur.id, "version": cur.version} if cur else None


def accept_reply(slug, name, text):
    """Check a reply against the stage's schema, write it as the stage's output, and sign it off.
    Nothing is written when it doesn't validate; the error names the field."""
    doc = journeytoml.load(slug)
    stage = by_name(doc, name)
    out = journeytoml.folder(slug) / stage.file
    m = FENCE.match(text)
    if m:
        text = m.group(1)
    if stage.schema:
        try:
            obj = json.loads(text)
        except ValueError as e:
            raise Problem(f"{stage.file} not written: the reply isn't JSON ({e})") from None
        errs = output_problems(stage, obj)
        body = json.dumps(obj, indent=1, ensure_ascii=False) + "\n"
    else:
        errs = output_problems(stage, text)
        body = text.strip() + "\n"
    if errs:
        raise Problem(f"{stage.file} not written: " + "; ".join(errs))
    missing = [i for i in inputs_of(doc, stage) if input_hash(doc, i) is None]
    if missing:
        raise Problem(f"{stage.file} not written: the {name} stage reads {', '.join(missing)}, and it isn't there yet")
    paths.atomic_write(out, body)
    _record(doc, stage, _used_brief(stage, doc["stages"].get(name) or {}))
    return out


def accept(slug, name):
    """You looked at a stage's output as it stands, with its inputs as they stand, and it holds.
    Records the hashes and touches no artefact."""
    doc = journeytoml.load(slug)
    stage = by_name(doc, name)
    out = journeytoml.folder(slug) / stage.file
    if not out.is_file():
        raise Problem(f"nothing to accept: {out} doesn't exist. Write it, or use stage {name} --prompt")
    st = stage_status(doc, stage)
    if st["problems"]:
        raise Problem(f"{out} doesn't validate, so it can't be accepted: " + "; ".join(st["problems"]))
    if st["waiting"]:
        raise Problem(f"the {name} stage reads {', '.join(st['waiting'])}, and it isn't there yet")
    cur = prompts.current(stage.brief) if stage.brief else None
    _record(doc, stage, {"id": cur.id, "version": cur.version} if cur else (doc["stages"].get(name) or {}).get("prompt"))
    return st
