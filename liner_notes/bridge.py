"""The local bridge between Liner Notes pages and cliamp.

    liner-notes serve            http://127.0.0.1:47391, this machine only
    liner-notes serve --lan      also on your network, for a tablet or phone, behind a pairing token

GET  /                         the journeys, as a small index page
GET  /now                      what cliamp is playing: title, artist, state, position, the matched song id
                               and the journey that has it
GET  /events                   server-sent events, one per now-playing change (from cliamp's IPC subscribe)
GET  /bands                    cliamp's visualizer bands as server-sent events (`cliamp visstream`)
GET  /lyrics                   timed lyrics for the current track (cliamp's `lyrics` operation, cached)
GET  /history                  what cliamp played recently, newest first, with song ids
GET  /theme                    the current Omarchy theme's colours, mode and font, when there is one
GET  /journey/<slug>.json      a journey;  GET /song/<id>.json  one song from the bank
GET  /p/<slug>.html            a built page;  GET /p/<slug>.stage.json  its stage data (liner-notes media)
GET  /m/<slug>/<dir>/<file>    a stage picture from the cache
POST /play {yt, song}          jump cliamp to that song in the loaded playlist
POST /cmd {op}                 toggle, next or prev

Safety. By default it binds to 127.0.0.1, checks the Host header (so a DNS-rebinding page can't
reach it under another name) and answers cross-origin calls only from file:// pages and itself.
With --lan it binds to every interface and every request from another machine must carry the
pairing token: in the URL once (?t=...), then in a cookie. The token is new each start unless you
pass --token. Nothing here writes to disk except the lyrics cache in memory.
"""
import hmac
import html
import json
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cliamp, paths
from .bank import Bank
from .ids import slug as slugify
from .ids import youtube_id
from .match import Matcher

PORT = 47391
THEME = Path.home() / ".local/state/omarchy/current"


class Library:
    """The bank and matcher, reloaded when a song or journey file changes."""

    def __init__(self, roots=None):
        self.roots, self.stamp, self.at = roots, None, 0
        self.lock = threading.Lock()
        self._load()

    def _stamp(self):
        files = [p for r in (self.roots or paths.roots()) for d in ("songs", "journeys") for p in (Path(r) / d).rglob("*.json")]
        return (len(files), max((p.stat().st_mtime for p in files), default=0))

    def _load(self):
        self.bank = Bank(self.roots)
        self.matcher = Matcher(self.bank.songs)
        self.stamp = self._stamp()

    def get(self):
        with self.lock:
            if time.monotonic() - self.at > 5:
                self.at = time.monotonic()
                if self._stamp() != self.stamp:
                    self._load()
            return self.bank, self.matcher

    def journey_for(self, song, playlist=None):
        bank, _ = self.get()
        if not song:
            return None
        if playlist and slugify(playlist) in bank.journeys and song in bank.journeys[slugify(playlist)]["songs"]:
            return slugify(playlist)
        have = bank.journeys_with(song)
        return have[0] if have else None


class Now:
    """The latest now-playing, kept fresh by cliamp's event stream, with a polling fallback."""

    def __init__(self, lib):
        self.lib, self.snap, self.version = lib, None, 0
        self.cond = threading.Condition()
        threading.Thread(target=self._watch, daemon=True).start()

    def _sig(self, snap):
        t = (snap or {}).get("track") or {}
        return (t.get("path"), (snap or {}).get("state"), (snap or {}).get("index"), (snap or {}).get("playlist_revision"))

    def _update(self, snap):
        with self.cond:
            changed = self._sig(snap) != self._sig(self.snap)
            self.snap = snap
            if changed:
                self.version += 1
                self.cond.notify_all()

    def _watch(self):
        while True:
            try:
                self._update(cliamp.state())
                for ev in cliamp.subscribe():
                    if ev.get("event") in ("runtime.state", "runtime.playback", "runtime.playlist") and isinstance(ev.get("data"), dict) and "track" in ev["data"]:
                        self._update(ev["data"])
            except cliamp.Unavailable:
                self._update(cliamp.state())
                time.sleep(2)
            except Exception:  # keep watching whatever happens
                time.sleep(2)

    def payload(self):
        s = cliamp.state()  # fresh, for the position
        if s:
            self._update(s)
        if not s or not s.get("track"):
            return {"ok": False}
        t = s["track"]
        _, m = self.lib.get()
        song = m.match(t)
        return {"ok": True, "state": s.get("state"), "title": t.get("title"), "artist": t.get("artist"),
                "yt": youtube_id(t.get("path")), "song": song, "journey": self.lib.journey_for(song, s.get("playlist")),
                "position": s.get("position") or 0, "duration": s.get("duration") or t.get("duration_secs") or 0,
                "shuffle": s.get("shuffle"), "playlist": s.get("playlist")}

    def wait(self, version, timeout=15):
        with self.cond:
            self.cond.wait_for(lambda: self.version != version, timeout)
            return self.version


