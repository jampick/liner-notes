"""The stages of a journey, what each one reads, and where each stands.

For every stage journey.toml keeps the hash of each input as it was when the output was made, the
hash of the output then, the brief it answered, and who it came from: a chat's reply (`by =
"reply"`) or a person who looked and accepted it (`by = "accept"`). `status` compares those with the
files as they are now, in the console's words:

  not started  no output yet
  broken       the output doesn't parse or doesn't fit its schema
  draft        written by hand or by a reply, and nobody has accepted it
  stale        an input changed or went missing, or the brief changed, since it was made
  edited       accepted, then edited
  accepted     a person looked at it as it stands

Inputs are hashed when the prompt is rendered, so an input that moves while a chat is working on the
reply shows as stale the moment the reply comes back. Nothing reruns on its own: you rerun the stage
(`prompt`, then `reply`), or run `accept`, which says you looked and it still holds.
"""

import datetime as dt
import json
from dataclasses import dataclass
from functools import cached_property

from .. import paths
from ..check import style_problems, validate
from . import fields, hashing, journeytoml, panel, replies, schema
from .journeytoml import Problem, refuse


@dataclass(frozen=True)
class Stage:
    name: str
    file: str
    brief: str


HEURISTIC = Stage("heuristic", "heuristic.md", "heuristic")
PANEL = Stage("panel", "panel.json", "vote")
DATES = Stage("dates", "dates.json", "dates")
ERAS = Stage("eras", "eras.json", "era")
FRONT = Stage("front", "front.json", "front")
ALL = (HEURISTIC, PANEL, DATES, ERAS, FRONT)

LABEL = {"seed": "the seed in journey.toml", "songs": "the song list", "bank-years": "a song's year in the bank"}
LABEL["panel-quotes"] = "a panel quote in the era"
WAITING = {"songs": "the panel's picks", "bank-years": "the panel's picks", "seed": "journey.toml"}
COVERAGE = "isn't on this journey"
RANK = {"person": 3, "writer": 2, "musicbrainz": 1, None: 0}
# Fields that hold ids, URLs or numbers, not prose, so the house style doesn't read them.
NOT_PROSE = {"song", "judge", "j", "by", "url", "source", "sources", "image", "fonts", "kind", "from", "to", "years", "public"}


class Ctx:
    """One journey as a command sees it: journey.toml, the folder, and the bank (read once, when needed)."""

    def __init__(self, doc):
        self.doc = doc
        self.slug = doc["slug"]
        self.dir = journeytoml.folder(self.slug)

    @cached_property
    def bank(self):
        from ..bank import Bank

        return Bank()

    @property
    def rule(self):
        return self.doc.get("rule") or panel.DEFAULT_RULE

    def stages(self):
        return [s for s in ALL if s is not PANEL or self.doc["panel"]]

    def stage(self, name):
        for s in self.stages():
            if s.name == name:
                return s
        names = ", ".join(s.name for s in self.stages())
        raise Problem(f"{self.slug} has no stage {name!r}; its stages are {names}")

    def inputs(self, stage):
        """What a stage reads. "seed" is journey.toml's [seed]; "songs" the song list (the panel's
        picks, or the playlist's songs); "bank-years" each of those songs' year and year_source in
        the bank; the rest are files in the folder."""
        return {
            "heuristic": ["seed"],
            "panel": ["seed", "heuristic.md"],
            "dates": ["songs", "bank-years"],
            "eras": ["dates.json", "heuristic.md"],
            "front": ["heuristic.md", "eras.json", "panel.json" if self.doc["panel"] else "songs"],
        }[stage.name]

    def read(self, name):
        """A JSON file in the folder, or None when it isn't there or doesn't parse."""
        f = self.dir / name
        if not f.is_file():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8-sig"))
        except ValueError:
            return None

    @cached_property
    def panel_count(self):
        if not self.doc["panel"]:
            return None
        p = self.read(PANEL.file)
        if not isinstance(p, dict) or panel.problems(p, self.rule):
            return None
        return panel.count(p, self.rule)

    @cached_property
    def songs(self):
        """The journey's songs, or None when the panel isn't there yet or doesn't validate."""
        if not self.doc["panel"]:
            return list(self.doc["seed"].get("songs") or [])
        return self.panel_count["picks"] if self.panel_count else None

    def input_hash(self, name):
        if name == "seed":
            return hashing.of_value(self.doc["seed"])
        if name == "songs":
            return None if self.songs is None else hashing.of_value(self.songs)
        if name == "bank-years":
            if self.songs is None:
                return None
            years = [[s, (self.bank.songs.get(s) or {}).get("year"), (self.bank.songs.get(s) or {}).get("year_source")] for s in self.songs]
            return hashing.of_value(years)
        return hashing.of_file(self.dir / name)

    def input_hashes(self, stage, era=None):
        """Each input's hash. For one era, the panel quotes it carries count too, so editing a quote
        sends that era back to its writer."""
        out = {i: self.input_hash(i) for i in self.inputs(stage)}
        if stage is ERAS and era and self.doc["panel"]:
            out["panel-quotes"] = hashing.of_value([[q["song"], q["judge"], q["quote"]] for q in era_quotes(self, era)])
        return out


