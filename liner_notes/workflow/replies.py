"""Reading a reply the way chats send them: a ```json block, often with a sentence before it, or bare
JSON. Anything else gets a reason in words, never the parser's own text."""

import json
import re

from .journeytoml import Problem

BLOCK = re.compile(r"```[^\n`]*\n(.*?)\n?```", re.S)


class Duplicate(ValueError):
    pass


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise Duplicate(k)
        out[k] = v
    return out


def unwrap(text):
    """The one fenced block in a reply, or the reply itself when there's none."""
    text = text.lstrip("﻿")
    blocks = BLOCK.findall(text)
    if len(blocks) > 1:
        raise Problem(f"the reply has {len(blocks)} fenced blocks; paste only the one with the answer")
    return blocks[0] if blocks else text


def parse_json(text, what):
    body = unwrap(text).strip()
    try:
        return json.loads(body, object_pairs_hook=_no_duplicates)
    except Duplicate as e:
        raise Problem(f"{what}: the reply gives the key {str(e)!r} twice in one object, so one of them would be lost") from None
    except ValueError as e:
        line, col = getattr(e, "lineno", None), getattr(e, "colno", None)
        if not body.startswith(("{", "[")) or e.msg == "Extra data":
            raise Problem(f"{what}: the reply has text around the JSON; paste only the JSON, or the ```json block") from None
        raise Problem(f"{what}: the JSON in the reply breaks at line {line}, column {col}; check the brackets and commas there") from None
