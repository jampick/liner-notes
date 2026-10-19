import http.client
import json
import threading
import time

import pytest

from liner_notes import bridge
from liner_notes.adapters.cliamp import CliampAdapter
from liner_notes.bank import write_json

from .fake_cliamp import FakeCliamp

TRACKS = [
    {"title": "Protection (Remastered 2006)", "artist": "Massive Attack - Topic", "path": "https://www.youtube.com/watch?v=0Rw-UsRPNqc", "duration_secs": 470},
    {"title": "Sailing", "artist": "Christopher Cross", "path": "spotify:track:abc", "duration_secs": 255},
    {"title": "Unknown Song", "artist": "Nobody", "path": "/music/x.flac"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    write_json(root / "songs/massive-attack--protection.json", {
        "id": "massive-attack--protection", "title": "Protection", "artist": "Massive Attack", "year": 1994, "album": "Protection",
        "aliases": {"youtube": ["0Rw-UsRPNqc"]}, "review": {"state": "draft", "by": None, "date": None},
        "notes": {"made": "Tracey Thorn sang it.", "context": "", "listen_for": [{"where": "the start", "note": "The beat."}], "sources": ["https://example.org"]}})
    write_json(root / "songs/christopher-cross--sailing.json", {"id": "christopher-cross--sailing", "title": "Sailing", "artist": "Christopher Cross", "year": 1979, "aliases": {}})
    write_json(root / "journeys/demo/journey.json", {"slug": "demo", "title": "Demo", "songs": ["massive-attack--protection", "christopher-cross--sailing"],
                                                     "eras": [{"n": 1, "name": "Bristol", "years": "1991–95", "from": 1991, "to": 1995}]})
    sock = tmp_path / "c.sock"
    monkeypatch.setenv("LINER_NOTES_CLIAMP_SOCKET", str(sock))
    monkeypatch.setenv("LINER_NOTES_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LINER_NOTES_DIST", str(tmp_path / "dist"))
    fake = FakeCliamp(sock, [dict(t) for t in TRACKS])
    yield fake, root
    fake.close()


def req(port, method, path, body=None, host=None, origin=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}"}
    if origin:
        h["Origin"] = origin
    if cookie:
        h["Cookie"] = cookie
    data = None
    if body is not None:
        data = json.dumps(body)
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.status, r.read(), dict(r.getheaders())
    c.close()
    return out


def j(r):
    return json.loads(r[1])


@pytest.fixture
def server(env, monkeypatch):
    fake, root = env
    lib = bridge.Library([root])
    now = bridge.Now(lib, CliampAdapter())

    def run(lan=False):
        token = "tok-123" if lan else None
        srv = bridge.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
        port = srv.server_address[1]
        srv.RequestHandlerClass = bridge.make_handler(lib, now, port, lan, token)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, port, token

    yield fake, run


def test_now_matches_messy_title(server):
    fake, run = server
    srv, port, _ = run()
    r = j(req(port, "GET", "/now"))
    assert r["ok"] and r["song"] == "massive-attack--protection" and r["journey"] == "demo" and r["yt"] == "0Rw-UsRPNqc"
    fake.index = 1
    r = j(req(port, "GET", "/now"))
    assert r["song"] == "christopher-cross--sailing" and r["yt"] is None
    fake.index = 2
    assert j(req(port, "GET", "/now"))["song"] is None
    srv.shutdown()


def test_host_and_origin_checks(server):
    fake, run = server
    srv, port, _ = run()
    assert req(port, "GET", "/now", host="evil.example:47391")[0] == 403
    assert req(port, "GET", "/now", origin="https://evil.example")[0] == 403
    assert req(port, "GET", "/now", origin="null")[0] == 200
    # Changes need an allowed Origin, not just no Origin.
    assert req(port, "POST", "/cmd", {"op": "toggle"})[0] == 403
    assert req(port, "POST", "/cmd", {"op": "toggle"}, origin="https://evil.example")[0] == 403
    assert j(req(port, "POST", "/cmd", {"op": "toggle"}, origin=f"http://127.0.0.1:{port}"))["ok"]
    assert ("toggle", {}) in fake.ops
    srv.shutdown()


def test_play_by_song_id(server):
    fake, run = server
    srv, port, _ = run()
    r = j(req(port, "POST", "/play", {"song": "christopher-cross--sailing"}, origin=f"http://127.0.0.1:{port}"))
    assert r == {"ok": True, "index": 1}
    assert ("queue.play", {"index": 1}) in fake.ops
    r = j(req(port, "POST", "/play", {"song": "nobody--nothing"}, origin=f"http://127.0.0.1:{port}"))
    assert r["error"] == "not in the loaded playlist"
    srv.shutdown()


def test_lan_needs_token(server):
    fake, run = server
    srv, port, token = run(lan=True)
    lan_host = f"192.168.1.20:{port}"
    assert req(port, "GET", "/now")[0] == 200  # this machine needs no token
    assert req(port, "GET", "/now", host=lan_host)[0] == 401
    assert req(port, "GET", "/now?t=wrong", host=lan_host)[0] == 401
    st, _, headers = req(port, "GET", f"/now?t={token}", host=lan_host)
    assert st == 200 and "ln_token=" in headers.get("Set-Cookie", "")
    assert req(port, "GET", "/now", host=lan_host, cookie=f"ln_token={token}")[0] == 200
    # A paired phone can drive playback from the bridge's own origin, and nothing else can.
    assert req(port, "POST", "/cmd", {"op": "next"}, host=lan_host, cookie=f"ln_token={token}", origin=f"http://{lan_host}")[0] == 200
    assert req(port, "POST", "/cmd", {"op": "next"}, host=lan_host, cookie=f"ln_token={token}", origin="https://evil.example")[0] == 403
    srv.shutdown()


def test_events_stream_on_track_change(server):
    fake, run = server
    srv, port, _ = run()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/events", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    first = r.fp.readline()
    assert first.startswith(b"data:")
    r.fp.readline()
    v0 = json.loads(first[5:])["version"]
    time.sleep(0.3)
    fake.play(1)
    line = r.fp.readline()
    while line in (b"\n", b": ping\n"):
        line = r.fp.readline()
    assert json.loads(line[5:])["version"] > v0
    c.close()
    srv.shutdown()


def test_lyrics_and_history(server):
    fake, run = server
    srv, port, _ = run()
    fake.history = [{"track": TRACKS[1], "played_at": "2026-10-18T20:00:00Z"}]
    h = j(req(port, "GET", "/history"))
    assert h["plays"][0]["song"] == "christopher-cross--sailing"
    ly = j(req(port, "GET", "/lyrics"))
    assert ly["ok"] and ly["song"] == "massive-attack--protection" and ly["lines"][0]["text"] == "hello"
    assert j(req(port, "GET", "/journey/demo.json"))["title"] == "Demo"
    assert j(req(port, "GET", "/song/christopher-cross--sailing.json"))["year"] == 1979
    assert req(port, "GET", "/song/../../etc.json")[0] == 404
    srv.shutdown()


def test_song_has_the_playing_notes(server):
    fake, run = server
    srv, port, _ = run()
    r = j(req(port, "GET", "/song"))
    assert r["ok"] and r["song"] == "massive-attack--protection" and r["title"] == "Protection" and r["year"] == 1994
    assert r["journey"] == {"slug": "demo", "title": "Demo"} and r["era"]["name"] == "Bristol"
    assert r["draft"] and r["review"]["state"] == "draft"
    # Empty fields and the source list stay out; the widget links to the page for those.
    assert r["notes"] == {"made": "Tracey Thorn sang it.", "listen_for": [{"where": "the start", "note": "The beat."}]}
    fake.index = 1  # in the bank, no notes yet, outside every era
    r = j(req(port, "GET", "/song"))
    assert r["song"] == "christopher-cross--sailing" and r["notes"] == {} and r["era"] is None and r["draft"]
    fake.index = 2  # playing, but not in the bank
    r = j(req(port, "GET", "/song"))
    assert r["ok"] and r["song"] is None and r["title"] == "Unknown Song" and r["notes"] is None and not r["draft"]
    assert req(port, "GET", "/song", host="evil.example:47391")[0] == 403
    srv.shutdown()


def test_lyrics_only_for_the_song_asked_about(server):
    fake, run = server
    srv, port, _ = run()
    assert j(req(port, "GET", "/lyrics?yt=0Rw-UsRPNqc"))["ok"]
    assert j(req(port, "GET", "/lyrics?song=massive-attack--protection"))["ok"]
    r = j(req(port, "GET", "/lyrics?yt=AAAAAAAAAAA&song=christopher-cross--sailing"))
    assert not r["ok"] and r["error"] == "not playing"
    srv.shutdown()


def test_adapter_without_extras(env, tmp_path):
    from liner_notes.adapters import Adapter, NowPlaying

    class Plain(Adapter):
        name = "plain"
        capabilities = frozenset({"control"})

        def available(self):
            return True

        def now(self):
            return NowPlaying(title="Sailing", artist="Christopher Cross", duration=255, position=10, position_at=0, state="paused", source="plain")

        def watch(self, interval=2.0):
            yield self.now()

        def command(self, op):
            self.op = op
            return True

    fake, root = env
    lib, ad = bridge.Library([root]), Plain()
    srv = bridge.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = bridge.make_handler(lib, bridge.Now(lib, ad), port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    n = j(req(port, "GET", "/now"))
    assert n["song"] == "christopher-cross--sailing" and n["source"] == "plain" and n["position"] == 10
    assert not j(req(port, "GET", "/lyrics"))["ok"]
    assert j(req(port, "POST", "/play", {"song": "christopher-cross--sailing"}, origin=f"http://127.0.0.1:{port}"))["error"].endswith("can't jump to a song")
    assert j(req(port, "POST", "/cmd", {"op": "next"}, origin=f"http://127.0.0.1:{port}"))["ok"] and ad.op == "next"
    srv.shutdown()
