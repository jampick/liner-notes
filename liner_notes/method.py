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
"""

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
    meta: dict = field(default_factory=dict)

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

    def rules(self):
        """The style as the bulleted list a prompt carries."""
        lines = [c.say for c in self.constructions]
        lines.append(self.words_say.format(words=", ".join(self.banned)))
        lines.append(self.lyrics_say)
        lines += list(self.also)
        return "\n".join(f"- {x}" for x in lines)


def _forms(word):
    """A banned word and its plain inflections, as one regex."""
    stem = word[:-1] if word.endswith("e") else word
    alts = {word, word + "s", word + "d", word + "ed", stem + "ing", word + "ly", stem + "ly"}
    if word.endswith("y"):
        alts.add(word[:-1] + "ies")
    return r"\b(?:" + "|".join(sorted(map(re.escape, alts), key=len, reverse=True)) + r")\b"


def parse_style(text, path=STYLE):
    try:
        d = tomllib.loads(text)
        w, lyr = d["words"], d["lyrics"]
        cons = tuple(Construction(c["id"], re.compile(c["pattern"]), c["say"], c["fix"]) for c in d.get("constructions") or [])
        style = Style(
            version=int(d["version"]),
            banned=tuple(w["banned"]),
            words_say=w["say"],
            words_fix=w["fix"],
            allowed=tuple((d.get("names") or {}).get("allowed") or ()),
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
            word_patterns=tuple((x, re.compile(_forms(x), re.I)) for x in w["banned"]),
        )
    except (tomllib.TOMLDecodeError, KeyError, TypeError, ValueError, re.error) as e:
        raise MethodError(f"{path}: not a style file ({e.__class__.__name__}: {e})") from e
    return style


@cache
def _style_at(path):
    return parse_style(Path(path).read_text(), path)


def load_style(path=None):
    """style.toml, parsed and with its patterns compiled."""
    return _style_at(str(path or STYLE))


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


def load_prompt(name, style=None):
    """One of the briefs in prompts/, by name: song, era, heuristic, vote, front, picture or dates."""
    if name not in NAMES:
        raise MethodError(f"no prompt called {name!r}; the prompts are {', '.join(NAMES)}")
    p = PROMPTS / f"{name}.md"
    return parse_prompt(p.read_text(), p, style)