# the brief


def load_brief(stage):
    from ..method import MethodError, load_prompt

    try:
        return load_prompt(stage.brief)
    except (MethodError, OSError) as e:
        raise Problem(f"can't read the {stage.brief} brief: {e}") from None


def reply_schema(stage):
    """What a reply to this stage must look like. An era reply is one era, without its bounds."""
    if stage is HEURISTIC:
        return None
    if stage is ERAS:
        one = json.loads(json.dumps(schema("eras")["properties"]["eras"]["items"]))
        one["required"] = ["essay", "scene", "sources", "unverified", "panel_check"]
        for k in ("from", "to", "years", "image"):
            one["properties"].pop(k)
        return one
    return schema(stage.name)


def output_schema_text(stage):
    s = reply_schema(stage)
    return fields.field_list(s) if s else "Plain Markdown."


def brief_mark(stage):
    """{id, version, hash} of the brief as it reads now (house style included), or None."""
    try:
        p = load_brief(stage)
    except Problem:
        return None
    # Prompt.text is the house brief, the same on every machine; a creator's own style additions are
    # appended only by render(), so they never stale a stage. The reply's field list goes in too, since
    # it changes with the stage's schema.
    return {"id": p.id, "version": p.version, "hash": hashing.of_value([p.text, output_schema_text(stage)])}


def brief_stale(stage, used):
    if not used:
        return []
    try:
        p = load_brief(stage)
    except Problem as e:
        return [str(e)]
    now = brief_mark(stage)
    if now["version"] != used.get("version") or now["id"] != used.get("id"):
        return [f"the {p.id} brief changed (version {used.get('version')}, now {p.version})"]
    if now["hash"] != used.get("hash"):
        return [f"the {p.id} brief's text changed (the brief itself or the house style)"]
    return []


# checking an output


def style_lines(obj, at="$"):
    """The house style (style.toml) over every prose field, as `$.field: problem` lines."""
    from ..method import load_style

    style = load_style()
    out = []

    def walk(x, at, key=None):
        if isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{at}.{k}", k)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{at}[{i}]", key)
        elif isinstance(x, str) and key not in NOT_PROSE:
            out.extend(f"{at}: {p}" for p in style_problems(x, style))

    walk(obj, at)
    return out


