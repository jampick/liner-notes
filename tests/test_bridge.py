import http.client
import json
import threading
import time

from liner_notes import __version__, bridge
from liner_notes.adapters import Adapter, NowPlaying

from .bridge_kit import TRACKS

NOW_KEYS = {"ok", "version", "source", "state", "title", "artist", "album", "yt", "song", "journey", "playlist",
            "position", "duration", "epoch", "moving", "latency", "can"}


def test_now_shape_and_matching(start):
    b = start()
    r = b.get("/now")
    assert set(r) == NOW_KEYS
    assert r["ok"] and r["version"] == __version__ and r["source"] == "cliamp"
    assert r["song"] == "massive-attack--protection" and r["journey"] == "demo" and r["yt"] == "0Rw-UsRPNqc"
    assert isinstance(r["epoch"], int) and isinstance(r["moving"], bool) and r["latency"] == 0
    assert "jump" in r["can"] and "lyrics" in r["can"]
    b.fake.play(1)
    time.sleep(0.3)
    r = b.get("/now")
    assert r["song"] == "christopher-cross--sailing" and r["yt"] is None
    b.fake.play(2)
    time.sleep(0.3)
    assert b.get("/now")["song"] is None


def test_now_without_player(start):
    b = start(fake=False)
    r = b.get("/now")
    assert r == {"ok": False, "reason": "no-player", "version": __version__, "source": "cliamp", "can": r["can"]}


def test_now_stopped(start):
    b = start()
    b.fake.set_state("stopped")
    time.sleep(0.3)
    r = b.get("/now")
    assert not r["ok"] and r["reason"] == "stopped"


def test_play_by_song_id(start):
    b = start()
    st, body, _ = b.post("/play", {"song": "christopher-cross--sailing"})
    assert st == 200 and json.loads(body) == {"ok": True, "index": 1}
    assert ("queue.play", {"index": 1}) in b.fake.ops
    assert json.loads(b.post("/play", {"song": "nobody--nothing"})[1])["error"] == "not in the loaded playlist"
    assert json.loads(b.post("/play", {"yt": "u7K72X4eo_s"})[1]) == {"ok": True, "index": 3}  # an offline path


def test_lan_needs_pairing(start):
    b = start(lan=True)
    assert b.pair == "tok-123" and b.token != b.pair  # two different secrets
    lan_host = f"192.168.1.20:{b.port}"
    assert b.req("GET", "/now")[0] == 200  # this machine needs no pairing
    assert b.req("GET", "/now", host=lan_host)[0] == 401
    assert b.req("GET", "/now?t=wrong", host=lan_host)[0] == 401
    assert b.req("GET", "/now?t=%C3%A9t%C3%A9", host=lan_host)[0] == 401  # non-ASCII used to raise TypeError
    assert b.req("GET", "/now", host=lan_host, headers={"X-Liner-Pair": "été"})[0] == 401
    assert b.req("GET", "/now", host=lan_host, token=b.token)[0] == 401  # the page token doesn't pair
    st, _, headers = b.req("GET", f"/now?t={b.pair}", host=lan_host)
    assert st == 200 and f"ln_pair={b.pair}" in headers.get("Set-Cookie", "")
    assert b.req("GET", "/now", host=lan_host, cookie=f"ln_pair={b.pair}")[0] == 200
    # A paired phone's page drives playback from the bridge's own origin with the page token; nothing else can.
    assert b.post("/cmd", {"op": "next"}, host=lan_host, cookie=f"ln_pair={b.pair}", origin=f"http://{lan_host}")[0] == 200
    assert b.post("/cmd", {"op": "next"}, host=lan_host, cookie=f"ln_pair={b.pair}", origin="https://evil.example")[0] == 403
    assert b.post("/cmd", {"op": "next"}, host=lan_host, origin=f"http://{lan_host}")[0] == 401  # token but not paired


