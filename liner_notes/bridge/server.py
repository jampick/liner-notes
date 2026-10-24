"""The HTTP side of the bridge: routes, the gate, logging, and starting it all.

Safety. By default it binds to 127.0.0.1 and checks the Host header, so a DNS-rebinding page can't
reach it under another name. Cross-origin reads are answered for the bridge's own origin, and
Origin null (a page opened from disk) gets CORS on /now and /theme only. Nothing with Origin null
can POST, and every POST needs the X-Liner-Token header: the page token from state()/token, which
the bridge writes into the pages it serves (`<meta name="liner-token" content="">`). A POST with a
missing or stale token gets 403 {"ok": false, "reason": "token"}, so a page can reload itself to
pick up the current one. Pages get a strict Content-Security-Policy.

With --lan it binds to every interface and every request from another machine must carry the
pairing token: in the URL once (?t=...), then in a cookie. The pairing token is a different secret
from the page token and never appears in a response body. LAN mode caps open connections.
"""

import errno
import hashlib
import hmac
import html
import json
import logging
import os
import queue
import re
import select
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import __version__, adapters, loudness, paths
from ..ids import youtube_id
from . import contract, media, tokens
from .bands import Bands
from .lyrics import Lyrics
from .now import Library, Now
from .theme import Theme

PORT = 47391
log = logging.getLogger("liner_notes.bridge")

APPLE_PREVIEWS, CSP, TOKEN_META, NULL_CORS, MAX_BODY = (
    contract.APPLE_PREVIEWS,
    contract.CSP,
    contract.TOKEN_META,
    contract.NULL_CORS,
    contract.MAX_BODY,
)
EXIT_PORT_BUSY = 3
LAN_MAX_CONNECTIONS = 64
QUIET = ("/now", "/bands", "/theme", "/song", "/events")  # polled; logged at DEBUG
CHUNK = 256 * 1024


class Pages:
    """Built pages with the token written in and the CSP listing their inline scripts' hashes,
    kept while the file is unchanged (a few MB each)."""

    def __init__(self, token, size=8):
        self.token, self.size, self.lock, self.cache = token, size, threading.Lock(), {}
        self.warned = set()

    def get(self, f):
        st = f.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self.cache.get(str(f))
            if hit and hit[0] == stamp:
                return hit[1], hit[2], st.st_mtime, hit[3]
        raw = f.read_bytes()
        if TOKEN_META not in raw and (str(f), stamp) not in self.warned:
            self.warned.add((str(f), stamp))
            log.warning("%s has no liner-token placeholder; its buttons will get 403 (rebuild it)", f.name)
        body = contract.inject_token(raw, self.token)
        etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
        policy = contract.csp(contract.script_hashes(body))  # the token only touches a meta tag
        with self.lock:
            if str(f) not in self.cache and len(self.cache) >= self.size:
                self.cache.pop(next(iter(self.cache)))
            self.cache[str(f)] = (stamp, body, etag, policy)
        return body, etag, st.st_mtime, policy


class BadRequest(Exception):
    pass