def dates_problems(ctx, d):
    errs = validate(d, schema("dates"))
    if errs:
        return errs
    want = ctx.songs
    rows = [r["song"] for r in d["songs"]]
    for i, r in enumerate(d["songs"]):
        if r.get("corrected_from") is not None and not r.get("url"):
            errs.append(f"$.songs[{i}].url: missing; a corrected year needs the page that shows it")
        if want is not None and r["song"] not in want:
            missing = [s for s in want if s not in rows]
            errs.append(f"$.songs[{i}].song: {r['song']} {COVERAGE}" + (f"; missing: {', '.join(missing)}" if missing else ""))
        elif rows.index(r["song"]) != i:
            errs.append(f"$.songs[{i}].song: {r['song']} is listed twice")
    if want is not None:
        errs += [f"$.songs: no row for {s}" for s in want if s not in rows]
    return errs


def file_problems(ctx, stage, obj):
    """What's wrong with a stage's whole output file."""
    if stage is HEURISTIC:
        return [] if obj.strip() else ["$: the heuristic is empty"]
    if stage is PANEL:
        return panel.problems(obj, ctx.rule)
    if stage is DATES:
        return dates_problems(ctx, obj)
    if stage is FRONT:
        errs = validate(obj, schema("front"))
        if errs or ctx.songs is None:
            return errs
        songs = set(ctx.songs)
        for i, sid in enumerate((obj.get("express") or {}).get("songs") or []):
            if sid not in songs:
                errs.append(f"$.express.songs[{i}]: {sid} isn't on this journey")
        for i, t in enumerate(obj.get("threads") or []):
            for end in ("from", "to"):
                if t[end] not in songs:
                    errs.append(f"$.threads[{i}].{end}: {t[end]} isn't on this journey")
        return errs
    return validate(obj, schema(stage.name))


def read_output(ctx, stage):
    """(object, problems) for a stage's file as it is on disk."""
    f = ctx.dir / stage.file
    try:
        text = f.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return None, [f"{stage.file} isn't UTF-8 text"]
    if stage is HEURISTIC:
        return text, file_problems(ctx, stage, text) or style_lines(text)
    try:
        obj = json.loads(text)
    except ValueError as e:
        return None, [f"{stage.file} isn't JSON (line {e.lineno}, column {e.colno})"]
    # The house style, and your own additions to it, as the file is now: a hand edit is held to
    # the same bar as a reply.
    return obj, file_problems(ctx, stage, obj) or style_lines(obj)


# status


def next_era(ctx):
    eras = (ctx.read(ERAS.file) or {}).get("eras") or []
    for n, e in enumerate(eras, 1):
        if isinstance(e, dict) and not (e.get("essay") and e.get("scene")):
            return n
    return None


