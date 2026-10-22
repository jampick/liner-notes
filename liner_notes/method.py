"""The method as files: the briefs in prompts/ and the house style in style.toml.

A prompt file starts with TOML front matter between two lines of exactly `+++`, with at least
`id` (the file's name without .md) and `version` (an integer, bumped when the brief changes):

    +++
    id = "era"
    version = 1
    +++
    You write ...

The body can hold placeholders, a name in braces like `{inputs}`. `{style_rules}` is filled from
style.toml when the prompt loads, so the prompts and `check` read one list. The rest are left for
whoever renders the prompt: `Prompt.render(inputs=..., output_schema=...)` replaces only the names it
is given, so the JSON examples in a brief, braces and all, come through untouched.

LINER_NOTES_PROMPTS names another folder of briefs, for tests and for trying a new brief. A brief
that folder doesn't have comes from the package.

You can add to the house style, never take from it: a style.toml beside your config.toml
(~/.config/liner-notes/style.toml) may list more `[words] banned` and more `[names] allowed`.

Both files are read again when they change on disk, so a long-running console sees an edit.
Since the loaded text has the style filled in, a stage that hashes `Prompt.text` sees a style
change as a changed brief.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from . import paths

PROMPTS = paths.PACKAGE / "prompts"
STYLE = paths.PACKAGE / "style.toml"
NAMES = ("song", "era", "heuristic", "vote", "front", "picture", "dates")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
FENCE = "+++"


class MethodError(ValueError):
    """A prompt or style file that doesn't follow the format."""


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    text: str
    path: Path
    meta: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def placeholders(self):
        """The names still to fill, in the order they first appear."""
        return list(dict.fromkeys(PLACEHOLDER.findall(self.text)))

    def render(self, **values):
        """The text with each given {name} replaced. Names not given stay as they are."""
        return PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), self.text)


@dataclass(frozen=True)
class Construction:
    id: str
    name: str
    pattern: re.Pattern
    say: str
    fix: str


@dataclass(frozen=True)
class Style:
    version: int
    banned: tuple
    words_say: str
    words_fix: str
    allowed: tuple
    constructions: tuple
    lyrics_min_words: int
    lyrics_cues: frozenset
    lyrics_before: int
    lyrics_after: int
    lyrics_not_lyric: frozenset
    lyrics_speech: frozenset
    lyrics_say: str
    lyrics_fix: str
    also: tuple
    path: Path
    word_patterns: tuple = ()  # (word, compiled regex of its forms), for check
    allowed_patterns: tuple = ()  # each allowed name on word boundaries, for check
    user_path: Path | None = None  # where a creator adds words and names
    words_line: str = ""

    def rules(self):
        """The style as the bulleted list a prompt carries."""
        lines = [c.say for c in self.constructions]
        lines.append(self.words_line)
        lines.append(self.lyrics_say)
        lines += list(self.also)
        return "\n".join(f"- {x}" for x in lines)


def _forms(word):
    """A banned word and its plain inflections (s, d, ed, ing, ly, ily), as one regex. Derived nouns
    (vibrancy, navigation) aren't covered; list them as words of their own if they matter."""
    stem = word[:-1] if word.endswith("e") else word
    alts = {word, word + "s", word + "d", word + "ed", stem + "ing", word + "ly", stem + "ly"}
    if word.endswith("y"):
        alts |= {word[:-1] + "ies", word[:-1] + "ily"}
    return r"\b(?:" + "|".join(sorted(map(re.escape, alts), key=len, reverse=True)) + r")\b"


def _name_pattern(name):
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)")


def user_style_path():
    """Your additions to the house style, beside config.toml."""
    return paths.config_file().parent / "style.toml"


def _user_additions(path):
    """(banned, allowed) from a creator's style file, which may only add."""
    try:
        d = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise MethodError(f"{path}: not TOML ({e})") from e
    extra = {k: v for k, v in d.items() if k not in ("words", "names")}
    extra |= {f"words.{k}": v for k, v in (d.get("words") or {}).items() if k != "banned"}
    extra |= {f"names.{k}": v for k, v in (d.get("names") or {}).items() if k != "allowed"}
    banned = (d.get("words") or {}).get("banned", [])
    allowed = (d.get("names") or {}).get("allowed", [])
    ok = all(isinstance(x, list) and all(isinstance(s, str) and s.strip() for s in x) for x in (banned, allowed))
    if extra or not ok:
        raise MethodError(
            f"{path}: your style file can only add to the house style: words.banned and names.allowed,"
            f" each a list of words or names ([words] banned = [...], [names] allowed = [...]); found {', '.join(extra) or 'something that is not a list'}"
        )
    return tuple(banned), tuple(allowed)


