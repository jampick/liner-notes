"""Making a journey one stage at a time: the folder of artefacts, their hashes, and the commands
`new`, `status`, `prompt`, `reply`, `accept`, `assemble` and `dates-apply`.

A journey in the making is a work folder in user data, ~/.local/share/liner-notes/work/<slug>/:

  journey.toml     title, seed, the panel's rule, and each stage's hashes (journeytoml.py)
  heuristic.md     what's in and out
  panel.json       the judges and every vote; picks and close calls are computed (panel.py)
  dates.json       each song's year with its source
  eras.json        names and bounds from a person, essays and scenes from a writer, one era a reply
  front.json       titles, lede, copy, the express route and threads
  journey.json     written by `assemble`

The bank never reads the work folder. `liner-notes build <slug>` copies an assembled journey into
your bank (publish.py), and that's when it goes live. Nothing here calls a model: `prompt` prints a
brief to paste anywhere and `reply` takes the answer back.
"""

from .. import paths
from ..bank import read_json


def schema(name):
    """One of the schemas in liner_notes/schemas, by name ("panel", "journey-toml", ...)."""
    return read_json(paths.schemas_dir() / f"{name}.schema.json")