def stage_status(ctx, stage):
    out = ctx.dir / stage.file
    rec = ctx.doc["stages"].get(stage.name) or {}
    slug = ctx.slug
    st = {"name": stage.name, "file": stage.file, "state": "accepted", "stale": [], "edited": False}
    st |= {"by": rec.get("by"), "waiting": [], "problems": [], "behind": [], "next": ""}
    now = ctx.input_hashes(stage)
    st["waiting"] = [i for i, h in now.items() if h is None]
    rerun = f"liner-notes prompt {slug} {stage.name}" + (f" --era {next_era(ctx) or 1}" if stage is ERAS else "")
    accept = f"liner-notes accept {slug} {stage.name}"
    if not out.is_file():
        st["state"] = "not started"
        if st["waiting"]:
            st["next"] = "waiting on " + ", ".join(dict.fromkeys(WAITING.get(i, i) for i in st["waiting"]))
        elif stage is ERAS:
            st["next"] = f"write {out} with each era's name, from and to, then {rerun}"
        else:
            st["next"] = f"{rerun}, or write {stage.file} yourself and run {accept}"
        return st
    _, st["problems"] = read_output(ctx, stage)
    # Rows for songs that left the journey (or no row for one that joined) follow from a changed song
    # list: once the stage was made, that's staleness, and the problems stay listed beside it.
    follows = rec.get("output") and all(COVERAGE in p or p.startswith("$.songs: no row") for p in st["problems"])
    if st["problems"] and not follows:
        st["state"] = "broken"
        st["next"] = f"fix {stage.file} (the problems are listed), then {accept}"
        return st
    then = rec.get("inputs") or {}
    behind = []
    if rec.get("output"):
        # Eras are made one at a time, so each era keeps the inputs it was made from; the stage is
        # stale while any era is behind, and fresh again once each of those is redone or accepted.
        parts = rec.get("parts") if stage is ERAS and rec.get("parts") else {"all": then}
        changed = {}
        for part, hashes in sorted(parts.items()):
            here = ctx.input_hashes(stage, int(part[4:])) if part.startswith("era-") else now
            for i, h in here.items():
                if h is None:
                    changed[f"{LABEL.get(i, i)} is missing"] = 1
                elif hashes.get(i) != h:
                    changed[f"{LABEL.get(i, i)} changed"] = 1
                    if part.startswith("era-"):
                        behind.append(int(part[4:]))
        st["stale"] = list(changed)
        if LABEL["songs"] + " changed" in st["stale"]:  # the years follow the list; one reason is enough
            st["stale"] = [x for x in st["stale"] if x != LABEL["bank-years"] + " changed"]
        if "dates.json changed" in st["stale"]:  # a year moving a song between eras moves its quote too
            st["stale"] = [x for x in st["stale"] if x != LABEL["panel-quotes"] + " changed"]
        st["stale"] += brief_stale(stage, rec.get("prompt"))
    st["edited"] = bool(rec.get("output")) and rec.get("by") == "accept" and hashing.of_file(out) != rec["output"]
    if st["stale"]:
        st["state"] = "stale"
        if behind:
            eras = sorted(set(behind))
            rerun = ", ".join(f"liner-notes prompt {slug} eras --era {n}" for n in eras)
            st["behind"] = eras
        st["next"] = f"rerun it ({rerun}), or if it still holds: {accept}"
    elif rec.get("by") != "accept":
        st["state"] = "draft"
        st["next"] = f"read {stage.file}, then {accept}"
        if stage is ERAS and next_era(ctx):
            st["next"] = f"{rerun} for the next era, or when every era is written: {accept}"
    elif st["edited"]:
        st["state"] = "edited"
        st["next"] = f"edited after you accepted it: {accept}"
    elif stage is ERAS and next_era(ctx):
        st["next"] = f"era {next_era(ctx)} has no essay yet: {rerun}"
    return st


def status_of(ctx):
    from .assemble import check

    doc = ctx.doc
    stages = [stage_status(ctx, s) for s in ctx.stages()]
    out = {
        "slug": ctx.slug,
        "title": doc["title"],
        "folder": str(ctx.dir),
        "from": "playlist" if doc["seed"].get("from") else "idea",
        "panel": doc["panel"],
        "rule": ctx.rule if doc["panel"] else None,
        "stages": stages,
        "songs": ctx.songs,
        "songs_missing": [s for s in ctx.songs or [] if s not in ctx.bank.songs],
    }
    if doc["panel"]:
        c = ctx.panel_count or {}
        out["picks"], out["close_calls"], out["overrides"] = c.get("picks"), c.get("close_calls"), c.get("overrides")
        cands = [x.get("song") for x in (ctx.read(PANEL.file) or {}).get("candidates") or [] if isinstance(x, dict)]
        out["candidates_not_in_bank"] = [x for x in cands if x and x not in ctx.bank.songs]
    holds, problems, _ = check(ctx, stages)
    out["holds"], out["problems"] = holds, problems
    out["ready"] = not holds and not problems
    return out


def status(slug):
    return status_of(Ctx(journeytoml.load(slug)))


# prompt


def _rows(ctx, songs, dates=None):
    rows = []
    for sid in songs:
        s = ctx.bank.songs.get(sid) or {}
        year = (dates or {}).get(sid, {}).get("year", s.get("year"))
        row = {"song": sid, "title": s.get("title"), "artist": s.get("artist"), "year": year}
        rows.append({k: v for k, v in row.items() if v is not None})
    return rows


