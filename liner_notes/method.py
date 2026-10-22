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
(~/.config/liner-notes/style.toml, or LINER_NOTES_STYLE) may list more `[words] banned` and more
`[names] allowed`. Your additions judge your own songs and journeys; the shipped bank is judged by
the house style alone.

Which text to hash: `Prompt.text` is the brief with the house style filled in, the same on every
machine, so a stage records a hash of that. A house style change moves it; your additions don't.
`Prompt.render(...)` is what you send: it fills the placeholders and, when you have additions,
appends them in a short section of their own.

Both files are read again when they change on disk, so a long-running console sees an edit. The
caches keep one entry per file.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field
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
        """What to send: the text with each given {name} replaced (names not given stay as they are),
        plus your additions to the style, if you have any, in a section of their own."""
        out = PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), self.text)
        return out + load_style().additions()


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
    extra_banned: tuple = ()  # what the creator's file added
    extra_allowed: tuple = ()
    lyrics_sung: frozenset = frozenset()

    def additions(self):
        """The creator's additions as a short section for the end of a prompt, or ""."""
        lines = []
        if self.extra_banned:
            lines.append(f"- Also none of these words: {', '.join(self.extra_banned)}.")
        if self.extra_allowed:
            lines.append(f"- These names are fine as they are: {', '.join(self.extra_allowed)}.")
        return "\n\n## Your additions to the style\n\n" + "\n".join(lines) + "\n" if lines else ""

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
    """Your additions to the house style: LINER_NOTES_STYLE, or style.toml beside config.toml."""
    env = os.environ.get("LINER_NOTES_STYLE")
    return Path(env).expanduser() if env else paths.config_file().parent / "style.toml"


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


def _check_allowed(name, cons, word_patterns, path):
    """An allowed name has to be a name: capitalised, not a banned word alone, and no dash rule in it."""
    if not any(c.isupper() for c in name):
        raise MethodError(
            f"{path}: {name!r} in [names] allowed has no capital letter, so it's a word, not a name; the house style keeps it"
        )
    if any(p.fullmatch(name) for _, p in word_patterns):
        raise MethodError(f"{path}: {name!r} in [names] allowed is a banned word on its own; allow the full name it's part of")
    if any(c.pattern.search(f" {name} ") for c in cons):
        raise MethodError(f"{path}: {name!r} in [names] allowed would let a house rule through; take it out")


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
        extra_banned, extra_allowed = (), ()
        if user is not None and Path(user).is_file():
            more_banned, more_allowed = _user_additions(Path(user))
            house_words = tuple((x, re.compile(_forms(x), re.I)) for x in banned)
            for name in more_allowed:
                _check_allowed(name, cons, house_words, user)
            extra_banned = tuple(x for x in more_banned if x not in banned)
            extra_allowed = tuple(x for x in more_allowed if x not in allowed)
            banned += extra_banned
            allowed += extra_allowed
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
            extra_banned=extra_banned,
            extra_allowed=extra_allowed,
            lyrics_sung=frozenset(x.lower() for x in lyr.get("sung") or ()),
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


_STYLES = {}  # (house path, user path or None) -> ((mtimes), Style): one entry per pair of files
_PROMPTS = {}  # prompt path -> ((mtime, house style), Prompt)


def load_style(path=None, house=False):
    """style.toml with your additions, parsed and compiled; read again when either file changes.
    house=True leaves your additions out: that's the style the shipped bank and Prompt.text use."""
    path = str(path or STYLE)
    user = None if house else str(user_style_path())
    key, stamp = (path, user), (_mtime(path), user and _mtime(user))
    hit = _STYLES.get(key)
    if not hit or hit[0] != stamp:
        _STYLES[key] = hit = (stamp, parse_style(Path(path).read_text(), path, user))
    return hit[1]


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
    body = body.replace("{style_rules}", (style or load_style(house=True)).rules())
    return Prompt(id=meta["id"], version=v, text=body, path=Path(path), meta=meta)


def prompt_path(name):
    """Where the brief called `name` is read from: LINER_NOTES_PROMPTS when it has one, else the package."""
    here = os.environ.get("LINER_NOTES_PROMPTS")
    if here:
        f = Path(here).expanduser() / f"{name}.md"
        if f.is_file():
            return f
    return PROMPTS / f"{name}.md"


def load_prompt(name, style=None):
    """One of the briefs, by name: song, era, heuristic, vote, front, picture or dates."""
    if name not in NAMES:
        raise MethodError(f"no prompt called {name!r}; the prompts are {', '.join(NAMES)}")
    p = prompt_path(name)
    if not p.is_file():
        raise MethodError(f"there's no {name} brief at {p}")
    if style is not None:
        return parse_prompt(p.read_text(), p, style)
    house = load_style(house=True)
    stamp = (_mtime(p), house)
    hit = _PROMPTS.get(str(p))
    if not hit or hit[0] != stamp:
        _PROMPTS[str(p)] = hit = (stamp, parse_prompt(p.read_text(), p, house))
    return hit[1]
