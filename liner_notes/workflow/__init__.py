"""Making a journey one stage at a time: the folder of artefacts, their hashes, and the commands
`new`, `status`, `stage --prompt`, `stage --accept`, `accept` and `assemble`.

A journey in the making is a folder in user data, ~/.local/share/liner-notes/journeys/<slug>/:

  journey.toml     title, seed, and each stage's input and output hashes (journeytoml.py)
  heuristic.md     what's in and out
  panel.json       the rule, the judges and every vote; picks and close calls are computed (panel.py)
  dates.json       each song's year with its source
  eras.json        bounds, names, essays and scenes
  front.json       titles, lede, copy, the express route and threads
  journey.json     written by `assemble`, the file the page reads

Nothing here calls a model. `stage --prompt` prints a brief to paste anywhere, and `stage --accept`
takes the reply back. (liner_notes/stage.py is the visual stage page; it has nothing to do with this.)
"""

from .. import paths
from ..bank import read_json


def schema(name):
    """One of the schemas in liner_notes/schemas, by name ("panel", "journey-toml", ...)."""
    return read_json(paths.schemas_dir() / f"{name}.schema.json")