def _dump(x):
    return json.dumps(x, indent=1, ensure_ascii=False)


def _section(title, body):
    return f"### {title}\n\n{body.strip()}\n"


WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve")


def count_word(n):
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def rule_text(rule):
    veto = " and no veto. {y} yes votes and a veto is a close call, not a pick." if rule.get("veto") else "."
    return f"A song gets in with at least {rule['yes']} yes votes of {rule['of']}" + veto.format(y=rule["yes"])


def era_quotes(ctx, era):
    """The panel quotes the era writer is shown for era N: one per song in the era that has one."""
    if not ctx.doc["panel"]:
        return []
    eras = (ctx.read(ERAS.file) or {}).get("eras") or []
    if not 1 <= era <= len(eras):
        return []
    e = eras[era - 1]
    dates = {r["song"]: r for r in (ctx.read(DATES.file) or {}).get("songs") or []}
    p = ctx.read(PANEL.file) or {}
    judges = p.get("judges") or {}
    quotes = {c["song"]: c["quote"] for c in p.get("candidates") or [] if isinstance(c, dict) and c.get("quote")}
    out = []
    for r in _rows(ctx, ctx.songs or [], dates):
        q = quotes.get(r["song"])
        if q and r.get("year") is not None and e["from"] <= r["year"] <= e["to"]:
            out.append({"song": r["song"], "judge": q["j"], "judge_name": (judges.get(q["j"]) or {}).get("name"), "quote": q["q"]})
    return out


def inputs_text(ctx, stage, era=None):
    doc = ctx.doc
    seed = doc["seed"]
    parts = []
    if seed.get("from"):
        names = seed.get("tracks") or seed.get("songs") or []
        parts.append(_section("The journey", f"{doc['title']}, from the playlist {seed['from']}:\n\n" + "\n".join(f"- {n}" for n in names)))
    else:
        parts.append(_section("The journey", f"{doc['title']} (the idea: {seed.get('idea') or doc['title']})"))
    if stage is HEURISTIC:
        return "\n".join(parts)
    if stage is DATES:
        rows = []
        for sid in ctx.songs:
            s = ctx.bank.songs.get(sid) or {}
            rows.append(
                {"song": sid, "title": s.get("title"), "artist": s.get("artist"), "year": s.get("year"), "source": s.get("year_source")}
            )
        return "\n".join([parts[0], _section("The songs, with the year each has now", _dump(rows))])
    parts.append(_section("The heuristic", (ctx.dir / HEURISTIC.file).read_text()))
    if stage is PANEL:
        r = ctx.rule
        veto = ", and one judge holds the veto" if r.get("veto") else ""
        parts.append(_section("The rule", f"A song gets in with {r['yes']} yes votes of {r['of']}{veto}. Seat {r['of']} judges."))
        if not doc["seed"].get("from"):
            songs = sorted(ctx.bank.songs.items(), key=lambda kv: (kv[1].get("artist", ""), kv[1].get("title", "")))
            rows = [f"- `{sid}`: {x.get('artist')}, {x.get('title')} ({x.get('year') or 'no year'})" for sid, x in songs]
            parts.append(
                _section(
                    "Songs the bank has",
                    "Name these by their id. A song from outside this list can stay on the ballot, "
                    "but as a pick it can't be built until the song is added to the bank.\n\n" + "\n".join(rows),
                )
            )
        return "\n".join(parts)
    dates = {r["song"]: r for r in (ctx.read(DATES.file) or {}).get("songs") or []}
    quotes, judges = {}, {}
    if doc["panel"]:
        p = ctx.read(PANEL.file) or {}
        judges = p.get("judges") or {}
        quotes = {c["song"]: c.get("quote") for c in p.get("candidates") or [] if c.get("quote")}
    if stage is ERAS:
        eras = ctx.read(ERAS.file)["eras"]
        e = eras[era - 1]
        name = e.get("name") or "(blank: name it)"
        parts.append(_section("This era", f"Era {era} of {len(eras)}: {name}, {e['from']} to {e['to']}"))
        others = [f"- Era {n}: {x.get('name') or '(no name yet)'}, {x['from']} to {x['to']}" for n, x in enumerate(eras, 1) if n != era]
        if others:
            parts.append(_section("The other eras", "\n".join(others)))
        shown = {q["song"]: q for q in era_quotes(ctx, era)}
        rows = []
        for r in _rows(ctx, ctx.songs, dates):
            if r.get("year") is not None and e["from"] <= r["year"] <= e["to"]:
                if r["song"] in shown:
                    q = shown[r["song"]]
                    r["panel_quote"] = {"judge": q["judge"], "judge_name": q["judge_name"], "quote": q["quote"]}
                rows.append(r)
        parts.append(_section("The songs in this era", _dump(rows)))
        return "\n".join(parts)
    # front
    eras = (ctx.read(ERAS.file) or {}).get("eras") or []
    parts.append(_section("The eras", _dump([{k: e.get(k) for k in ("name", "from", "to", "essay")} for e in eras])))
    rows = _rows(ctx, ctx.songs, dates)
    for r in rows:
        notes = (ctx.bank.songs.get(r["song"]) or {}).get("notes") or {}
        if notes.get("made"):
            r["made"], r["context"] = notes.get("made"), notes.get("context", "")
        if quotes.get(r["song"]):
            r["panel_quote"] = {"judge": quotes[r["song"]]["j"], "quote": quotes[r["song"]]["q"]}
    parts.append(_section("The songs", _dump(rows)))
    if judges:
        parts.append(_section("The panel", _dump({k: {x: v.get(x) for x in ("name", "title", "lens")} for k, v in judges.items()})))
    return "\n".join(parts)


