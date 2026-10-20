import json
import socket
import time

import pytest

from liner_notes.bridge.server import CSP


def raw(port, data, wait=3.0):
    """Send bytes as they are and return what comes back (status line first), with how long it took."""
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    t0 = time.monotonic()
    s.sendall(data)
    out = b""
    try:
        while b"\r\n\r\n" not in out:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
    except TimeoutError:
        pass
    s.close()
    return out, time.monotonic() - t0


def test_host_rebinding_is_refused(start):
    b = start()
    assert b.req("GET", "/now", host="evil.example:47391")[0] == 403
    assert b.req("POST", "/cmd", {"op": "next"}, host="evil.example:47391", token=b.token)[0] == 403
    assert ("next", {}) not in b.fake.ops


def test_origins(start, env):
    b = start()
    (env["tmp"] / "dist").mkdir()
    (env["tmp"] / "dist/demo.html").write_text('<meta name="liner-token" content="">')
    assert b.req("GET", "/now", origin="https://evil.example")[0] == 403
    st, _, h = b.req("GET", "/now", origin="null")  # a page opened from disk can follow along
    assert st == 200 and h.get("Access-Control-Allow-Origin") == "null"
    assert b.req("GET", "/theme", origin="null")[2].get("Access-Control-Allow-Origin") == "null"
    # and nothing else: above all not a page, which carries the page token
    for path in (
        "/history",
        "/lyrics",
        "/v/",
        "/p/demo.html",
        "/journey/demo.json",
        "/song/christopher-cross--sailing.json",
        "/song",
        "/events",
        "/m/demo/culture/x.webp",
        "/d/demo/culture/x.webp",
        "/",
    ):
        h = b.req("HEAD" if path == "/events" else "GET", path, origin="null")[2]
        assert "Access-Control-Allow-Origin" not in h, path
    st, _, h = b.req("GET", "/history", origin=f"http://127.0.0.1:{b.port}")
    assert h.get("Access-Control-Allow-Origin") == f"http://127.0.0.1:{b.port}"


def test_posts_need_the_token_and_a_real_origin(start):
    b = start()
    own = f"http://127.0.0.1:{b.port}"
    assert b.req("POST", "/cmd", {"op": "toggle"}, origin="null", token=b.token)[0] == 403
    assert b.req("POST", "/cmd", {"op": "toggle"}, origin="https://evil.example", token=b.token)[0] == 403
    st, body, _ = b.req("POST", "/cmd", {"op": "toggle"}, origin=own)
    assert st == 403 and json.loads(body)["reason"] == "token"  # the page reloads itself on this
    st, body, _ = b.req("POST", "/cmd", {"op": "toggle"}, origin=own, token="wrong")
    assert st == 403 and json.loads(body)["reason"] == "token"
    assert b.req("POST", "/cmd", {"op": "toggle"}, origin=own, token="été")[0] == 403
    assert ("toggle", {}) not in b.fake.ops
    st, body, _ = b.req("POST", "/cmd", {"op": "toggle"}, origin=own, token=b.token)
    assert st == 200 and json.loads(body)["ok"]
    assert ("toggle", {}) in b.fake.ops
    assert b.req("POST", "/cmd", {"op": "next"}, token=b.token)[0] == 200  # a script with the token, no Origin