def test_events_stream_on_track_change(start):
    b = start()
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=10)
    c.request("GET", "/events", headers={"Host": f"127.0.0.1:{b.port}"})
    r = c.getresponse()
    first = r.fp.readline()
    assert first.startswith(b"data:")
    r.fp.readline()
    v0 = json.loads(first[5:])["version"]
    time.sleep(0.3)
    b.fake.play(1)
    line = r.fp.readline()
    while line in (b"\n", b": ping\n"):
        line = r.fp.readline()
    assert json.loads(line[5:])["version"] > v0
    c.close()


def test_history_and_bank_routes(start):
    b = start()
    b.fake.history = [{"track": TRACKS[1], "played_at": "2026-10-18T20:00:00Z"}, {"track": TRACKS[3], "played_at": "2026-10-18T19:55:00Z"}]
    h = b.get("/history")
    assert [p["song"] for p in h["plays"]] == ["christopher-cross--sailing", "massive-attack--teardrop"]
    assert h["plays"][1]["yt"] == "u7K72X4eo_s"
    assert b.get("/journey/demo.json")["title"] == "Demo"
    assert b.get("/song/christopher-cross--sailing.json")["year"] == 1979
    assert b.req("GET", "/song/../../etc.json")[0] == 404


def test_song_has_the_playing_notes(start):
    b = start()
    r = b.get("/song")
    assert r["ok"] and r["song"] == "massive-attack--protection" and r["title"] == "Protection" and r["year"] == 1994
    assert r["journey"] == {"slug": "demo", "title": "Demo"} and r["era"]["name"] == "Bristol"
    assert r["draft"] and r["review"]["state"] == "draft"
    assert r["notes"] == {"made": "Tracey Thorn sang it.", "listen_for": [{"where": "the start", "note": "The beat."}]}
    b.fake.play(1)  # in the bank, no notes yet, outside every era
    time.sleep(0.3)
    r = b.get("/song")
    assert r["song"] == "christopher-cross--sailing" and r["notes"] == {} and r["era"] is None and r["draft"]
    b.fake.play(2)  # playing, but not in the bank
    time.sleep(0.3)
    r = b.get("/song")
    assert r["ok"] and r["song"] is None and r["title"] == "Unknown Song" and r["notes"] is None and not r["draft"]
    assert b.req("GET", "/song", host="evil.example:47391")[0] == 403


def test_adapter_without_extras(start):
    class Plain(Adapter):
        name = "plain"
        capabilities = frozenset({"control"})

        def available(self):
            return True

        def now(self):
            return NowPlaying(title="Sailing", artist="Christopher Cross", duration=255, position=10, position_at=0, state="paused", source="plain")

        def command(self, op):
            self.op = op
            return True

    ad = Plain()
    b = start(fake=False, adapter=ad)
    n = b.get("/now")
    assert n["song"] == "christopher-cross--sailing" and n["source"] == "plain" and n["position"] == 10 and not n["moving"]
    assert not b.get("/lyrics")["ok"]
    assert b.req("GET", "/bands")[0] == 404
    assert json.loads(b.post("/play", {"song": "christopher-cross--sailing"})[1])["error"].endswith("can't jump to a song")
    assert json.loads(b.post("/cmd", {"op": "next"})[1])["ok"] and ad.op == "next"


def test_player_errors_become_502(start):
    class Broken(Adapter):
        name = "broken"
        capabilities = frozenset({"control"})

        def available(self):
            return True

        def now(self):
            return None

        def command(self, op):
            raise TimeoutError("cliamp took too long")

    b = start(fake=False, adapter=Broken())
    assert b.post("/cmd", {"op": "next"})[0] == 502
    assert b.req("GET", "/now")[0] == 200  # the server is still fine


def test_make_server_keeps_its_signature(env):
    from liner_notes.adapters.cliamp import CliampAdapter

    srv, pair = bridge.make_server(0, adapter=CliampAdapter(), roots=[env["root"]])
    assert pair is None and srv.server_address[0] == "127.0.0.1"  # no pairing on loopback
    assert srv.RequestHandlerClass.services["token"]  # but pages get a token
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/now", headers={"Host": f"127.0.0.1:{port}"})
    assert json.loads(c.getresponse().read())["reason"] == "no-player"
    srv.shutdown()
    srv.server_close()