def _part(stage, era):
    if stage is ERAS:
        if not era:
            raise Problem("eras go one at a time: add --era N")
        return f"era-{era}"
    if era:
        raise Problem(f"--era is only for the eras stage, not {stage.name}")
    return "all"


def _check_era(ctx, era):
    eras = (ctx.read(ERAS.file) or {}).get("eras")
    f = ctx.dir / ERAS.file
    if not eras:
        raise Problem(
            f'eras.json needs the era names and bounds first: write {f} as {{"eras": [{{"name": ..., "from": 1976, "to": 1978}}, ...]}}'
        )
    if not 1 <= era <= len(eras):
        raise Problem(f"there's no era {era}: eras.json has {len(eras)}")
    return eras


def render(slug, name, era=None):
    """The paste-able prompt for one stage: the brief, filled with the inputs and the reply's fields.
    Records which brief was shown and the inputs' hashes then, so a reply made from them can be
    told apart from the files as they are when it comes back."""
    with journeytoml.editing(slug) as doc:
        ctx = Ctx(doc)
        stage = ctx.stage(name)
        part = _part(stage, era)
        now = ctx.input_hashes(stage, era)
        missing = [i for i, h in now.items() if h is None]
        if missing:
            what = ", ".join(
                dict.fromkeys("the panel's picks (a valid panel.json)" if i in ("songs", "bank-years") else i for i in missing)
            )
            raise Problem(f"the {name} stage reads {what}, and it isn't there yet")
        if stage is ERAS:
            _check_era(ctx, era)
        p = load_brief(stage)
        values = {"inputs": inputs_text(ctx, stage, era), "output_schema": output_schema_text(stage)}
        extra = {"rule": rule_text(ctx.rule), "judge_count": count_word(ctx.rule["of"])} if stage is PANEL else {}
        text = p.render(**values, **extra)
        for k, v in values.items():
            if "{" + k + "}" not in p.text and not (stage is HEURISTIC and k == "output_schema"):
                text += f"\n\n{v}\n"
        mark = brief_mark(stage)
        doc["stages"].setdefault(name, {}).setdefault("rendered", {})[part] = {**mark, "inputs": now}
    return {"text": text.rstrip() + "\n", "brief": {"id": p.id, "version": p.version}}


