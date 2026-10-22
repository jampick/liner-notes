"""journey.toml: read with tomllib, written by the small emitter here (the standard library reads
TOML but doesn't write it). Only what journey.toml holds is supported: strings, numbers, booleans,
lists of those, and tables.

A command that changes journey.toml does it inside `editing(slug)`, which holds a lock file beside
it from load to save, so the console's worker and a command in a terminal can't lose each other's
update.
"""

import contextlib
import json
import re
import tomllib

from .. import paths
from ..check import validate
from . import schema

try:
    import fcntl
except ImportError:  # Windows: one process at a time is on you
    fcntl = None

NAME = "journey.toml"
LOCK = ".journey.toml.lock"
VERSION = 1
BARE = re.compile(r"[A-Za-z0-9_-]+")
FIELD = re.compile(r"^(\$[^\s:]*): (.*)$", re.S)


class Problem(Exception):
    """Something a person has to sort out. `errors` is a list of {field, message} for --json."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or [{"field": "", "message": message}]


def as_errors(lines):
    """`$.eras[0]: missing name` lines as {field, message}, with the missing key in the field."""
    out = []
    for line in lines:
        m = FIELD.match(line)
        if not m:
            out.append({"field": "", "message": line})
            continue
        at, msg = m.groups()
        miss = re.fullmatch(r"missing (\S+)", msg)
        out.append({"field": f"{at}.{miss.group(1)}", "message": "missing"} if miss else {"field": at, "message": msg})
    return out


def refuse(what, lines):
    """A Problem for a file that wasn't written, naming each field."""
    errors = as_errors(lines)
    text = "; ".join(f"{e['field']}: {e['message']}" if e["field"] else e["message"] for e in errors)
    return Problem(f"{what}: {text}", errors)


def work():
    """Where journeys in the making live. The bank never looks here, so a half-made journey can't
    reach list, play, the keys or check; `build <slug>` is what moves an assembled one into the bank."""
    return paths.user_data() / "work"


def folder(slug):
    return work() / slug


def _string(s):
    # JSON escapes the C0 controls; TOML also refuses DEL in a basic string.
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def _key(k):
    return k if BARE.fullmatch(k) else _string(k)


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"journey.toml can't hold {type(v).__name__}")


def _drop_none(d):
    return {k: _drop_none(v) if isinstance(v, dict) else v for k, v in d.items() if v is not None}


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
        raise Problem(f'no journey {slug!r} in the making: {f} doesn\'t exist. liner-notes new "<idea>" starts one')
    try:
        doc = tomllib.loads(f.read_text())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise Problem(f"{f} isn't valid TOML: {e}") from None
    errs = validate(doc, schema("journey-toml"))
    if errs:
        raise refuse(f"{f}", errs)
    doc.setdefault("stages", {})
    return doc


def save(doc):
    errs = validate(doc, schema("journey-toml"))
    if errs:
        raise refuse("not writing journey.toml", errs)
    text = dumps(doc) + "\n"
    if tomllib.loads(text) != _drop_none(doc):  # never leave a journey.toml that reads back differently
        raise Problem("not writing journey.toml: it wouldn't read back the same (a bug in liner-notes)")
    paths.atomic_write(folder(doc["slug"]) / NAME, text)


@contextlib.contextmanager
def locked(slug):
    d = folder(slug)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / LOCK, "a") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def editing(slug):
    """Load journey.toml under its lock, hand it over, and save it if the block finishes."""
    if not (folder(slug) / NAME).is_file():
        load(slug)  # raises the plain "no journey" message without making a folder
    with locked(slug):
        doc = load(slug)
        yield doc
        save(doc)


def slugs():
    """Every journey in the making."""
    return sorted(f.parent.name for f in work().glob(f"*/{NAME}")) if work().is_dir() else []
