"""`liner-notes check`: validate the bank and the journeys, and say what still needs a person.

The schemas in schemas/ are JSON Schema (2020-12). The validator here covers the part of it they
use (type, required, properties, items, enum, pattern, minimum, minItems, minLength) so `check`
needs nothing outside the standard library. It also checks what a schema can't: every journey
song exists, threads point at songs in the journey, era photographs exist, our own text has no
em dashes, and every written song lists sources.
"""
import json
import re

from . import paths
from .bank import Bank, read_json

TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool, "null": type(None)}


def validate(doc, schema, at="$"):
    errs = []
    t = schema.get("type")
    if t:
        ok = any(isinstance(doc, TYPES[x]) and not (x in ("integer", "number") and isinstance(doc, bool)) for x in (t if isinstance(t, list) else [t]))
        if not ok:
            return [f"{at}: expected {t}"]
    if "enum" in schema and doc not in schema["enum"]:
        errs.append(f"{at}: {doc!r} not one of {schema['enum']}")
    if isinstance(doc, str):
        if "pattern" in schema and not re.search(schema["pattern"], doc):
            errs.append(f"{at}: {doc!r} doesn't match {schema['pattern']}")
        if len(doc) < schema.get("minLength", 0):
            errs.append(f"{at}: empty")
    if isinstance(doc, (int, float)) and "minimum" in schema and doc < schema["minimum"]:
        errs.append(f"{at}: below {schema['minimum']}")
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            if k not in doc:
                errs.append(f"{at}: missing {k}")
        for k, sub in (schema.get("properties") or {}).items():
            if k in doc:
                errs += validate(doc[k], sub, f"{at}.{k}")
    if isinstance(doc, list):
        if len(doc) < schema.get("minItems", 0):
            errs.append(f"{at}: needs at least {schema['minItems']}")
        if "items" in schema:
            for i, x in enumerate(doc):
                errs += validate(x, schema["items"], f"{at}[{i}]")
    return errs


def schemas():
    d = paths.home() / "schemas"
    return {n: read_json(d / f"{n}.schema.json") for n in ("song", "journey", "culture-year")}


def check(log=print):
    sc = schemas()
    bank = Bank()
    problems, notes = [], []
    for sid, s in bank.songs.items():
        f = bank.song_files[sid]
        problems += [f"{f.name}: {e}" for e in validate(s, sc["song"])]
        if f.stem != sid:
            problems.append(f"{f.name}: id {sid!r} doesn't match the file name")
        n = s.get("notes") or {}
        if n.get("made") and not n.get("sources"):
            problems.append(f"{f.name}: has notes but no sources")
        if "—" in json.dumps(n, ensure_ascii=False):
            problems.append(f"{f.name}: em dash in the notes")
    for r in bank.roots:
        for f in sorted((r / "culture/years").glob("*.json")):
            problems += [f"{f.name}: {e}" for e in validate(read_json(f), sc["culture-year"])]
    for slug, j in bank.journeys.items():
        jdir = bank.journey_dirs[slug]
        problems += [f"{slug}: {e}" for e in validate(j, sc["journey"])]
        songs = set(j["songs"])
        for sid in j["songs"]:
            if sid not in bank.songs:
                problems.append(f"{slug}: song {sid} isn't in the bank")
        for th in j.get("threads") or []:
            if th["from"] not in songs or th["to"] not in songs:
                problems.append(f"{slug}: thread {th['from']} -> {th['to']} leaves the journey")
        for e in j["eras"]:
            if e.get("image") and not (jdir / e["image"]).exists():
                problems.append(f"{slug}: era {e['n']} image {e['image']} is missing")
        quotes = (j.get("panel") or {}).get("quotes") or {}
        for sid in quotes:
            if sid not in songs:
                problems.append(f"{slug}: panel quote for {sid}, which isn't in the journey")
        written = [s for s in j["songs"] if (bank.songs.get(s, {}).get("notes") or {}).get("made")]
        reviewed = [s for s in j["songs"] if (bank.songs.get(s, {}).get("review") or {}).get("state") == "reviewed"]
        notes.append(f"{slug}: {len(j['songs'])} songs, {len(written)} written, {len(reviewed)} reviewed by a person")
    for n in notes:
        log(n)
    for p in problems:
        log("problem:", p)
    log(f"{len(bank.songs)} songs, {len(bank.journeys)} journeys, {len(problems)} problems")
    return 1 if problems else 0
