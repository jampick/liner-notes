"""Shared pieces for the bridge tests: a small bank, a fake cliamp, a fake LRCLIB, and a bridge on a free port.

Nothing here reaches the network or the real cliamp: the cliamp socket, the cache, the dist folder,
the state folder and LRCLIB all point into the test's tmp_path.
"""
import http.client
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from liner_notes import bridge
from liner_notes.adapters.cliamp import CliampAdapter
from liner_notes.bank import write_json
from liner_notes.bridge.bands import Bands
from liner_notes.bridge.lyrics import Lyrics
from liner_notes.bridge.server import Server

from .fake_cliamp import FakeCliamp

TRACKS = [
    {"title": "Protection (Remastered 2006)", "artist": "Massive Attack - Topic", "path": "https://www.youtube.com/watch?v=0Rw-UsRPNqc", "duration_secs": 470},
    {"title": "Sailing", "artist": "Christopher Cross", "path": "spotify:track:abc", "duration_secs": 255},
    {"title": "Unknown Song", "artist": "Nobody", "path": "/music/x.flac"},
    {"title": "Teardrop", "artist": "Massive Attack", "path": "/home/u/.cache/journeys/audio/u7K72X4eo_s.webm", "duration_secs": 330},
]
VISSTREAM = Path(__file__).with_name("fake_visstream.py")


def visstream(mode):
    return [sys.executable, str(VISSTREAM), mode]


class FakeLrclib:
    """GET /api/search answers with `rows`; every request is counted."""

    def __init__(self):
        self.rows, self.hits = [], []
        kit = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                kit.hits.append(self.path)
                body = json.dumps(kit.rows).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write_json(root / "songs/massive-attack--protection.json", {
        "id": "massive-attack--protection", "title": "Protection", "artist": "Massive Attack", "year": 1994, "album": "Protection",
        "aliases": {"youtube": ["0Rw-UsRPNqc"]}, "review": {"state": "draft", "by": None, "date": None},
        "notes": {"made": "Tracey Thorn sang it.", "context": "", "listen_for": [{"where": "the start", "note": "The beat."}], "sources": ["https://example.org"]}})
    write_json(root / "songs/christopher-cross--sailing.json", {"id": "christopher-cross--sailing", "title": "Sailing", "artist": "Christopher Cross", "year": 1979, "aliases": {}})
    write_json(root / "songs/massive-attack--teardrop.json", {"id": "massive-attack--teardrop", "title": "Teardrop", "artist": "Massive Attack", "year": 1998,
                                                               "aliases": {"youtube": ["u7K72X4eo_s"]}})
    write_json(root / "journeys/demo/journey.json", {"slug": "demo", "title": "Demo", "songs": ["massive-attack--protection", "christopher-cross--sailing"],
                                                     "eras": [{"n": 1, "name": "Bristol", "years": "1991–95", "from": 1991, "to": 1995}]})
    write_json(root / "journeys/trip-hop/journey.json", {"slug": "trip-hop", "title": "Trip-Hop", "songs": ["massive-attack--teardrop", "massive-attack--protection"], "eras": []})
    sock = tmp_path / "c.sock"
    monkeypatch.setenv("LINER_NOTES_CLIAMP_SOCKET", str(sock))
    monkeypatch.setenv("LINER_NOTES_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LINER_NOTES_DIST", str(tmp_path / "dist"))
    monkeypatch.setenv("LINER_NOTES_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr("liner_notes.bridge.lyrics.PILOT", tmp_path / "pilot-lyrics")
    monkeypatch.setattr("liner_notes.bridge.media.PILOT_VIDEO", tmp_path / "pilot-video")
    monkeypatch.setattr("liner_notes.bridge.route.PILOT_TABLE", tmp_path / "pilot-av.json")
    monkeypatch.setattr("liner_notes.bridge.route.route", lambda: "")
    lrc = FakeLrclib()
    monkeypatch.setenv("LINER_NOTES_LRCLIB_URL", lrc.url)
    yield {"tmp": tmp_path, "root": root, "sock": sock, "lrclib": lrc}
    lrc.close()


class Running:
    def __init__(self, srv, now, handler):
        self.srv, self.now, self.handler = srv, now, handler
        self.port = srv.server_address[1]
        self.token = handler.services["token"]
        self.svc = handler.services

    def req(self, method, path, body=None, host=None, origin=None, cookie=None, token=None, headers=None, raw=None, timeout=5):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if origin:
            h["Origin"] = origin
        if cookie:
            h["Cookie"] = cookie
        if token:
            h["X-Liner-Token"] = token
        data = raw
        if body is not None:
            data = json.dumps(body)
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        out = r.status, r.read(), dict(r.getheaders())
        c.close()
        return out

    def get(self, path, **kw):
        return json.loads(self.req("GET", path, **kw)[1])

    def post(self, path, body, **kw):
        kw.setdefault("token", self.token)
        kw.setdefault("origin", f"http://127.0.0.1:{self.port}")
        return self.req("POST", path, body, **kw)

    def stop(self):
        self.now.close()
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def start(env):
    """start(fake=..., lan=False, adapter=None, bands_mode="emit", lyrics_ttl=180) -> Running."""
    running, fakes = [], []

    def run(fake=True, lan=False, adapter=None, bands_mode="emit", lyrics_ttl=180.0, clock=False, token=None, **fk):
        if fake:
            f = FakeCliamp(env["sock"], [dict(t) for t in TRACKS], clock=clock, **fk)
            fakes.append(f)
        lib = bridge.Library([env["root"]])
        now = bridge.Now(lib, adapter or CliampAdapter(), watch_every=0.2)
        srv = Server(("127.0.0.1", 0), lambda *a: None)
        port = srv.server_address[1]
        lyr = Lyrics(lambda: now.adapter, now.fresh, ttl=lyrics_ttl)
        handler = bridge.make_handler(lib, now, port, lan, token or ("tok-123" if lan else None), lyrics=lyr, bands=Bands(cmd=visstream(bands_mode)))
        srv.RequestHandlerClass = handler
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        r = Running(srv, now, handler)
        r.fake = fakes[-1] if fake else None
        running.append(r)
        return r

    yield run
    for r in running:
        r.stop()
    for f in fakes:
        f.close()