def make_handler(lib, now, port, lan=False, token=None):
    allowed = {"null", f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    loop_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    lyrics = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # --- the gate -------------------------------------------------------------------
        def loopback(self):
            return self.client_address[0] in ("127.0.0.1", "::1") and self.headers.get("Host", "") in loop_hosts

        def token_ok(self):
            if not lan or self.loopback():
                return True
            m = re.search(r"(?:^|;\s*)ln_token=([\w-]+)", self.headers.get("Cookie", ""))
            given = self.headers.get("X-Liner-Token") or (m.group(1) if m else None) or self.query().get("t")
            return bool(given) and hmac.compare_digest(given, token)

        def origin_ok(self):
            o = self.headers.get("Origin")
            if lan and o and o == f"http://{self.headers.get('Host', '')}":
                return True
            return o is None or o in allowed

        def host_ok(self):
            return lan or self.headers.get("Host", "") in loop_hosts

        def gate(self, post=False):
            if not self.host_ok():
                self.send_json({"ok": False, "error": "host"}, 403)
                return False
            if not self.token_ok():
                self.send_json({"ok": False, "error": "pair first: open the address liner-notes serve --lan printed"}, 401)
                return False
            ok = self.origin_ok() if not post else (self.headers.get("Origin") in allowed or (lan and self.origin_ok() and self.headers.get("Origin")))
            if not ok:
                self.send_json({"ok": False, "error": "origin"}, 403)
                return False
            return True

        def query(self):
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            return dict(p.split("=", 1) for p in q.split("&") if "=" in p)

        # --- responses ------------------------------------------------------------------
        def head(self, code, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            o = self.headers.get("Origin")
            if o in allowed or (lan and o and o == f"http://{self.headers.get('Host', '')}"):
                self.send_header("Access-Control-Allow-Origin", o)
                self.send_header("Vary", "Origin")
            if lan and self.query().get("t") and self.token_ok():
                self.send_header("Set-Cookie", f"ln_token={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.head(code, extra={"Content-Length": str(len(body))})
            self.wfile.write(body)

        def send_file(self, p, ctype):
            body = Path(p).read_bytes()
            self.head(200, ctype, {"Content-Length": str(len(body))})
            self.wfile.write(body)

        def do_OPTIONS(self):
            if not self.host_ok() or not self.origin_ok():
                return self.send_json({"ok": False, "error": "origin"}, 403)
            self.head(204, extra={"Access-Control-Allow-Methods": "GET, POST", "Access-Control-Allow-Headers": "Content-Type",
                                  "Access-Control-Allow-Private-Network": "true", "Content-Length": "0"})

        def do_GET(self):
            if not self.gate():
                return
            path = self.path.split("?")[0]
            bank, matcher = lib.get()
            if path == "/now":
                return self.send_json(now.payload())
            if path == "/events":
                return self.events()
            if path == "/bands":
                return self.bands()
            if path == "/lyrics":
                return self.lyrics(matcher)
            if path == "/history":
                res = cliamp.result("history", {"limit": 200}) or {}
                out = []
                for h in res.get("history") or []:
                    t = h.get("track") or {}
                    out.append({"yt": youtube_id(t.get("path")), "song": matcher.match(t), "at": h.get("played_at"), "secs": t.get("duration_secs") or 0})
                return self.send_json({"ok": bool(res), "plays": out})
            if path == "/theme":
                return self.send_json(theme())
            if path in ("/", "/index.html"):
                return self.index(bank)
            m = re.fullmatch(r"/journey/([a-z0-9-]+)\.json", path)
            if m and m.group(1) in bank.journeys:
                return self.send_json(bank.journeys[m.group(1)])
            m = re.fullmatch(r"/song/([a-z0-9-]+)\.json", path)
            if m and m.group(1) in bank.songs:
                return self.send_json(bank.songs[m.group(1)])
            m = re.fullmatch(r"/m/[a-z0-9-]+/(culture|artists)/([\w.-]+\.(webp|jpg|png))", path)
            if m and (paths.cache() / m.group(1) / m.group(2)).is_file():
                return self.send_file(paths.cache() / m.group(1) / m.group(2), "image/" + m.group(3))
            m = re.fullmatch(r"/p/([a-z0-9-]+)\.html", path)
            if m and (paths.dist() / f"{m.group(1)}.html").is_file():
                return self.send_file(paths.dist() / f"{m.group(1)}.html", "text/html; charset=utf-8")
            m = re.fullmatch(r"/p/([a-z0-9-]+)\.stage\.json", path)
            if m:
                for p in (paths.cache() / "stage" / f"{m.group(1)}.stage.json", paths.dist() / f"{m.group(1)}.stage.json"):
                    if p.is_file():
                        return self.send_file(p, "application/json")
            self.send_json({"ok": False, "error": "not found"}, 404)

        def index(self, bank):
            rows = "".join(
                f"<li><a href='/p/{s}.html#companion'>{html.escape(j['title'])}</a> <small>{len(j['songs'])} songs</small></li>"
                for s, j in sorted(bank.journeys.items()) if (paths.dist() / f"{s}.html").is_file())
            body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                    f"<title>Liner Notes</title><style>body{{font:17px/1.5 Georgia,serif;margin:32px 16px;background:#111;color:#eee}}"
                    f"a{{color:#f0c27b}}small{{color:#999}}li{{margin:10px 0}}</style><h1>Liner Notes</h1><ul>{rows or '<li>No built journeys yet.</li>'}</ul>").encode()
            self.head(200, "text/html; charset=utf-8", {"Content-Length": str(len(body))})
            self.wfile.write(body)

        def events(self):
            self.head(200, "text/event-stream", {"Connection": "keep-alive"})
            v = now.version
            try:
                self.wfile.write(f"data: {json.dumps({'version': v})}\n\n".encode())
                self.wfile.flush()
                while True:
                    nv = now.wait(v)
                    self.wfile.write((f"data: {json.dumps({'version': nv})}\n\n" if nv != v else ": ping\n\n").encode())
                    self.wfile.flush()
                    v = nv
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            self.close_connection = True

        def bands(self):
            self.head(200, "text/event-stream", {"Connection": "keep-alive"})
            try:
                proc = subprocess.Popen(["cliamp", "visstream", "--fps", "24"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            except OSError:
                self.close_connection = True
                return
            try:
                for line in proc.stdout:
                    self.wfile.write(f"data: {line.strip()}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                proc.kill()
                self.close_connection = True

        def lyrics(self, matcher):
            s = cliamp.state() or {}
            t = s.get("track") or {}
            k = t.get("path")
            if not k:
                return self.send_json({"ok": False})
            out = {"yt": youtube_id(k), "song": matcher.match(t)}
            if k not in lyrics:
                res = cliamp.result("lyrics", timeout=20)
                if not res or not res.get("ok"):
                    return self.send_json({"ok": False, **out})
                lyrics[k] = [{"start": ln.get("start") or 0, "text": ln.get("text") or ""} for ln in res.get("lyrics") or []]
            return self.send_json({"ok": True, **out, "lines": lyrics[k]})

        def do_POST(self):
            if not self.gate(post=True):
                return
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
            except (json.JSONDecodeError, ValueError):
                return self.send_json({"ok": False, "error": "bad json"}, 400)
            if self.path == "/cmd" and body.get("op") in ("toggle", "next", "prev"):
                job = cliamp.call(body["op"])
                return self.send_json({"ok": bool(job) and job.get("state") == "succeeded"})
            if self.path == "/play" and (re.fullmatch(r"[\w-]{11}", str(body.get("yt") or "")) or re.fullmatch(r"[a-z0-9-]+", str(body.get("song") or ""))):
                _, matcher = lib.get()
                tracks = cliamp.queue()
                yt, song = body.get("yt"), body.get("song")
                idx = next((i for i, t in enumerate(tracks) if yt and youtube_id(t.get("path")) == yt), None)
                if idx is None and song:
                    idx = next((i for i, t in enumerate(tracks) if matcher.match(t) == song), None)
                if idx is None:
                    return self.send_json({"ok": False, "error": "not in the loaded playlist"})
                cliamp.call("queue.play", {"index": idx})
                return self.send_json({"ok": True, "index": idx})
            self.send_json({"ok": False, "error": "unknown"}, 400)

    return Handler


def theme():
    colors = {}
    try:
        for line in (THEME / "theme/colors.toml").read_text().splitlines():
            m = re.fullmatch(r'\s*(\w+)\s*=\s*"([^"]*)"\s*', line)
            if m:
                colors[m.group(1)] = m.group(2)
        name = (THEME / "theme.name").read_text().strip()
    except OSError:
        return {"ok": False}
    mode = colors.pop("mode", None) or ("light" if (THEME / "theme/light.mode").exists() else "dark")
    font = ""
    if Path("/usr/share/omarchy/bin/omarchy-font-current").exists():
        font = subprocess.run(["omarchy-font-current"], capture_output=True, text=True, timeout=3).stdout.strip()
    return {"ok": True, "name": name, "mode": mode, "colors": colors, "font": font}


def lan_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def make_server(port=PORT, lan=False, token=None, roots=None):
    lib = Library(roots)
    now = Now(lib)
    token = token or (secrets.token_urlsafe(18) if lan else None)
    srv = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), make_handler(lib, now, port, lan, token))
    srv.daemon_threads = True
    return srv, token


def serve(port=PORT, lan=False, token=None):
    try:
        srv, token = make_server(port, lan, token)
    except OSError as e:
        sys.exit(f"liner-notes: {e}")
    print(f"Liner Notes is serving http://127.0.0.1:{port}/")
    if lan:
        url = f"http://{lan_address()}:{port}/?t={token}"
        print(f"On your network: {url}\nAnyone with this address can see what you play and skip tracks. Stop the server to revoke it.")
        try:
            print(subprocess.run(["qrencode", "-t", "ansiutf8", url], capture_output=True, text=True, timeout=5).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
