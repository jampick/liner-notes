"""Where cliamp's sound goes, and how late it gets there.

The A/V latency table (written by an avsync measurement, seconds per route) lives in
state()/av-latency.json, with the pilot's ~/.local/state/journeys/av-latency.json as a fallback.
The route is the PulseAudio/PipeWire sink cliamp's stream plays into. When that's Easy Effects,
the sound goes on to the device in its [StreamOutputs] outputDevice and takes the effects' own
delay with it, so the route is "easyeffects_sink>" plus that device.

Finding the route costs three pactl calls, so Latency looks it up at most once a minute and never
per request. A new measurement shows up within that minute.
"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .. import paths

EE_CONFIG = Path.home() / ".config/easyeffects/db/easyeffectsrc"
PILOT_TABLE = Path.home() / ".local/state/journeys/av-latency.json"


def tables():
    return [paths.state() / "av-latency.json", PILOT_TABLE]


def _pactl(*args):
    exe = shutil.which("pactl")
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _ee_output():
    try:
        group = None
        for line in EE_CONFIG.read_text().splitlines():
            if line.startswith("["):
                group = line.strip("[]")
            elif group == "StreamOutputs" and line.startswith("outputDevice="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return _pactl("get-default-sink").strip()


def route():
    """The route key for cliamp's stream, or "" when there's no sound server to ask."""
    try:
        sinks = {s["index"]: s["name"] for s in json.loads(_pactl("-f", "json", "list", "short", "sinks") or "[]")}
        inputs = json.loads(_pactl("-f", "json", "list", "sink-inputs") or "[]")
    except (ValueError, KeyError, TypeError):
        return ""
    sink = None
    for s in inputs:
        if "cliamp" in ((s.get("properties") or {}).get("application.name") or ""):
            sink = sinks.get(s.get("sink"))
            break
    sink = sink or _pactl("get-default-sink").strip()
    if sink == "easyeffects_sink":
        return f"easyeffects_sink>{_ee_output()}"
    return sink


class Latency:
    """The measured latency for the current route, looked up again every `every` seconds at most."""

    def __init__(self, every=60.0, find_route=None, files=None):
        self.every, self.find_route, self.files = every, find_route, files
        self.lock = threading.Lock()
        self.value, self.key, self.at = 0.0, "", None

    def _lookup(self, key):
        """The first table that has this route wins."""
        for f in self.files or tables():
            try:
                t = json.loads(Path(f).read_text())
                if isinstance(t, dict) and key in t:
                    return float(t[key])
            except (OSError, ValueError, TypeError):
                continue
        return 0.0

    def get(self):
        with self.lock:
            now = time.monotonic()
            if self.at is not None and now - self.at < self.every:
                return self.value
            self.at = now
            self.key = (self.find_route or route)()
            self.value = self._lookup(self.key) if self.key else 0.0
            return self.value
