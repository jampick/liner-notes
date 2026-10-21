"""The real bridge and tools/fake_bridge.py, on the same story, must answer the page the same way.

Both get one playing song (yt AAAAAAAAAAA, not in the bank), a page, stage data, a cover, an artist
picture and a 5000-byte video. Each of the requests below goes to both, and the status, the JSON keys
and the headers the page depends on must match. Values that differ by nature (position, version)
aren't compared.

The fake speaks the contract only once it imports it from liner_notes.bridge.contract; until then
this skips, with that reason (LINER_NOTES_CONTRACT_FORCE=1 runs it anyway, to see the drift).
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

FAKE = Path(__file__).resolve().parent.parent / "tools/fake_bridge.py"
HEADERS = ("Content-Security-Policy", "Access-Control-Allow-Origin", "Content-Range", "Cache-Control", "Content-Type")
YT = "AAAAAAAAAAA"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 30
PAGE = '<!doctype html><meta name="liner-token" content=""><title>x</title><script>window.x = 1;</script>'


def fake_speaks_contract():
    try:
        src = FAKE.read_text()
    except OSError:
        return False
    return "liner_notes.bridge.contract" in src or "from liner_notes.bridge import contract" in src


needs_fake = pytest.mark.skipif(
    not (fake_speaks_contract() or os.environ.get("LINER_NOTES_CONTRACT_FORCE")),
    reason="tools/fake_bridge.py doesn't import liner_notes.bridge.contract yet",
)


def cases(token):
    t = {"X-Liner-Token": token}
    return [
        ("GET", "/now", None, None),
        ("GET", f"/lyrics?yt={YT}", None, None),
        ("GET", "/lyrics?yt=BBBBBBBBBBB", None, None),
        ("GET", "/history", None, None),
        ("GET", "/theme", None, None),
        ("GET", "/v/", None, None),
        ("GET", "/p/x.html", None, None),
        ("HEAD", "/p/x.html", None, None),
        ("HEAD", "/p/nope.html", None, None),
        ("GET", "/p/x.stage.json", None, None),
        ("GET", "/m/x/covers/offsong-1.webp", None, None),
        ("GET", f"/m/live/covers/{YT}.webp", None, None),
        ("GET", "/m/x/artists/a.png", None, None),
        ("GET", f"/v/{YT}.mp4", None, {"Range": "bytes=0-9"}),
        ("GET", f"/v/{YT}.mp4", None, {"Range": "bytes=5-3"}),
        ("GET", f"/v/{YT}.mp4", None, {"Range": "bytes=-"}),
        ("GET", f"/v/{YT}.mp4", None, {"Range": "bytes=9000-"}),
        ("POST", "/cmd", '{"op":"next"}', None),
        ("POST", "/cmd", '{"op":"next"}', t),
        ("POST", "/cmd", '{"op":"bogus"}', t),
        ("POST", "/cmd", "[]", t),
        ("POST", "/cmd", '{"op":"next"}', {**t, "Origin": "null"}),
        ("GET", "/now", None, {"Origin": "null"}),
        ("GET", "/p/x.html", None, {"Origin": "null"}),
        ("GET", "/song", None, None),
    ]


def ask(port, method, path, body, headers):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        j = json.loads(data)
        shape = sorted(j) if isinstance(j, dict) else type(j).__name__
    except ValueError:
        shape = f"{len(data)} bytes"
    return r.status, shape, {k: v for k, v in r.getheaders() if k in HEADERS}


def free_port():
    for p in range(47403, 47410):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    pytest.skip("no free port in 47403-47409")


@needs_fake
def test_real_and_fake_bridges_agree(start, env):
    tmp = env["tmp"]
    real_cache, fake = tmp / "cache", tmp / "fake"
    for d in (
        real_cache / "covers",
        real_cache / "artists",
        real_cache / "video",
        real_cache / "stage",
        tmp / "dist",
        fake / "media/covers",
        fake / "media/artists",
        fake / "video",
        fake / "dist",
    ):
        d.mkdir(parents=True, exist_ok=True)
    for base in (tmp / "dist", fake / "dist"):
        (base / "x.html").write_text(PAGE)
    (real_cache / "stage/x.stage.json").write_text("{}")
    (fake / "dist/x.stage.json").write_text("{}")
    for d in (real_cache / "covers", fake / "media/covers"):
        (d / "offsong-1.webp").write_bytes(PNG)
        (d / f"{YT}.webp").write_bytes(PNG)
    for d in (real_cache / "artists", fake / "media/artists"):
        (d / "a.png").write_bytes(PNG)
    for d in (real_cache / "video", fake / "video"):
        (d / f"{YT}.mp4").write_bytes(b"\0" * 5000)

    b = start(fake=False)
    from .fake_cliamp import FakeCliamp

    fc = FakeCliamp(
        env["sock"], [{"title": "Song A", "artist": "X", "path": f"https://www.youtube.com/watch?v={YT}", "duration_secs": 200}]
    )
    fc.lyrics = {"ok": True, "lyrics": [{"start": 1.5, "text": "one"}, {"start": 4.0, "text": "two"}]}
    (fake / "state.json").write_text(
        json.dumps(
            {
                "now": {
                    "ok": True,
                    "state": "playing",
                    "title": "Song A",
                    "artist": "X",
                    "yt": YT,
                    "song": None,
                    "journey": None,
                    "playlist": None,
                    "duration": 200,
                    "pos0": 12,
                    "t0": time.time(),
                },
                "lyrics": {YT: [{"t": 1.5, "text": "one"}, {"t": 4.0, "text": "two"}]},
                "history": [],
            }
        )
    )
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(FAKE), "serve", str(fake), "--port", str(port), "--token", b.token],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        end = time.monotonic() + 10
        while time.monotonic() < end:
            try:
                ask(port, "GET", "/now", None, None)
                break
            except OSError:
                time.sleep(0.1)
        assert b.get("/now")["ok"]
        diffs = []
        for m, p, body, h in cases(b.token):
            real, fk = ask(b.port, m, p, body, h), ask(port, m, p, body, h)
            if real != fk:
                diffs.append(f"{m} {p} {h or ''}\n   real {real}\n   fake {fk}")
        assert not diffs, "the fake bridge has drifted from the real one:\n" + "\n".join(diffs)
    finally:
        proc.kill()
        proc.wait()
        fc.close()


def test_the_real_bridge_answers_with_the_contract(start):
    from liner_notes.bridge import contract, media, server

    b = start()
    st, body, _ = b.req("POST", "/cmd", {"op": "next"})
    assert st == 403 and json.loads(body) == contract.token_refusal()
    r = b.get("/lyrics?yt=BBBBBBBBBBB")
    assert r == contract.lyrics_not_playing("0Rw-UsRPNqc", "massive-attack--protection")
    assert set(b.get("/now")) == set(contract.NOW_KEYS)
    assert server.CSP == contract.CSP and media.parse_range is contract.parse_range and media.COVER is contract.COVER
    assert contract.inject_token(contract.TOKEN_META + b"x", "abc") == b'<meta name="liner-token" content="abc">x'
