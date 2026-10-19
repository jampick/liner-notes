"""Where things live.

The song bank and journeys are read from two places, user data first so a generated or edited
song overrides the shipped one:

  ~/.local/share/liner-notes/{songs,journeys,culture}   what you generate (LINER_NOTES_DATA)
  <repo>/{songs,journeys,culture}                        what ships with Liner Notes (LINER_NOTES_HOME)

Pictures that other people own (covers, Wikipedia images) are fetched into the cache and never
committed: ~/.cache/liner-notes (LINER_NOTES_CACHE). Built pages go to <repo>/dist in a checkout,
otherwise to the user data directory.
"""
import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def _env_path(name, default):
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


def home():
    """The shipped data root: a checkout, or LINER_NOTES_HOME."""
    return _env_path("LINER_NOTES_HOME", PACKAGE.parent)


def user_data():
    xdg = _env_path("XDG_DATA_HOME", Path.home() / ".local/share")
    return _env_path("LINER_NOTES_DATA", xdg / "liner-notes")


def cache():
    xdg = _env_path("XDG_CACHE_HOME", Path.home() / ".cache")
    return _env_path("LINER_NOTES_CACHE", xdg / "liner-notes")


def state():
    xdg = _env_path("XDG_STATE_HOME", Path.home() / ".local/state")
    return xdg / "liner-notes"


def roots():
    """Data roots, highest priority first."""
    out = []
    for r in (user_data(), home()):
        if r not in out:
            out.append(r)
    return out


def dist():
    if "LINER_NOTES_DIST" in os.environ:
        return Path(os.environ["LINER_NOTES_DIST"]).expanduser()
    h = home()
    return h / "dist" if (h / ".git").exists() or (h / "pyproject.toml").exists() else user_data() / "dist"


def cliamp_dir():
    return _env_path("CLIAMP_CONFIG_DIR", Path.home() / ".config/cliamp")