def test_preflight(start):
    b = start()
    st, _, h = b.req("OPTIONS", "/cmd", origin=f"http://127.0.0.1:{b.port}")
    assert st == 204 and "X-Liner-Token" in h["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Private-Network" not in h
    assert b.req("OPTIONS", "/cmd", origin="null")[0] == 403
    assert b.req("OPTIONS", "/cmd", origin="https://evil.example")[0] == 403


@pytest.mark.parametrize("length", ["-1", "abc", "1e3", "99999999999", " "])
def test_bad_content_length_gets_400_fast(start, length):
    b = start()
    head = (
        f"POST /cmd HTTP/1.1\r\nHost: 127.0.0.1:{b.port}\r\nOrigin: http://127.0.0.1:{b.port}\r\n"
        f"X-Liner-Token: {b.token}\r\nContent-Length: {length}\r\n\r\n"
    ).encode()
    out, took = raw(b.port, head + b'{"op":"next"}')
    assert out.startswith(b"HTTP/1.1 400") and took < 1.0, (length, out[:60])


def test_short_body_doesnt_hang_a_thread(start):
    b = start()
    b.handler.timeout = 1  # 10 s in real use
    head = (f"POST /cmd HTTP/1.1\r\nHost: 127.0.0.1:{b.port}\r\nX-Liner-Token: {b.token}\r\nContent-Length: 100\r\n\r\n").encode()
    s = socket.create_connection(("127.0.0.1", b.port), timeout=15)
    s.sendall(head + b'{"op"')
    t0 = time.monotonic()
    s.settimeout(15)
    got = s.recv(4096)
    assert time.monotonic() - t0 < 3  # the handler's socket timeout lets go
    s.close()
    assert not got or got.startswith(b"HTTP/1.1 400")
    assert b.req("GET", "/now")[0] == 200


@pytest.mark.parametrize("body", [b"[]", b'"next"', b"null", b"42", b"{bad"])
def test_body_must_be_a_json_object(start, body):
    b = start()
    st, out, _ = b.req("POST", "/cmd", token=b.token, raw=body, headers={"Content-Type": "application/json"})
    assert st == 400


def test_oversized_body(start):
    b = start()
    st, _, _ = b.req("POST", "/cmd", token=b.token, raw=b'{"op":"next","pad":"' + b"x" * 5000 + b'"}')
    assert st == 400
    assert b.req("POST", "/cmd", token=b.token, raw=b"x", headers={"Transfer-Encoding": "chunked"})[0] in (400, 501)


def test_pages_get_the_token_and_a_csp(start, env):
    b = start()
    dist = env["tmp"] / "dist"
    dist.mkdir()
    (dist / "demo.html").write_text('<!doctype html><head><meta name="liner-token" content=""></head><body>hi</body>')
    st, body, h = b.req("GET", "/p/demo.html")
    assert st == 200 and f'<meta name="liner-token" content="{b.token}">'.encode() in body
    assert h["Content-Security-Policy"] == CSP and "frame-src 'none'" in CSP
    assert h["Cache-Control"] == "no-cache" and h["ETag"]
    assert b.req("GET", "/p/demo.html", headers={"If-None-Match": h["ETag"]})[0] == 304
    st, _, h = b.req("GET", "/")
    assert st == 200 and h["Content-Security-Policy"] == CSP
    assert "Content-Security-Policy" not in b.req("GET", "/now")[2]
    assert "media-src 'self' blob: https://audio-ssl.itunes.apple.com;" in CSP
    assert "connect-src 'self' https://audio-ssl.itunes.apple.com;" in CSP
    assert CSP.count("https://") == 2  # nothing else external


def test_cliamp_timeouts_dont_hang(start):
    b = start()
    b.fake.hang.add("next")  # the job never finishes
    t0 = time.monotonic()
    st, body, _ = b.post("/cmd", {"op": "next"}, timeout=10)
    assert st == 502 and json.loads(body)["error"] == "the player didn't answer" and time.monotonic() - t0 < 7


def test_requests_are_logged_without_the_query(start, caplog):
    import logging

    b = start(lan=True)
    with caplog.at_level(logging.DEBUG, logger="liner_notes.bridge"):
        b.req("GET", f"/now?t={b.pair}", host=f"192.168.1.20:{b.port}")
        b.req("GET", "/history")
        b.req("GET", "/now", host="evil.example:1")  # in LAN mode: unpaired
        time.sleep(0.2)  # the line is written after the response goes out
    lines = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "liner_notes.bridge"]
    assert any(lv == logging.DEBUG and m.startswith("GET /now 200 ") for lv, m in lines)
    assert any(lv == logging.INFO and m.startswith("GET /history 200 ") for lv, m in lines)
    assert any(lv == logging.WARNING and m.startswith("GET /now 401 ") for lv, m in lines)
    assert not any(b.pair in m for _, m in lines)  # the ?t= pairing token stays out of the journal


