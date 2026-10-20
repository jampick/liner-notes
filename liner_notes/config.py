"""~/.config/liner-notes/config.toml, with the defaults filled in.

    [keys]                   short names for `liner-notes play`
    drift = "Downtempo Drift"

    [stage]
    workspace = 2            where the page opens the first time (then it remembers where you moved it)
    cliamp_workspace = 3     where a cold-started cliamp goes, silently

Anything missing falls back to the defaults below, so an empty or absent file is fine.
"""
import tomllib

from . import paths

KEYS = {
    "sophisti": "Sophisti-Pop",
    "soulrap": "Soul Rap Covers",
    "yacht": "Yacht Rock",
    "drift": "Downtempo Drift",
    "temperton": "Rod Temperton",
    "yachthouse": "Yacht House",
}
DEFAULTS = {
    "keys": KEYS,
    "stage": {"workspace": 2, "cliamp_workspace": 3, "browser": ""},
    "depth": {"model": "~/.local/state/omarchy/astro-arc/motion/depth-anything-v2-small.onnx"},
}


def load(path=None):
    f = path or paths.config_file()
    try:
        mine = tomllib.loads(f.read_text())
    except FileNotFoundError:
        mine = {}
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"liner-notes: {f}: {e}") from e
    out = {}
    for k, v in DEFAULTS.items():
        out[k] = {**v, **(mine.get(k) or {})}
    for k, v in mine.items():
        out.setdefault(k, v)
    return out