# reply and accept


def _record(ctx, stage, by, inputs, prompt, part=None):
    rec = ctx.doc["stages"].setdefault(stage.name, {})
    if stage is ERAS:
        # Each era keeps the inputs it was made from. An accept vouches for every era as it stands.
        if by == "accept":
            n = len((ctx.read(ERAS.file) or {}).get("eras") or [])
            rec["parts"] = {f"era-{k}": ctx.input_hashes(stage, k) for k in range(1, n + 1)}
        else:
            rec.setdefault("parts", {})[part] = dict(inputs)
    rec["by"] = by
    rec["on"] = dt.date.today().isoformat()
    rec["inputs"] = inputs
    rec["output"] = hashing.of_file(ctx.dir / stage.file)
    if prompt:
        rec["prompt"] = {k: prompt[k] for k in ("id", "version", "hash")}
    rendered = {} if by == "accept" else dict(rec.get("rendered") or {})
    if part:
        rendered.pop(part, None)
    if rendered:
        rec["rendered"] = rendered
    else:
        rec.pop("rendered", None)


def _summary(ctx, stage, obj, era=None):
    if stage is HEURISTIC:
        return f"{len([x for x in obj.splitlines() if x.strip()])} lines"
    if stage is PANEL:
        c = panel.count(obj, ctx.rule)
        titles = {x["song"]: x["title"] for x in obj["candidates"]}
        off = [x["song"] for x in obj["candidates"] if x["song"] not in ctx.bank.songs]
        picks_off = [titles[s] for s in c["picks"] if s in off]
        more = f", {len(off)} not in your bank" + (f" ({len(picks_off)} of them picks: {', '.join(picks_off)})" if picks_off else "")
        head = f"{len(obj['candidates'])} candidates{more}; {len(c['picks'])} picks, {len(c['close_calls'])} close calls"
        return head + ": " + ", ".join(titles[s] for s in c["picks"])
    if stage is DATES:
        fixed = [r for r in obj["songs"] if r.get("corrected_from") is not None]
        names = {s: (ctx.bank.songs.get(s) or {}).get("title", s) for s in ctx.songs}
        more = ": " + ", ".join(f"{names.get(r['song'], r['song'])} {r['corrected_from']} to {r['year']}" for r in fixed) if fixed else ""
        return f"{len(obj['songs'])} years, {len(fixed)} corrected{more}"
    if stage is ERAS:
        e = obj["eras"][era - 1]
        return f"era {era} of {len(obj['eras'])}, {e.get('name')}: {len(e['essay'].split())} words, {len(e.get('sources') or [])} sources"
    return f"{obj['title']}: {obj['kicker']}"


def _merge_era(ctx, reply, era):
    eras_doc = ctx.read(ERAS.file)
    eras = _check_era(ctx, era)
    if not isinstance(reply, dict):
        return None, ["$: an era reply is one JSON object"]
    errs = validate(reply, reply_schema(ERAS))
    mine = eras[era - 1]
    for k in ("from", "to"):
        if k in reply and reply[k] != mine.get(k):
            errs.append(f"$.{k}: era {era} runs {mine.get('from')} to {mine.get('to')}; a reply can't move its bounds")
    if errs:
        return None, errs
    rows = reply.get("panel_check") or []
    shown = {(q["song"], q["judge"]): q["quote"] for q in era_quotes(ctx, era)}
    for i, r in enumerate(rows):
        want = shown.get((r.get("song"), r.get("judge")))
        if want is not None and (r.get("quote") or "").strip() != want.strip():
            errs.append(f"$.panel_check[{i}].quote: {r.get('quote')!r} isn't the quote as it was shown; copy it exactly: {want!r}")
    for q in era_quotes(ctx, era):
        if not any(r.get("song") == q["song"] and r.get("judge") == q["judge"] for r in rows):
            errs.append(
                f"$.panel_check: no row for {q['song']}'s quote from {q['judge']} ({q['quote']!r}); every quote shown gets a verdict"
            )
    if errs:
        return None, errs
    merged = dict(mine)
    for k in ("essay", "scene", "sources", "unverified", "panel_check"):
        merged[k] = reply[k]
    if not (mine.get("name") or "").strip() and (reply.get("name") or "").strip():
        merged["name"] = reply["name"].strip()
    new = json.loads(json.dumps(eras_doc))
    new["eras"][era - 1] = merged
    return new, validate(new, schema("eras"))


