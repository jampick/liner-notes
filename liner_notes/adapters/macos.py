"""macOS now-playing through `media-control stream` (https://github.com/ungive/media-control).

Status: documented stub. The parser is tested against the documented output; the adapter hasn't
run on a Mac yet. macOS 15.4 closed MediaRemote to ordinary apps; media-control gets around that
with a helper that Apple's own Perl is still allowed to load, which may not last.

Contract: `media-control stream` prints one JSON object per line, {"type": "data", "diff": bool,
"payload": {...}}. With diff true the payload holds only changed keys, so we merge. Keys used:
title, artist, album, duration, elapsedTime, timestamp (ISO 8601, when elapsedTime was true),
playing, bundleIdentifier.
"""
import datetime as dt
import json
import shutil
import subprocess
import time

from . import Adapter, NowPlaying


def _ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return time.time()


def parse(state, line):
    """Merge one stream line into `state` (a dict) and return the NowPlaying it now describes."""
    msg = json.loads(line)
    if msg.get("type") != "data":
        return None
    p = msg.get("payload") or {}
    if not msg.get("diff"):
        state.clear()
    state.update(p)
    if not state.get("title"):
        return None
    ids = {"app": state["bundleIdentifier"]} if state.get("bundleIdentifier") else {}
    return NowPlaying(title=state["title"], artist=state.get("artist") or "", album=state.get("album") or "",
                      duration=float(state.get("duration") or 0), position=float(state.get("elapsedTime") or 0),
                      position_at=_ts(state.get("timestamp")), state="playing" if state.get("playing") else "paused",
                      source="macos", ids=ids)


class MacAdapter(Adapter):
    name = "macos"
    capabilities = frozenset({"events"})

    def available(self):
        return bool(shutil.which("media-control"))

    def now(self):
        try:
            out = subprocess.run(["media-control", "get"], capture_output=True, text=True, timeout=5).stdout
            return parse({}, json.dumps({"type": "data", "diff": False, "payload": json.loads(out or "{}") or {}}))
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def watch(self, interval=2.0):
        proc = subprocess.Popen(["media-control", "stream"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        state = {}
        try:
            for line in proc.stdout:
                try:
                    yield parse(state, line)
                except ValueError:
                    continue
        finally:
            proc.kill()
