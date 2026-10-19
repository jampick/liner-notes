#!/usr/bin/env python3
"""A bridge on a fake cliamp, for headless companion checks without touching real playback.

    python3 tools/fake_bridge.py <slug> <port>     plays the journey's third song, forever
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from liner_notes import bridge  # noqa: E402
from liner_notes.adapters.cliamp import CliampAdapter  # noqa: E402
from liner_notes.bank import Bank  # noqa: E402
from tests.fake_cliamp import FakeCliamp  # noqa: E402

slug, port = sys.argv[1], int(sys.argv[2])
sock = Path(tempfile.mkdtemp()) / "cliamp.sock"
os.environ["LINER_NOTES_CLIAMP_SOCKET"] = str(sock)
bank = Bank()
j = bank.journeys[slug]
tracks = []
for sid in j["songs"]:
    s = bank.songs[sid]
    yt = (j.get("pins") or {}).get(sid, {}).get("youtube") or (s["aliases"].get("youtube") or [""])[0]
    tracks.append({"title": s["title"], "artist": s["artist"], "path": f"https://www.youtube.com/watch?v={yt}", "duration_secs": 240})
fake = FakeCliamp(sock, tracks, playing=2)
srv, _ = bridge.make_server(port, adapter=CliampAdapter())
threading.Thread(target=srv.serve_forever, daemon=True).start()
print(f"fake bridge on http://127.0.0.1:{port}", flush=True)
while True:
    time.sleep(3600)
