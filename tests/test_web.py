"""The page templates and the fake bridge the headless checks run against (tools/scenarios.js does the rest)."""
import http.client
import importlib.util
import json
import re
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "liner_notes/web"
TEMPLATES = [WEB / "template.html", WEB / "remix_template.html"]


def load_fake():
    spec = importlib.util.spec_from_file_location("fake_bridge", ROOT / "tools/fake_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fake = load_fake()


def script(path):
    return "\n".join(re.findall(r"<script>(.*?)</script>", path.read_text(), re.S))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_token_placeholder_in_head(path):
    head = path.read_text().split("</head>")[0]
    assert '<meta name="liner-token" content="">' in head
    assert "X-Liner-Token" in script(path)


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_nothing_from_youtube_or_google(path):
    html = path.read_text()
    assert "iframe_api" not in html and "YT.Player" not in html
    assert "fonts.googleapis" not in html and "fonts.gstatic" not in html
    assert not re.search(r"<link[^>]+stylesheet[^>]+https?:", html)


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_no_innerhtml_with_data(path):
    # The only innerHTML left is the play/pause icon: a constant SVG.
    for m in re.finditer(r"innerHTML\s*=\s*([^;]+);", script(path)):
        assert re.fullmatch(r"\s*(svgPlay|p \? '<path[^']*' : '<path[^']*')\s*", m.group(1)), m.group(0)


def test_journey_template_keeps_its_build_placeholders():
    html = (WEB / "template.html").read_text()
    for p in ("__TITLE__", "__DESCRIPTION__", "__FONTS__", "/*__DATA__*/null", "<title>"):
        assert p in html


def test_remix_template_placeholder():
    assert "/*DATA*/null" in (WEB / "remix_template.html").read_text()


def test_lyrics_off_is_not_remembered():
    # The pilot dropped the remembered "lyrics off" (it hid lyrics with no sign why); the saved timing keys stay.
    js = script(WEB / "template.html")
    assert "'journey-lyrics'" not in js
    assert "journey-lyric-offset-" in js and "journey-lyric-lead" in js


def test_reduced_motion_in_both_templates():
    for p in TEMPLATES:
        assert "prefers-reduced-motion: reduce" in p.read_text()


def test_note_and_space_in_remix_byline():
    # "\266a " eats the space as part of the escape; the page must show "♪ Party Pupils".
    css = (WEB / "remix_template.html").read_text()
    assert '"\\266a\\00a0"' in css


@pytest.mark.parametrize("header,size,want", [
    (None, 100, None), ("bytes=0-9", 100, (0, 9)), ("bytes=90-", 100, (90, 99)), ("bytes=-10", 100, (90, 99)),
    ("bytes=0-500", 100, (0, 99)), ("bytes=-", 100, "bad"), ("bytes=100-", 100, "bad"), ("bytes=5-2", 100, "bad"), ("items=0-1", 100, "bad"),
])
def test_range_parsing(header, size, want):
    assert fake.parse_range(header, size) == want


def test_now_counts_on_only_while_playing():
    t = 1000.0
    st = {"now": {"ok": True, "state": "playing", "pos0": 10, "t0": t, "duration": 240, "epoch": 3}}
    assert fake.now_payload(st, t + 5)["position"] == 15
    st["now"]["state"] = "paused"
    out = fake.now_payload(st, t + 5)
    assert out["position"] == 10 and out["moving"] is False
    st["now"].update(state="playing", moving=False)
    assert fake.now_payload(st, t + 5)["position"] == 10
    assert fake.now_payload({}, t) == {"ok": False, "reason": "stopped", "version": "fake", "source": "cliamp"}


@pytest.fixture
def served(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "video").mkdir()
    (tmp_path / "dist/demo.html").write_text('<head><meta name="liner-token" content=""></head>')
    (tmp_path / "video/abcdefghijk.mp4").write_bytes(bytes(range(256)) * 4)
    (tmp_path / "state.json").write_text(json.dumps({"now": {"ok": True, "state": "playing", "title": "T", "yt": "abcdefghijk", "pos0": 1, "t0": time.time()},
                                                     "lyrics": {"abcdefghijk": [{"t": 1, "text": "a line"}]}}))
    srv, token = fake.make_server(tmp_path, 0, "tok")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield tmp_path, port
    srv.shutdown()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    return r, r.read()


def test_fake_bridge_serves_page_with_token_and_csp(served):
    _, port = served
    r, body = call(port, "GET", "/p/demo.html")
    assert r.status == 200 and b'content="tok"' in body
    assert "font-src 'self' data:" in r.getheader("Content-Security-Policy")
    r, body = call(port, "HEAD", "/p/demo.html")
    assert r.status == 200 and body == b""
    assert call(port, "HEAD", "/p/nope.html")[0].status == 404


def test_fake_bridge_video_ranges(served):
    _, port = served
    r, body = call(port, "GET", "/v/abcdefghijk.mp4", headers={"Range": "bytes=10-19"})
    assert r.status == 206 and body == bytes(range(10, 20)) and r.getheader("Content-Range") == "bytes 10-19/1024"
    assert call(port, "GET", "/v/abcdefghijk.mp4", headers={"Range": "bytes=-"})[0].status == 416
    assert json.loads(call(port, "GET", "/v/")[1])["ids"] == ["abcdefghijk"]


def test_fake_bridge_lyrics_only_for_the_song_asked(served):
    _, port = served
    assert json.loads(call(port, "GET", "/lyrics?yt=abcdefghijk")[1])["lines"][0]["text"] == "a line"
    assert json.loads(call(port, "GET", "/lyrics?yt=zzzzzzzzzzz")[1]) == {"ok": False}


def test_fake_bridge_post_needs_token_and_logs(served):
    root, port = served
    r, body = call(port, "POST", "/cmd", {"op": "next"})
    assert r.status == 403 and json.loads(body) == {"ok": False, "reason": "token"}
    assert call(port, "POST", "/cmd", {"op": "next"}, {"X-Liner-Token": "tok", "Origin": "null"})[0].status == 403
    assert call(port, "POST", "/cmd", {"op": "next"}, {"X-Liner-Token": "tok"})[0].status == 200
    assert call(port, "POST", "/cmd", [], {"X-Liner-Token": "tok"})[0].status == 400
    posts = [json.loads(x) for x in (root / "posts.log").read_text().splitlines()]
    assert [p["token_ok"] for p in posts] == [False, True]