def parse_style(text, path=STYLE, user=None):
    """style.toml, plus a creator's additions from `user` (a path) when it exists."""
    try:
        d = tomllib.loads(text)
        w, lyr = d["words"], d["lyrics"]
        cons = tuple(
            Construction(c["id"], c.get("name", c["id"]), re.compile(c["pattern"]), c["say"], c["fix"])
            for c in d.get("constructions") or []
        )
        banned = tuple(w["banned"])
        allowed = tuple((d.get("names") or {}).get("allowed") or ())
        if user is not None and Path(user).is_file():
            more_banned, more_allowed = _user_additions(Path(user))
            banned += tuple(x for x in more_banned if x not in banned)
            allowed += tuple(x for x in more_allowed if x not in allowed)
        words_line = w["say"].format(words=", ".join(banned))
        style = Style(
            version=int(d["version"]),
            banned=banned,
            words_say=w["say"],
            words_fix=w["fix"],
            allowed=allowed,
            constructions=cons,
            lyrics_min_words=int(lyr["min_words"]),
            lyrics_cues=frozenset(x.lower() for x in lyr["cues"]),
            lyrics_before=int(lyr["before"]),
            lyrics_after=int(lyr["after"]),
            lyrics_not_lyric=frozenset(x.lower() for x in lyr.get("not_lyric") or ()),
            lyrics_speech=frozenset(x.lower() for x in lyr.get("speech") or ()),
            lyrics_say=lyr["say"],
            lyrics_fix=lyr["fix"],
            also=tuple((d.get("prompt") or {}).get("also") or ()),
            path=Path(path),
            word_patterns=tuple((x, re.compile(_forms(x), re.I)) for x in banned),
            allowed_patterns=tuple(_name_pattern(x) for x in allowed),
            user_path=Path(user) if user is not None else None,
            words_line=words_line,
        )
    except MethodError:
        raise
    except (tomllib.TOMLDecodeError, KeyError, IndexError, TypeError, ValueError, re.error) as e:
        raise MethodError(f"{path}: not a style file ({e.__class__.__name__}: {e})") from e
    return style


def _mtime(path):
    try:
        return Path(path).stat().st_mtime_ns
    except OSError:
        return None


@cache
def _style_at(path, mtime, user, user_mtime):
    return parse_style(Path(path).read_text(), path, user)


def load_style(path=None):
    """style.toml with your additions, parsed and compiled; read again when either file changes."""
    path, user = str(path or STYLE), str(user_style_path())
    return _style_at(path, _mtime(path), user, _mtime(user))


def parse_prompt(text, path, style=None):
    """A prompt file's text: front matter checked, {style_rules} filled."""
    name = Path(path).stem
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != FENCE or FENCE not in [x.rstrip("\r") for x in lines[1:]]:
        raise MethodError(f"{path}: a prompt starts with a line of {FENCE}, its TOML front matter, and another line of {FENCE}")
    end = 1 + [x.rstrip("\r") for x in lines[1:]].index(FENCE)
    try:
        meta = tomllib.loads("\n".join(lines[1:end]))
    except tomllib.TOMLDecodeError as e:
        raise MethodError(f"{path}: front matter isn't TOML ({e})") from e
    if meta.get("id") != name:
        raise MethodError(f'{path}: front matter needs id = "{name}", the file name')
    v = meta.get("version")
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise MethodError(f"{path}: front matter needs version = <a whole number from 1>")
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    body = body.replace("{style_rules}", (style or load_style()).rules())
    return Prompt(id=meta["id"], version=v, text=body, path=Path(path), meta=meta)


def prompt_path(name):
    """Where the brief called `name` is read from: LINER_NOTES_PROMPTS when it has one, else the package."""
    here = os.environ.get("LINER_NOTES_PROMPTS")
    if here:
        f = Path(here).expanduser() / f"{name}.md"
        if f.is_file():
            return f
    return PROMPTS / f"{name}.md"


@cache
def _prompt_at(path, mtime, style):
    return parse_prompt(Path(path).read_text(), Path(path), style)


def load_prompt(name, style=None):
    """One of the briefs, by name: song, era, heuristic, vote, front, picture or dates."""
    if name not in NAMES:
        raise MethodError(f"no prompt called {name!r}; the prompts are {', '.join(NAMES)}")
    p = prompt_path(name)
    if not p.is_file():
        raise MethodError(f"there's no {name} brief at {p}")
    return _prompt_at(str(p), _mtime(p), style or load_style())
