"""journey.toml: read with tomllib, written by the small emitter here (the standard library reads
TOML but doesn't write it). Only what journey.toml holds is supported: strings, numbers, booleans,
lists of those, and tables."""

import json
import re
import tomllib

from .. import paths
from ..check import validate
from . import schema

NAME = "journey.toml"
VERSION = 1
BARE = re.compile(r"[A-Za-z0-9_-]+")


class Problem(Exception):
    """Something a person has to sort out; the message says what."""


def folder(slug):
    return paths.user_data() / "journeys" / slug


def _key(k):
    return k if BARE.fullmatch(k) else json.dumps(k, ensure_ascii=False)


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"journey.toml can't hold {type(v).__name__}")


def dumps(doc, at=()):
    """TOML text for a dict: plain keys first, then each sub-table under its own header."""
    out = []
    plain = {k: v for k, v in doc.items() if not isinstance(v, dict) and v is not None}
    tables = {k: v for k, v in doc.items() if isinstance(v, dict)}
    if at and (plain or not tables):
        out.append("[" + ".".join(_key(k) for k in at) + "]")
    out += [f"{_key(k)} = {_value(v)}" for k, v in plain.items()]
    for k, v in tables.items():
        if out:
            out.append("")
        out.append(dumps(v, (*at, k)))
    return "\n".join(out)


def load(slug):
    f = folder(slug) / NAME
    if not f.is_file():
        raise Problem(f"no journey {slug!r} in the making: {f} doesn't exist. liner-notes new starts one")
    try:
        doc = tomllib.loads(f.read_text())
    except tomllib.TOMLDecodeError as e:
        raise Problem(f"{f} isn't valid TOML: {e}") from None
    errs = validate(doc, schema("journey-toml"))
    if errs:
        raise Problem(f"{f}: " + "; ".join(errs))
    doc.setdefault("stages", {})
    return doc


def save(doc):
    errs = validate(doc, schema("journey-toml"))
    if errs:
        raise Problem("not writing journey.toml: " + "; ".join(errs))
    paths.atomic_write(folder(doc["slug"]) / NAME, dumps(doc) + "\n")


def slugs():
    """Every journey in the making, in user data."""
    root = paths.user_data() / "journeys"
    return sorted(f.parent.name for f in root.glob(f"*/{NAME}")) if root.is_dir() else []