def test_streams_stay_open_over_tls(env):
    import shutil
    import ssl
    import subprocess
    import threading

    import pytest

    from liner_notes import bridge
    from liner_notes.adapters.cliamp import CliampAdapter

    from .bridge_kit import TRACKS
    from .fake_cliamp import FakeCliamp

    if not shutil.which("openssl"):
        pytest.skip("no openssl to make a test certificate")
    crt, key = env["tmp"] / "t.crt", env["tmp"] / "t.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(crt),
        ],
        check=True,
        capture_output=True,
    )
    fake = FakeCliamp(env["sock"], [dict(t) for t in TRACKS])
    srv, _ = bridge.make_server(0, adapter=CliampAdapter(), roots=[env["root"]], tls=(crt, key))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    s = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5), server_hostname="localhost")
    try:
        s.sendall(f"GET /events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        f = s.makefile("rb")
        while not f.readline().startswith(b"data:"):
            pass
        time.sleep(1.2)  # the gone-client check runs; a TLS socket used to look closed
        fake.play(1)
        line = f.readline()
        while line in (b"\n", b": ping\n"):
            line = f.readline()
        assert line.startswith(b"data:")
    finally:
        s.close()
        srv.shutdown()
        srv.server_close()
        fake.close()


def test_page_token_survives_restarts(start, env):
    b1 = start()
    f = env["tmp"] / "state/liner-notes/token"
    assert f.read_text() == b1.token and (f.stat().st_mode & 0o777) == 0o600
    b2 = start(fake=False)
    assert b2.token == b1.token  # a page loaded before the restart keeps its controls
    f.write_text("not a token!")
    b3 = start(fake=False)
    assert b3.token != b1.token and f.read_text() == b3.token and (f.stat().st_mode & 0o777) == 0o600


def test_a_page_without_the_placeholder_is_logged_once(start, env, caplog):
    import logging

    b = start()
    (env["tmp"] / "dist").mkdir()
    (env["tmp"] / "dist/old.html").write_text("<p>a pilot page</p>")
    with caplog.at_level(logging.WARNING, logger="liner_notes.bridge"):
        for _ in range(3):
            b.req("GET", "/p/old.html")
    assert sum("no liner-token placeholder" in r.getMessage() for r in caplog.records) == 1


def test_refused_post_is_logged_at_warning(start, caplog):
    import logging

    b = start()
    with caplog.at_level(logging.WARNING, logger="liner_notes.bridge"):
        b.req("POST", "/cmd", {"op": "next"})
        time.sleep(0.1)
    assert any("POST /cmd refused" in r.getMessage() and "no page token" in r.getMessage() for r in caplog.records)


def test_an_idle_tls_peer_doesnt_stall_the_server(env):
    import shutil
    import ssl
    import subprocess
    import threading

    from liner_notes import bridge
    from liner_notes.adapters.cliamp import CliampAdapter

    if not shutil.which("openssl"):
        pytest.skip("no openssl to make a test certificate")
    crt, key = env["tmp"] / "t.crt", env["tmp"] / "t.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(crt),
        ],
        check=True,
        capture_output=True,
    )
    srv, _ = bridge.make_server(0, adapter=CliampAdapter(), roots=[env["root"]], tls=(crt, key))
    srv.handshake_timeout = 1
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    idle = socket.create_connection(("127.0.0.1", port))  # connects, never says hello
    try:
        t0 = time.monotonic()
        s = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=3), server_hostname="localhost")
        s.sendall(f"GET /now HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        assert s.recv(64).startswith(b"HTTP/1.1 200") and time.monotonic() - t0 < 1.0
        s.close()
        time.sleep(1.3)
        assert idle.recv(1) == b""  # the idle peer was dropped after the handshake timeout
    finally:
        idle.close()
        srv.shutdown()
        srv.server_close()


def test_lan_caps_connections(env):
    from liner_notes.bridge.server import Server

    srv = Server(("127.0.0.1", 0), lambda *a: None, max_connections=2)
    held = []

    class Slow:
        def __init__(self, request, addr, server):
            held.append(request)
            time.sleep(1.0)

    srv.RequestHandlerClass = Slow
    import threading

    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    port = srv.server_address[1]
    socks = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]
    time.sleep(0.3)
    assert len(held) == 2
    socks[2].settimeout(1)
    assert socks[2].recv(1) == b""  # the third was turned away
    time.sleep(1.0)
    extra = socket.create_connection(("127.0.0.1", port))  # and a slot comes back
    time.sleep(0.3)
    assert len(held) == 3
    for s in socks + [extra]:
        s.close()
    srv.shutdown()
    srv.server_close()


def test_pairing_url_stays_off_a_log(env, tmp_path):
    import io

    from liner_notes.bridge.server import announce_pairing

    f = announce_pairing("http://192.168.1.20:47391/?t=secret-pairing", False, out=io.StringIO())
    assert f.read_text().strip() == "http://192.168.1.20:47391/?t=secret-pairing" and (f.stat().st_mode & 0o777) == 0o600

    class Tty(io.StringIO):
        def isatty(self):
            return True

    out = Tty()
    assert announce_pairing("http://x/?t=abc", True, out=out) is None and "?t=abc" in out.getvalue()


def test_port_holder_and_exit_code(env, monkeypatch, caplog):
    import logging
    import os

    from liner_notes.bridge import server

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        pid, cmd = server.port_holder(port)
        assert pid == os.getpid() and "python" in cmd
        monkeypatch.setattr(server, "setup_logging", lambda: None)
        with caplog.at_level(logging.ERROR, logger="liner_notes.bridge"), pytest.raises(SystemExit) as e:
            server.serve(port)
        assert e.value.code == server.EXIT_PORT_BUSY
        assert any(f"port {port} is taken by pid {os.getpid()}" in r.getMessage() for r in caplog.records)
    finally:
        s.close()
