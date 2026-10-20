"""Windows now-playing through the System Media Transport Controls (SMTC).

Status: documented stub. Python's stdlib can't reach WinRT, so the plan is a small helper
(PowerShell, or a single-file C# exe) that subscribes to
GlobalSystemMediaTransportControlsSessionManager and prints one JSON object per change. Point
LINER_NOTES_SMTC_HELPER at it. Spotify, the Windows media player, browsers and most players
publish to SMTC.

Contract for the helper's lines: {"title", "artist", "album", "durationSec", "positionSec",
"lastUpdated" (ISO 8601, when positionSec was true), "status" ("Playing" | "Paused" | "Stopped"),
"appId"}. Unknown keys are ignored.
"""

import datetime as dt
import json
import os
import subprocess
import time

from . import Adapter, NowPlaying


def parse(line):
    d = json.loads(line)
    if not d.get("title"):
        return None
    try:
        at = dt.datetime.fromisoformat(str(d.get("lastUpdated")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        at = time.time()
    return NowPlaying(
        title=d["title"],
        artist=d.get("artist") or "",
        album=d.get("album") or "",
        duration=float(d.get("durationSec") or 0),
        position=float(d.get("positionSec") or 0),
        position_at=at,
        state={"Playing": "playing", "Paused": "paused"}.get(d.get("status"), "stopped"),
        source="smtc",
        ids={"app": d["appId"]} if d.get("appId") else {},
    )


class SmtcAdapter(Adapter):
    name = "smtc"
    capabilities = frozenset({"events"})

    def __init__(self, helper=None):
        self.helper = helper or os.environ.get("LINER_NOTES_SMTC_HELPER")
        self.last = None

    def available(self):
        return os.name == "nt" and bool(self.helper) and os.path.exists(self.helper)

    def now(self):
        return self.last

    def watch(self, interval=2.0):
        proc = subprocess.Popen([self.helper], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            for line in proc.stdout:
                try:
                    self.last = parse(line)
                except ValueError:
                    continue
                yield self.last
        finally:
            proc.kill()