class Server(ThreadingHTTPServer):
    """Threaded, with the TLS handshake done in the request's own thread (with a timeout), so a peer
    that connects and says nothing can't stall accept(), and an optional cap on open connections."""

    daemon_threads = True
    handshake_timeout = 10

    def __init__(self, addr, handler, max_connections=None):
        super().__init__(addr, handler)
        self.slots = threading.BoundedSemaphore(max_connections) if max_connections else None

    def process_request(self, request, client_address):
        if self.slots and not self.slots.acquire(blocking=False):
            log.warning("too many connections; dropped one from %s", client_address[0])
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self.slots:
                self.slots.release()

    def finish_request(self, request, client_address):
        if isinstance(request, ssl.SSLSocket):
            request.settimeout(self.handshake_timeout)
            try:
                request.do_handshake()
            except (ssl.SSLError, OSError) as e:
                log.debug("TLS handshake with %s failed: %s", client_address[0], e)
                return
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        """A client that hung up mid-request is routine; anything else goes to the log, not bare stderr."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            log.debug("client %s went away: %s", client_address[0], exc)
        else:
            log.exception("request from %s failed", client_address[0])


def make_handler(lib, now, port, lan=False, token=None, scheme="http", lyrics=None, bands=None, theme=None, page_token=None):
    """token is the LAN pairing token (made here when lan and none is given); page_token is the
    POST token written into pages (state()/token by default)."""
    pair = token or (tokens.pairing_token() if lan else None)
    token = page_token or tokens.page_token()
    own = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"http://[::1]:{port}"}
    loop_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    lyrics = lyrics or Lyrics(lambda: now.adapter, now.fresh)
    bands = bands or Bands()
    theme = theme or Theme()
    pages = Pages(token)
    depth = media.Depth()

    services = {"lib": lib, "now": now, "lyrics": lyrics, "bands": bands, "theme": theme, "token": token, "pair": pair}

    def same(given, secret):
        return bool(given) and bool(secret) and hmac.compare_digest(given.encode("utf-8", "replace"), secret.encode())

    def same_token(given):
        return same(given, token)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"liner-notes/{__version__}"
        timeout = 10  # a stalled client can't hold a thread: reads and writes give up after this
        now_adapter = now.adapter.name
        _status = None

        def log_request(self, code="-", size="-"):
            pass  # _run logs every request, without the query string (it can carry the pairing token)

        def log_message(self, fmt, *a):
            log.debug("http: " + fmt, *a)

        # --- the gate -------------------------------------------------------------------
        def loopback(self):
            return self.client_address[0] in ("127.0.0.1", "::1") and self.headers.get("Host", "") in loop_hosts

        def token_ok(self):
            if not lan or self.loopback():
                return True
            m = re.search(r"(?:^|;\s*)ln_pair=([^;\s]+)", self.headers.get("Cookie", ""))
            given = self.headers.get("X-Liner-Pair") or (m.group(1) if m else None) or urllib.parse.unquote(self.query().get("t", ""))
            return same(given, pair)

        def own_origin(self, o):
            return o in own or (lan and o == f"{scheme}://{self.headers.get('Host', '')}")

        def cors_origin(self):
            """The Origin to echo back, or None."""
            o = self.headers.get("Origin")
            if not o:
                return None
            if self.own_origin(o):
                return o
            if o == "null" and self.route() in NULL_CORS and self.command in ("GET", "HEAD"):
                return o
            return None

        def host_ok(self):
            return lan or self.headers.get("Host", "") in loop_hosts

        def gate(self, post=False):
            if not self.host_ok():
                self.send_json({"ok": False, "error": "host"}, 403)
                return False
            if not self.token_ok():
                self.send_json({"ok": False, "error": "pair first: open the address liner-notes serve --lan printed"}, 401)
                return False
            o = self.headers.get("Origin")
            if post:
                if o is not None and not self.own_origin(o):
                    self.send_json({"ok": False, "error": "origin"}, 403)
                    return False
                given = self.headers.get("X-Liner-Token")
                if not same_token(given):
                    log.warning(
                        "POST %s refused from %s: %s page token", self.route(), self.client_address[0], "a stale" if given else "no"
                    )
                    self.send_json(contract.token_refusal(), 403)
                    return False
                return True
            if o is not None and o != "null" and not self.own_origin(o):
                self.send_json({"ok": False, "error": "origin"}, 403)
                return False
            return True

        def route(self):
            return self.path.split("?", 1)[0]

        def query(self):
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            return dict(p.split("=", 1) for p in q.split("&") if "=" in p)

        # --- responses ------------------------------------------------------------------
        def send_response(self, code, message=None):
            self._status = code
            super().send_response(code, message)

        def head(self, code, ctype="application/json", extra=None, cache=contract.CACHE["api"], csp=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", csp or CSP)
                self.send_header("Referrer-Policy", "no-referrer")
            o = self.cors_origin()
            if o:
                self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            if lan and self.query().get("t") and not self.loopback() and self.token_ok():
                secure = "; Secure" if scheme == "https" else ""
                self.send_header("Set-Cookie", f"ln_pair={pair}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000{secure}")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.head(code, extra={"Content-Length": str(len(body))})
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_body(self, body, ctype, etag=None, mtime=None, cache=contract.CACHE["page"], csp=None):
            extra = {}
            if etag:
                extra["ETag"] = etag
            if mtime:
                extra["Last-Modified"] = formatdate(mtime, usegmt=True)
            if etag and self.headers.get("If-None-Match") == etag:
                return self.head(304, ctype, extra, cache, csp)
            self.head(200, ctype, {**extra, "Content-Length": str(len(body))}, cache, csp)
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_file(self, p, ctype, cache=contract.CACHE["picture"]):
            self.send_range(Path(p), ctype, cache=cache, ranges=False)

        def send_range(self, f, ctype, cache=contract.CACHE["video"], ranges=True):
            """A file in chunks, with ETag and Last-Modified, and one byte range when asked (206/416)."""
            st = f.stat()
            size = st.st_size
            etag = f'"{st.st_mtime_ns:x}-{size:x}"'
            base = {"ETag": etag, "Last-Modified": formatdate(st.st_mtime, usegmt=True)}
            if ranges:
                base["Accept-Ranges"] = "bytes"
            if self.headers.get("If-None-Match") == etag:
                return self.head(304, ctype, base, cache)
            r = media.parse_range(self.headers.get("Range"), size) if ranges else None
            if r == media.UNSATISFIABLE:
                return self.head(416, ctype, {**base, "Content-Range": f"bytes */{size}", "Content-Length": "0"}, cache)
            first, last = r if r else (0, size - 1)
            extra = {**base, "Content-Length": str(max(0, last - first + 1))}
            if r:
                extra["Content-Range"] = f"bytes {first}-{last}/{size}"
            self.head(206 if r else 200, ctype, extra, cache)
            if self.command == "HEAD":
                return
            with f.open("rb") as fh:
                fh.seek(first)
                left = last - first + 1
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        self.close_connection = True  # the file shrank under us; the length we sent is wrong
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def client_gone(self):
            """True once the client has closed its end (readable with nothing to read). A stream's client
            sends nothing after its request, so on TLS, where a peek isn't possible, one byte is read."""
            conn = self.connection
            try:
                r, _, _ = select.select([conn], [], [], 0)
                if not r:
                    return False
                if not hasattr(conn, "pending"):
                    return conn.recv(1, socket.MSG_PEEK) == b""
                conn.settimeout(0.05)
                try:
                    return conn.recv(1) == b""
                except (ssl.SSLWantReadError, TimeoutError, BlockingIOError):
                    return False
                finally:
                    conn.settimeout(self.timeout)
            except (OSError, ValueError):
                return True

        # --- dispatch and logging -------------------------------------------------------
        def _run(self, fn):
            t0, self._status = time.monotonic(), None
            try:
                fn()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except BadRequest as e:
                self.close_connection = True
                self.send_json({"ok": False, "error": str(e)}, 400)
            except Exception:
                log.exception("%s %s failed", self.command, self.route())
                self.close_connection = True
                if self._status is None:
                    try:
                        self.send_json({"ok": False, "error": "internal error"}, 500)
                    except OSError:
                        pass
            ms = (time.monotonic() - t0) * 1000
            st, path = self._status or 0, self.route()
            if st >= 500:
                level = logging.ERROR
            elif st in (400, 401, 403, 416):
                level = logging.WARNING
            elif path in QUIET or path.startswith(("/v/", "/m/", "/d/")):
                level = logging.DEBUG
            else:
                level = logging.INFO
            log.log(level, "%s %s %s %.1fms", self.command, path, st, ms)

        def do_GET(self):
            self._run(self.get)

        def do_HEAD(self):
            self._run(self.get)

        def do_POST(self):
            self._run(self.post)

        def do_OPTIONS(self):
            self._run(self.options)

        def options(self):
            o = self.headers.get("Origin")
            if not self.host_ok() or not o or not self.own_origin(o):
                return self.send_json({"ok": False, "error": "origin"}, 403)
            self.head(
                204,
                extra={
                    "Access-Control-Allow-Methods": "GET, POST",
                    "Access-Control-Allow-Headers": "Content-Type, X-Liner-Token",
                    "Access-Control-Max-Age": "600",
                    "Content-Length": "0",
                },
            )

        def get(self):
            if not self.gate():
                return
            path = self.route()
            if path == "/now":
                return self.send_json(now.payload())
            bank, matcher = lib.get()
            if path == "/song":
                return self.send_json(playing_song(now.payload(), bank))
            if self.command == "HEAD" and path in ("/events", "/bands"):
                return self.send_json({"ok": False, "error": "streams answer GET only"}, 405)
            if path == "/events":
                return self.events()
            if path == "/bands":
                return self.bands()
            if path == "/lyrics":
                return self.lyrics(bank, matcher)
            if path == "/history":
                return self.history(matcher)
            if path == "/theme":
                return self.send_json(theme.get())
            if path in ("/", "/index.html"):
                return self.index(bank)
            if path == "/v/":
                return self.send_json({"ok": True, "ids": media.video_ids()})
            m = media.VIDEO.fullmatch(path)
            if m:
                f = media.find_video(m.group(1))
                return self.send_range(f, "video/mp4") if f else self.not_found()
            m = contract.JOURNEY.fullmatch(path)
            if m and m.group(1) in bank.journeys:
                return self.send_json(bank.journeys[m.group(1)])
            m = contract.SONG.fullmatch(path)
            if m and m.group(1) in bank.songs:
                return self.send_json(bank.songs[m.group(1)])
            m = media.PICTURE.fullmatch(path)
            if m:
                f = media.picture(m.group(2), m.group(3))
                return self.send_file(f, media.TYPES[m.group(4)]) if f else self.not_found()
            m = contract.COVER.fullmatch(path)
            if m:
                f = media.cover(m.group(2), matcher)
                return self.send_file(f, "image/webp") if f else self.not_found()
            m = media.DEPTH.fullmatch(path)
            if m:
                f = depth.find(m.group(1), m.group(2), m.group(3), bank.journey_dirs.get(m.group(1)))
                return self.send_file(f, "image/png") if f else self.send_json({"ok": False, "error": "no depth map"}, 404)
            m = contract.PAGE.fullmatch(path)
            if m and (paths.dist() / f"{m.group(1)}.html").is_file():
                body, etag, mtime, policy = pages.get(paths.dist() / f"{m.group(1)}.html")
                return self.send_body(body, "text/html; charset=utf-8", etag, mtime, csp=policy)
            m = contract.STAGE.fullmatch(path)
            if m:
                for p in (paths.cache() / "stage" / f"{m.group(1)}.stage.json", paths.dist() / f"{m.group(1)}.stage.json"):
                    if p.is_file():
                        return self.send_file(p, "application/json", cache=contract.CACHE["page"])
            self.not_found()

        def not_found(self):
            self.send_json({"ok": False, "error": "not found"}, 404)

        def index(self, bank):
            rows = "".join(
                f"<li><a href='/p/{s}.html#companion'>{html.escape(j['title'])}</a> <small>{len(j['songs'])} songs</small></li>"
                for s, j in sorted(bank.journeys.items())
                if (paths.dist() / f"{s}.html").is_file()
            )
            body = (
                f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>Liner Notes</title><style>body{{font:17px/1.5 Georgia,serif;margin:32px 16px;background:#111;color:#eee}}"
                f"a{{color:#f0c27b}}small{{color:#999}}li{{margin:10px 0}}</style><h1>Liner Notes</h1><ul>{rows or '<li>No built journeys yet.</li>'}</ul>"
            ).encode()
            self.send_body(body, "text/html; charset=utf-8", cache=contract.CACHE["api"])

        def history(self, matcher):
            ad = now.adapter
            if "history" not in ad.capabilities:
                return self.send_json({"ok": False, "plays": [], "error": f"{ad.name} keeps no history"})
            out = []
            for h in ad.history(200):
                t = h.get("track") or {}
                out.append(
                    {
                        "yt": youtube_id(t.get("path")),
                        "song": matcher.match(t),
                        "at": h.get("played_at"),
                        "secs": t.get("duration_secs") or 0,
                    }
                )
            return self.send_json({"ok": True, "plays": out})

        def events(self):
            self.head(200, "text/event-stream", {"Connection": "close"})
            self.close_connection = True
            v = now.version
            self.wfile.write(f"data: {json.dumps({'version': v})}\n\n".encode())
            self.wfile.flush()
            while not self.client_gone():
                nv = now.wait(v, timeout=5)
                self.wfile.write((f"data: {json.dumps({'version': nv})}\n\n" if nv != v else ": ping\n\n").encode())
                self.wfile.flush()
                v = nv

        def bands(self):
            if "bands" not in now.adapter.capabilities:
                return self.send_json({"ok": False, "error": f"{now.adapter.name} has no visualizer stream"}, 404)
            self.head(200, "text/event-stream", {"Connection": "close"})
            self.close_connection = True
            q = bands.subscribe()
            try:
                self.wfile.write(b": bands\n\n")
                self.wfile.flush()
                sent = checked = time.monotonic()
                while True:
                    try:
                        line = q.get(timeout=0.5)
                    except queue.Empty:
                        line = None
                    t = time.monotonic()
                    if line:
                        self.wfile.write(f"data: {line}\n\n".encode())
                        self.wfile.flush()
                        sent = t
                    elif t - sent > 5:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        sent = t
                    if t - checked > 0.5:
                        checked = t
                        if self.client_gone():
                            return
            finally:
                bands.unsubscribe(q)

        def lyrics(self, bank, matcher):
            """Lyrics for the song the page asks about (?yt= and/or ?song=), and only if it is the one playing."""
            np, gen = now.fresh()
            if not np:
                return self.send_json({"ok": False, "lines": [], "source": None, "error": "nothing playing"})
            song = matcher.match(np.as_track())
            out = {"yt": np.ids.get("youtube"), "song": song}
            q = self.query()
            want_yt, want_song = urllib.parse.unquote(q.get("yt", "")), urllib.parse.unquote(q.get("song", ""))
            if (want_yt or want_song) and not ((want_yt and want_yt == out["yt"]) or (want_song and want_song == out["song"])):
                return self.send_json(contract.lyrics_not_playing(out["yt"], out["song"]))
            res = lyrics.get(np, gen, bank.songs.get(song or ""))
            return self.send_json({**res, **out})

        # --- changes --------------------------------------------------------------------
        def read_body(self):
            if self.headers.get("Transfer-Encoding"):
                raise BadRequest("send a Content-Length")
            cl = self.headers.get("Content-Length")
            cl = "0" if cl is None else cl.strip()
            if not re.fullmatch(r"\d{1,9}", cl):
                raise BadRequest("bad Content-Length")
            n = int(cl)
            if n > MAX_BODY:
                raise BadRequest("body too large")
            raw = self.rfile.read(n) if n else b""
            if len(raw) != n:
                raise BadRequest("short body")
            try:
                body = json.loads(raw or b"{}")
            except ValueError as e:
                raise BadRequest("bad json") from e
            if not isinstance(body, dict):
                raise BadRequest("the body must be a JSON object")
            return body

        def post(self):
            if not self.gate(post=True):
                self.close_connection = True  # the body wasn't read
                return
            body = self.read_body()
            path = self.route()
            ad = now.adapter
            if path == "/cmd" and body.get("op") in ("toggle", "next", "prev"):
                try:
                    ok = ad.command(body["op"])
                except Exception as e:
                    log.warning("cmd %s failed: %s", body["op"], e)
                    return self.send_json({"ok": False, "error": "the player didn't answer"}, 502)
                return self.send_json({"ok": bool(ok)})
            if path == "/play" and (
                re.fullmatch(r"[\w-]{11}", str(body.get("yt") or "")) or re.fullmatch(r"[a-z0-9-]+", str(body.get("song") or ""))
            ):
                if "jump" not in ad.capabilities:
                    return self.send_json({"ok": False, "error": f"{ad.name} can't jump to a song"})
                _, matcher = lib.get()
                yt, song = body.get("yt"), body.get("song")
                try:
                    tracks = ad.queue()
                    idx = next((i for i, t in enumerate(tracks) if yt and youtube_id(t.get("path")) == yt), None)
                    if idx is None and song:
                        idx = next((i for i, t in enumerate(tracks) if matcher.match(t) == song), None)
                    if idx is None:
                        return self.send_json({"ok": False, "error": "not in the loaded playlist"})
                    ok = ad.jump(idx)
                except Exception as e:
                    log.warning("play failed: %s", e)
                    return self.send_json({"ok": False, "error": "the player didn't answer"}, 502)
                return self.send_json({"ok": bool(ok), "index": idx})
            self.send_json({"ok": False, "error": "unknown"}, 400)

    Handler.services = services  # for tests and tools: the lyrics cache, the bands reader, the token
    return Handler


def playing_song(np, bank):
    """/now plus the notes for the song it matched, its journey and the era its year falls in.
    Read-only and small, so a bar widget can poll it without building a page."""
    if not np.get("ok"):
        return np
    out = {k: np.get(k) for k in ("ok", "source", "state", "title", "artist", "album", "song")}
    out.update(year=None, journey=None, era=None, notes=None, review=None, draft=False)
    s = bank.songs.get(np.get("song") or "")
    if not s:
        return out
    review = s.get("review") or {"state": "draft"}
    notes = s.get("notes") or {}
    out.update(
        title=s["title"],
        artist=s["artist"],
        album=s.get("album") or np.get("album"),
        year=s.get("year"),
        notes={k: notes[k] for k in ("listen_for", "made", "context", "leads_to", "unverified") if notes.get(k)},
        review=review,
        draft=review.get("state") != "reviewed",
    )
    j = bank.journeys.get(np.get("journey") or "")
    if j:
        out["journey"] = {"slug": j["slug"], "title": j["title"]}
        era = next((e for e in j.get("eras") or [] if e["from"] <= (s.get("year") or 0) <= e["to"]), None)
        if era:
            out["era"] = {"n": era["n"], "name": era["name"], "years": era["years"]}
    return out


def lan_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def tailscale_cert():
    """(DNS name, Tailscale IP, cert file, key file) for this machine's *.ts.net name, or raise with what to do.

    The certificate comes from Let's Encrypt through Tailscale, so the machine's tailnet name ends up in the
    public Certificate Transparency logs. Tailscale documents this; it's why HTTPS is opt-in (--tailscale).
    """
    try:
        st = json.loads(subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise RuntimeError("tailscale isn't running here") from e
    me = st.get("Self") or {}
    name, ips = (me.get("DNSName") or "").rstrip("."), me.get("TailscaleIPs") or []
    if not name.endswith(".ts.net") or not ips:
        raise RuntimeError("this machine has no MagicDNS name; turn on MagicDNS and HTTPS certificates in the Tailscale admin console")
    d = paths.state() / "tls"
    d.mkdir(parents=True, exist_ok=True)
    crt, key = d / f"{name}.crt", d / f"{name}.key"
    if not crt.exists() or time.time() - crt.stat().st_mtime > 30 * 86400:
        r = subprocess.run(
            ["tailscale", "cert", "--cert-file", str(crt), "--key-file", str(key), name], capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"tailscale cert failed: {r.stderr.strip()}. If it's a permissions error: sudo tailscale set --operator=$USER"
            )
    return name, next((i for i in ips if ":" not in i), ips[0]), crt, key


def setup_logging():
    """Requests and errors to stderr (the journal, under systemd). LINER_NOTES_LOG sets the level."""
    level = getattr(logging, (os.environ.get("LINER_NOTES_LOG") or "INFO").upper(), logging.INFO)
    fmt = "%(levelname)s %(message)s" if os.environ.get("JOURNAL_STREAM") else "%(asctime)s %(levelname)s %(message)s"
    lg = logging.getLogger("liner_notes")
    if not lg.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(fmt))
        lg.addHandler(h)
    lg.setLevel(level)


def make_server(port=PORT, lan=False, token=None, roots=None, adapter=None, tls=None, bind=None, leveler=None):
    """tls = (cert file, key file) serves HTTPS, which a phone needs for Wake Lock and offline caching.
    leveler (a loudness.Leveler) sets cliamp's gain at every song change; only serve() passes one.
    Returns (server, pairing token); the pairing token is None unless lan. The page token is in state()/token."""
    lib = Library(roots)
    now = Now(lib, adapter or adapters.pick(), observers=[leveler.observe] if leveler else ())
    pair = (token or tokens.pairing_token()) if lan else None
    srv = Server((bind or ("0.0.0.0" if lan else "127.0.0.1"), port), lambda *a: None, max_connections=LAN_MAX_CONNECTIONS if lan else None)
    srv.RequestHandlerClass = make_handler(lib, now, srv.server_address[1], lan, pair, "https" if tls else "http")
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(tls[0]), str(tls[1]))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    return srv, pair


def port_holder(port, proc=Path("/proc")):
    """(pid, command line) of the process listening on this TCP port, when /proc says; else None."""
    inodes = set()
    for name in ("net/tcp", "net/tcp6"):
        try:
            rows = (proc / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) > 9 and f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:
                inodes.add(f[9])
    if not inodes:
        return None
    wanted = {f"socket:[{i}]" for i in inodes}
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            fds = list((d / "fd").iterdir())
        except OSError:
            continue  # someone else's process, or gone
        for fd in fds:
            try:
                if os.readlink(fd) not in wanted:
                    continue
                cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                continue  # that fd closed while we looked; the others still count
            return int(d.name), cmd
    return None


def announce_pairing(url, tailscale, out=None):
    """The pairing URL carries the pairing token: printed once to a terminal, never into a log.
    Without a terminal (systemd) it goes to state()/pairing, mode 0600, and the log says where."""
    out = out or sys.stdout
    if out.isatty():
        print(
            f"On your {'tailnet' if tailscale else 'network'}: {url}\nAnyone with this address can see what you play and skip tracks. Stop the server to revoke it.",
            file=out,
        )
        if not tailscale:
            print(
                "Plain http on a network address isn't a secure context, so phones won't keep the screen awake. --tailscale serves HTTPS.",
                file=out,
            )
        try:
            print(subprocess.run(["qrencode", "-t", "ansiutf8", url], capture_output=True, text=True, timeout=5).stdout, file=out)
        except (OSError, subprocess.SubprocessError):
            pass
        return None
    f = paths.state() / "pairing"
    tokens.write_private(f, url + "\n")
    log.info("pairing address written to %s (readable by you only)", f)
    return f


def _stop(*_):
    raise KeyboardInterrupt


def serve(port=PORT, lan=False, token=None, tailscale=False, adapter=None, level=False):
    """level: keep every song at the same loudness by setting cliamp's gain (see loudness.py)."""
    setup_logging()
    signal.signal(signal.SIGTERM, _stop)  # so a systemd stop runs the finally below
    leveler = loudness.Leveler() if level else None
    tls, bind, host = None, None, None
    if tailscale:
        try:
            host, bind, crt, key = tailscale_cert()
        except RuntimeError as e:
            sys.exit(f"liner-notes: {e}")
        tls, lan = (crt, key), True
    try:
        srv, pair = make_server(port, lan, token, adapter=adapter, tls=tls, bind=bind, leveler=leveler)
    except OSError as e:
        busy = e.errno == errno.EADDRINUSE
        holder = port_holder(port) if busy else None
        if holder:
            log.error("port %s is taken by pid %s: %s", port, holder[0], holder[1])
        else:
            log.error("can't serve on port %s: %s", port, e)
        sys.exit(EXIT_PORT_BUSY if busy else 1)
    log.info("liner-notes %s on port %s, player %s", __version__, srv.server_address[1], srv.RequestHandlerClass.now_adapter)
    if not tailscale:
        print(f"Liner Notes is serving http://127.0.0.1:{port}/  (player: {srv.RequestHandlerClass.now_adapter})", flush=True)
    if lan:
        announce_pairing(f"https://{host}:{port}/?t={pair}" if tailscale else f"http://{lan_address()}:{port}/?t={pair}", tailscale)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if leveler:
            leveler.close()
            leveler.restore()