def accept_reply(slug, name, text, era=None):
    """Check a reply, write it as the stage's output (one era of eras.json, with --era), and record
    it as a reply: a draft until someone accepts it. Nothing is written when it doesn't pass; the
    error names each field."""
    with journeytoml.editing(slug) as doc:
        ctx = Ctx(doc)
        stage = ctx.stage(name)
        part = _part(stage, era)
        out = ctx.dir / stage.file
        what = f"{stage.file} not written"
        missing = [i for i, h in ctx.input_hashes(stage).items() if h is None]
        if missing:
            said = ", ".join(dict.fromkeys("the panel's picks" if i in ("songs", "bank-years") else i for i in missing))
            raise Problem(f"{what}: the {name} stage reads {said}, and it isn't there yet")
        if stage is HEURISTIC:
            obj = replies.unwrap(text).strip()
            errs = file_problems(ctx, stage, obj) or style_lines(obj)
            data = obj + "\n"
        else:
            reply = replies.parse_json(text, what)
            if stage is ERAS:
                obj, errs = _merge_era(ctx, reply, era)
            else:
                obj = reply
                if stage is PANEL and isinstance(obj, dict):
                    panel.resolve(obj, ctx.bank)
                errs = file_problems(ctx, stage, obj)
            errs = errs or style_lines(reply)
            data = None if errs else json.dumps(obj, indent=1, ensure_ascii=False) + "\n"
        if errs:
            raise refuse(what, errs)
        rendered = ((doc["stages"].get(name) or {}).get("rendered") or {}).get(part)
        paths.atomic_write(out, data)
        if rendered:
            inputs, prompt = rendered["inputs"], rendered
        else:
            inputs, prompt = ctx.input_hashes(stage, era), brief_mark(stage)
        _record(ctx, stage, "reply", inputs, prompt, part)
        now = ctx.input_hashes(stage, era)
        changed = [LABEL.get(i, i) for i, h in inputs.items() if h != now.get(i)]
        summary = _summary(ctx, stage, obj, era)
    return {"file": str(out), "summary": summary, "state": "stale" if changed else "draft", "changed": changed}


def accept(slug, name):
    """You looked at a stage's output as it stands, with its inputs as they stand, and it holds.
    Records the hashes and the brief as it reads now, and touches no artefact."""
    with journeytoml.editing(slug) as doc:
        ctx = Ctx(doc)
        stage = ctx.stage(name)
        out = ctx.dir / stage.file
        if not out.is_file():
            raise Problem(f"nothing to accept: {out} doesn't exist. Write it, or run liner-notes prompt {slug} {name}")
        before = stage_status(ctx, stage)
        if before["problems"]:
            raise refuse(f"{out} doesn't pass, so it can't be accepted", before["problems"])
        if before["waiting"]:
            raise Problem(f"the {name} stage reads {', '.join(LABEL.get(i, i) for i in before['waiting'])}, and it isn't there yet")
        mark = brief_mark(stage) or (doc["stages"].get(name) or {}).get("prompt")
        _record(ctx, stage, "accept", ctx.input_hashes(stage), mark)
    return before
