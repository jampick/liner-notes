"""Where things live.

The song bank and journeys are read from two places, user data first so a generated or edited
song overrides the shipped one:

  ~/.local/share/liner-notes/{songs,journeys,culture}   what you generate (LINER_NOTES_DATA)
  <repo>/{songs,journeys,culture}                        what ships with Liner Notes (LINER_NOTES_HOME)

Everything personal stays out of git, in the XDG directories:

  ~/.local/share/liner-notes/{lyrics,remixes,standins}   retimed lyrics, remix alignments, stand-in videos
  ~/.local/share/liner-notes/dist                        built pages (LINER_NOTES_DIST)
  ~/.cache/liner-notes/{covers,culture,artists,depth,video,audio,stage}   (LINER_NOTES_CACHE)
  ~/.local/state/liner-notes/{chromium,workspace,av-latency.json,offline} (LINER_NOTES_STATE)
  ~/.config/liner-notes/config.toml                      keys and workspaces (LINER_NOTES_CONFIG)
"""
import os
import tempfile
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
    return _env_path("LINER_NOTES_STATE", xdg / "liner-notes")


def config_file():
    xdg = _env_path("XDG_CONFIG_HOME", Path.home() / ".config")
    return _env_path("LINER_NOTES_CONFIG", xdg / "liner-notes" / "config.toml")


def user_systemd_dir():
    xdg = _env_path("XDG_CONFIG_HOME", Path.home() / ".config")
    return _env_path("LINER_NOTES_SYSTEMD_DIR", xdg / "systemd" / "user")


def roots():
    """Data roots, highest priority first."""
    out = []
    for r in (user_data(), home()):
        if r not in out:
            out.append(r)
    return out


def dist():
    """Built pages. They carry covers, so they live with your data, never in the repo."""
    return _env_path("LINER_NOTES_DIST", user_data() / "dist")


def cliamp_dir():
    return _env_path("CLIAMP_CONFIG_DIR", Path.home() / ".config/cliamp")


def pilot():
    """The journeys pilot checkout, read as a fallback until `liner-notes migrate-personal` has run."""
    return _env_path("LINER_NOTES_PILOT", Path.home() / "Projects/Music/journeys")


def pilot_cache():
    return _env_path("LINER_NOTES_PILOT_CACHE", Path.home() / ".cache/journeys")


def lyric_file(yt):
    """A retimed lyrics file for an upload, ours first, then the pilot's."""
    for d in (user_data() / "lyrics", pilot() / "lyrics"):
        p = d / f"{yt}.json"
        if p.is_file():
            return p
    return None


def video_file(yt):
    for d in (cache() / "video", pilot_cache() / "video"):
        p = d / f"{yt}.mp4"
        if p.is_file():
            return p
    return None


AUDIO = (".m4a", ".webm", ".opus", ".mp3", ".ogg", ".flac", ".wav", ".aac")


def audio_file(yt):
    for d in (cache() / "audio", cache() / "soundcloud", pilot_cache() / "audio", pilot_cache() / "soundcloud"):
        hit = next((p for p in sorted(d.glob(f"{yt}.*")) if p.suffix.lower() in AUDIO), None) if d.is_dir() else None
        if hit:
            return hit
    return None


def atomic_write(path, data):
    """Write a file so a reader sees the old one or the new one, never half of either."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = data.encode() if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
