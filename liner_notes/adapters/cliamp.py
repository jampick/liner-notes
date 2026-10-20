"""cliamp over its V2 IPC socket: the first-class adapter, with events, control and the extras."""
import os
import time

from .. import cliamp
from ..ids import youtube_id
from . import Adapter, NowPlaying


def from_snapshot(s):
    if not s or not s.get("track"):
        return None
    t = s["track"]
    path = t.get("path") or ""
    ids = {"path": path}
    if youtube_id(path):
        ids["youtube"] = youtube_id(path)
    for k, v in (t.get("provider_meta") or {}).items():
        ids[k] = v
    if s.get("playlist"):
        ids["cliamp_playlist"] = s["playlist"]
    meta = {k: s[k] for k in ("playlist_revision", "index", "total", "shuffle") if k in s}
    return NowPlaying(title=t.get("title") or "", artist=t.get("artist") or "", album=t.get("album") or "",
                      duration=float(s.get("duration") or t.get("duration_secs") or 0), position=float(s.get("position") or 0),
                      position_at=time.time(), state=s.get("state") or "stopped", source="cliamp", ids=ids, meta=meta)


class CliampAdapter(Adapter):
    """Reads go over one kept-open IPC connection; nothing here spawns `cliamp status`."""

    name = "cliamp"
    capabilities = frozenset({"events", "control", "jump", "lyrics", "history", "bands"})

    def __init__(self):
        self.client = cliamp.Client()

    def available(self):
        return os.path.exists(cliamp.socket_path())

    def snapshot(self):
        return self.client.state()

    def now(self):
        return from_snapshot(self.client.state())

    def watch(self, interval=2.0, timeout=None):
        """Yield now() and then one NowPlaying per event. With a timeout, a quiet stream raises
        OSError after that many seconds, so a caller can look around and come back."""
        yield self.now()
        for ev in cliamp.subscribe(timeout=timeout):
            d = ev.get("data")
            if ev.get("event") in ("runtime.state", "runtime.playback", "runtime.playlist") and isinstance(d, dict) and "track" in d:
                yield from_snapshot(d)

    def command(self, op):
        job = cliamp.call(op, timeout=5)
        return bool(job) and job.get("state") == "succeeded"

    def queue(self):
        return cliamp.queue()

    def jump(self, index):
        job = cliamp.call("queue.play", {"index": index}, timeout=5)
        return bool(job) and job.get("state") == "succeeded"

    def lyrics(self):
        return cliamp.result("lyrics", timeout=20)

    def history(self, limit=200):
        return (cliamp.result("history", {"limit": limit}) or {}).get("history") or []
