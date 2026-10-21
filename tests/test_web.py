"""The page templates and the fake bridge the headless checks run against (tools/scenarios.js does the rest)."""

import http.client
import importlib.util
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "liner_notes/web"
TEMPLATES = [WEB / "template.html", WEB / "remix.html"]


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
    assert "/*DATA*/null" in (WEB / "remix.html").read_text()


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
    css = (WEB / "remix.html").read_text()
    assert '"\\266a\\00a0"' in css


def test_only_web_addresses_become_links():
    js = script(WEB / "template.html")
    assert "href: u," not in js and "href: s.youtube" not in js and "href: s.apple" not in js
    assert "const web = u =>" in js


def test_fake_bridge_uses_the_bridge_contract():
    # The fake serves what the page sees in tests; its routes, range rules, CSP and token refusal are the bridge's own.
    from liner_notes.bridge import contract

    assert fake.C is contract


def test_page_scripts_parse():
    # A stray bracket in a template only shows as a page error at run time; node catches it here.
    node = shutil.which("node")
    if not node:
        pytest.skip("no node")
    for path in TEMPLATES:
        for k, js in enumerate(re.findall(r"<script>(.*?)</script>", path.read_text(), re.S)):
            js = js.replace("/*DATA*/null", "[]").replace("/*__DATA__*/null", "null")
            r = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
            assert r.returncode == 0, f"{path.name} script {k}: {r.stderr[-400:]}"


@pytest.fixture
def served(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "video").mkdir()
    (tmp_path / "dist/demo.html").write_text('<head><meta name="liner-token" content=""></head>')
    (tmp_path / "video/abcdefghijk.mp4").write_bytes(bytes(range(256)) * 4)
    now = {"ok": True, "state": "playing", "title": "T", "yt": "abcdefghijk", "song": "a-song", "pos0": 1, "t0": time.time()}
    (tmp_path / "state.json").write_text(json.dumps({"now": now, "lyrics": {"abcdefghijk": [{"t": 1, "text": "a line"}]}}))
    srv, _ = fake.make_server(tmp_path, 0, "tok")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield tmp_path, srv.server_address[1]
    srv.shutdown()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    return r, r.read()


def test_fake_bridge_answers_like_the_bridge(served):
    root, port = served
    r, body = call(port, "GET", "/p/demo.html")
    assert r.status == 200 and b'content="tok"' in body and r.getheader("ETag")
    assert call(port, "GET", "/p/demo.html", headers={"If-None-Match": r.getheader("ETag")})[0].status == 304
    lines = json.loads(call(port, "GET", "/lyrics?yt=abcdefghijk")[1])["lines"]
    assert lines == [{"t": 1, "start": 1, "text": "a line"}]
    other = json.loads(call(port, "GET", "/lyrics?yt=zzzzzzzzzzz")[1])
    assert other["ok"] is False and other["error"] == "not playing" and other["lines"] == []
    assert call(port, "GET", "/v/abcdefghijk.mp4", headers={"Range": "bytes=5-3"})[0].status == 200
    r, body = call(port, "POST", "/cmd", {"op": "next"})
    assert r.status == 403 and json.loads(body) == fake.C.token_refusal()
    assert call(port, "POST", "/cmd", {"op": "dance"}, {"X-Liner-Token": "tok"})[0].status == 400
    assert call(port, "POST", "/cmd", {"op": "next"}, {"X-Liner-Token": "tok"})[0].status == 200
