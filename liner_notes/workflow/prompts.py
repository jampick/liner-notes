"""The briefs each stage renders. Item 4 ships them as liner_notes/prompts/<name>.md, each opening
with TOML front matter between two lines of exactly `+++` (an `id` and a `version`), and a loader,
liner_notes.method.load_prompt. Until that lands, _load_prompt reads the same files itself.

LINER_NOTES_PROMPTS points at another folder of briefs, for tests and for trying a new brief.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import paths


@dataclass
class Prompt:
    id: str
    version: str
    text: str
    path: Path


class Missing(Exception):
    pass


def _read(path):
    raw = path.read_text()
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "+++":
        raise Missing(f"{path} doesn't start with +++ front matter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "+++")
    except StopIteration:
        raise Missing(f"{path}: the +++ front matter never closes") from None
    meta = tomllib.loads("".join(lines[1:end]))
    if "id" not in meta or "version" not in meta:
        raise Missing(f"{path}: the front matter needs an id and a version")
    return Prompt(str(meta["id"]), str(meta["version"]), "".join(lines[end + 1 :]).strip() + "\n", path)


def _load_prompt(name):
    """The brief called `name`: from LINER_NOTES_PROMPTS when set, else item 4's loader, else the package."""
    here = os.environ.get("LINER_NOTES_PROMPTS")
    if not here:
        try:
            from ..method import load_prompt
        except ImportError:
            pass
        else:
            p = load_prompt(name)
            return Prompt(str(p.id), str(p.version), p.text, Path(p.path))
    f = Path(here).expanduser() / f"{name}.md" if here else paths.PACKAGE / "prompts" / f"{name}.md"
    if not f.is_file():
        raise Missing(f"there's no {name} brief at {f}")
    return _read(f)


def current(name):
    """The brief, or None when it can't be read (so status can still say everything else)."""
    try:
        return _load_prompt(name)
    except Exception:  # a broken brief shows as "can't read the brief", not as a crash in status
        return None
