#!/usr/bin/env python3
"""A stand-in for the bridge, for headless page checks that never touch cliamp or real playback.

    python3 tools/fake_bridge.py fixtures <dir>              build test pages and media into <dir>
    python3 tools/fake_bridge.py serve <dir> --port 47411    serve them the way the bridge does

It speaks the page <-> bridge contract: /now, /lyrics, /bands, /history, /theme, /p/<slug>.html (token put in the
page, the bridge's CSP on it), /p/<slug>.stage.json, /v/ and /v/<id>.mp4 with Range, /d/ and /m/ pictures, and
POST /cmd and /play behind X-Liner-Token. What it says comes from <dir>/state.json, which a test rewrites
whenever it wants the player to change (song, pause, seek, ok:false, an off-journey song). The file is read
again on every request, so a test can kill this process and start it again on the same port and the story
carries on: that's how bridge death is played.

    {"now": {"ok": true, "state": "playing", "title": ..., "artist": ..., "yt": ..., "song": ..., "journey": ...,
             "playlist": ..., "duration": 240, "pos0": 12.0, "t0": <unix time pos0 was true>, "epoch": 3, "moving": true},
     "lyrics": {"<yt>" or "song:<id>": [{"t": 1.5, "text": "..."}]},  "lyrics_delay": 0,
     "history": [{"yt": ..., "song": ..., "at": "<iso>", "secs": 240}],  "theme": {...} or null,
     "videos": ["<id>", ...] (default: every file in <dir>/video),  "bands": true, "bands_close_after": 0}

Every request goes to <dir>/requests.log and every POST to <dir>/posts.log, one JSON object a line. The fixtures
are made up (synthetic lyrics, pictures and video) apart from the journey text that ships in this repo.
"""
import argparse
import hmac
import json
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "fake"
CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; connect-src 'self'; font-src 'self' data:; frame-src 'none'; object-src 'none'; base-uri 'none'")
TOKEN_META = '<meta name="liner-token" content="">'


class State:
    """state.json, read again whenever it changes on disk."""

    def __init__(self, path):
        self.path, self.mtime, self.data = Path(path), None, {}
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            try:
                mt = self.path.stat().st_mtime_ns
            except FileNotFoundError:
                return {}
            if mt != self.mtime:
                try:
                    self.data, self.mtime = json.loads(self.path.read_text()), mt
                except (OSError, ValueError):
                    pass  # caught mid-write; keep the last good one
            return self.data


def now_payload(st, t=None):
    n = st.get("now") or {"ok": False, "reason": "stopped"}
    if not n.get("ok"):
        return {"ok": False, "reason": n.get("reason", "stopped"), "version": VERSION, "source": "cliamp"}
    t = time.time() if t is None else t
    playing, moving = n.get("state") == "playing", n.get("moving", True)
    pos = float(n.get("pos0") or 0)
    if playing and moving:
        pos += max(0.0, t - float(n.get("t0") or t))
    if n.get("duration"):
        pos = min(pos, float(n["duration"]))
    out = {k: n.get(k) for k in ("title", "artist", "album", "yt", "song", "journey", "playlist", "duration")}
    out.update(ok=True, version=VERSION, source=n.get("source", "cliamp"), state=n.get("state", "playing"), position=round(pos, 3),
               epoch=n.get("epoch", 0), moving=bool(playing and moving), latency=n.get("latency", 0.0),
               can=["bands", "history", "lyrics", "play", "toggle", "next", "prev"])
    return out


def parse_range(header, size):
    """(start, end) for a single bytes range, None for no range, or 'bad' for one to refuse with 416."""
    if not header:
        return None
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return "bad"
    a, b = m.group(1), m.group(2)
    if a:
        start, end = int(a), min(int(b), size - 1) if b else size - 1
    else:
        n = int(b)
        if n == 0:
            return "bad"
        start, end = max(0, size - n), size - 1
    if start >= size or start > end:
        return "bad"
    return start, end


def make_handler(root, state, token, port, log_lock):
    root = Path(root)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def log(name, obj):
        with log_lock, open(root / name, "a") as f:
            f.write(json.dumps(obj) + "\n")

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def host_ok(self):
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def head(self, code, ctype="application/json", extra=None, length=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            if length is not None:
                self.send_header("Content-Length", str(length))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.head(code, length=len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_bytes(self, body, ctype, extra=None):
            self.head(200, ctype, extra, len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path, _, qs = self.path.partition("?")
            log("requests.log", {"t": time.time(), "m": self.command, "p": path})
            if not self.host_ok():
                return self.send_json({"ok": False, "error": "bad host"}, 403)
            st = state.get()
            q = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            if path == "/now":
                return self.send_json(now_payload(st))
            if path == "/lyrics":
                time.sleep(float(st.get("lyrics_delay") or 0))
                lyr = st.get("lyrics") or {}
                lines = lyr.get(q.get("yt", "")) or lyr.get("song:" + q.get("song", ""))
                return self.send_json({"ok": True, "lines": lines, "source": "retimed"} if lines else {"ok": False})
            if path == "/history":
                return self.send_json({"ok": True, "plays": st.get("history") or []})
            if path == "/theme":
                return self.send_json(st.get("theme") or {"ok": False})
            if path == "/bands":
                return self.bands(st)
            if path == "/v/":
                have = sorted(p.stem for p in (root / "video").glob("*.mp4"))
                allowed = st.get("videos")
                return self.send_json({"ok": True, "ids": [i for i in have if allowed is None or i in allowed]})
            m = re.fullmatch(r"/v/([\w-]{11})\.mp4", path)
            if m and (root / "video" / f"{m.group(1)}.mp4").is_file() and (st.get("videos") is None or m.group(1) in st["videos"]):
                return self.video(root / "video" / f"{m.group(1)}.mp4")
            m = re.fullmatch(r"/p/([a-z0-9-]+)\.html", path)
            if m and (root / "dist" / f"{m.group(1)}.html").is_file():
                page = (root / "dist" / f"{m.group(1)}.html").read_text().replace(TOKEN_META, f'<meta name="liner-token" content="{token}">', 1)
                return self.send_bytes(page.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer"})
            m = re.fullmatch(r"/p/([a-z0-9-]+)\.stage\.json", path)
            if m and (root / "dist" / f"{m.group(1)}.stage.json").is_file():
                return self.send_bytes((root / "dist" / f"{m.group(1)}.stage.json").read_bytes(), "application/json")
            m = re.fullmatch(r"/([md])/[a-z0-9-]+/([a-z]+)/([\w.-]+\.(png|webp|jpg))", path)
            if m:
                f = root / ("media" if m.group(1) == "m" else "depth") / m.group(2) / m.group(3)
                if f.is_file():
                    return self.send_bytes(f.read_bytes(), "image/" + ("jpeg" if m.group(4) == "jpg" else m.group(4)))
            self.send_json({"ok": False, "error": "not found"}, 404)

        def video(self, f):
            size = f.stat().st_size
            r = parse_range(self.headers.get("Range"), size)
            if r == "bad":
                return self.head(416, "video/mp4", {"Content-Range": f"bytes */{size}"}, 0)
            start, end = r or (0, size - 1)
            self.head(206 if r else 200, "video/mp4", {"Accept-Ranges": "bytes", **({"Content-Range": f"bytes {start}-{end}/{size}"} if r else {})}, end - start + 1)
            if self.command == "HEAD":
                return
            with open(f, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                try:
                    while left > 0:
                        chunk = fh.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def bands(self, st):
            if st.get("bands") is False:
                return self.send_json({"ok": False, "error": "no visualizer stream"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.close_connection = True
            limit, k = int(st.get("bands_close_after") or 0), 0
            try:
                while True:
                    k += 1
                    v = [round(0.5 + 0.4 * ((k * (i + 3)) % 7) / 7, 3) for i in range(10)]
                    self.wfile.write(f"data: {json.dumps({'bands': v})}\n\n".encode())
                    self.wfile.flush()
                    if limit and k >= limit:
                        return
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def do_POST(self):
            path = self.path.split("?")[0]
            log("requests.log", {"t": time.time(), "m": "POST", "p": path})
            origin = self.headers.get("Origin")
            if not self.host_ok() or (origin is not None and origin not in origins):
                return self.send_json({"ok": False, "error": "forbidden"}, 403)
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 <= n <= 10000:
                    raise ValueError
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self.send_json({"ok": False, "error": "bad request"}, 400)
            ok = hmac.compare_digest(self.headers.get("X-Liner-Token", "").encode(), token.encode())
            log("posts.log", {"t": time.time(), "path": path, "body": body, "token_ok": ok, "origin": origin})
            if not ok:
                return self.send_json({"ok": False, "reason": "token"}, 403)
            if path == "/cmd" and body.get("op") in ("toggle", "next", "prev"):
                return self.send_json({"ok": True})
            if path == "/play" and (body.get("yt") or body.get("song")):
                return self.send_json({"ok": True})
            self.send_json({"ok": False, "error": "unknown"}, 404)

    return H


def make_server(root, port, token=None):
    token = token or secrets.token_urlsafe(18)
    srv = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    srv.RequestHandlerClass = make_handler(root, State(Path(root) / "state.json"), token, srv.server_address[1], threading.Lock())
    srv.daemon_threads = True
    return srv, token


# ---------- fixtures ----------

def png(w, h, pixel):
    """A small PNG from a pixel function (x, y) -> (r, g, b), without any image library."""
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def picture(hue):
    return png(96, 64, lambda x, y: ((x * 2 + hue) % 256, (y * 3 + hue // 2) % 256, (x + y + 90) % 256))


def depth():
    return png(48, 32, lambda x, y: (int(255 * y / 31),) * 3)


def video(path, secs):
    """A synthetic test-pattern video, for sync checks. Needs ffmpeg; the video checks are skipped without it."""
    if not shutil.which("ffmpeg"):
        return False
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=256x144:rate=10:duration={secs}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "10", "-movflags", "+faststart", str(path)], check=True)
    return True


# What a page built with no journey (/p/live.html) carries: the live stage and nothing else.
LIVE_DATA = {"slug": "live", "stops": [], "eras": [], "live": True}


def remix_data(ids):
    """Two made-up remixes in the shape engine/remix_build.py writes: one sung in loops, one with a chop."""
    def song(i, rid, title, remixer):
        lines = [{"start": 10.0 + 8 * k, "text": f"{title} line {k + 1} goes like this"} for k in range(6)]
        events = [{"line": 0, "text": lines[0]["text"], "orig": 10.0, "times": [8.0, 12.0, 16.0, 20.0], "end": 24.0, "chop": False},
                  {"line": 2, "text": "goes like", "orig": 26.0, "times": [40.0, 44.0], "end": 48.0, "chop": True},
                  {"line": 4, "text": lines[4]["text"], "orig": 42.0, "times": [70.0], "end": 76.0, "chop": False}]
        return {"id": rid, "match": f"{remixer} - {title} (Remix)", "title": title, "artist": "Test Artist", "year": 1979 + i,
                "remixer": remixer, "remixYear": 2020 + i, "volume": i + 1, "video": None,
                "remix": {"length": 180.0, "bpm": 122.0, "energy": [0.3 + 0.4 * ((k * 7) % 5) / 5 for k in range(180)], "cover": None},
                "original": {"length": 200.0, "bpm": 98.0, "energy": None, "lines": lines, "cover": None},
                "events": events, "photo": None,
                "notes": {"orig": [{"h": "How it was made", "p": "A made-up note about the original, long enough to read."}],
                          "flip": [{"h": "About 98 to 122 BPM", "p": "The remix runs 1.24 times the original's tempo."}],
                          "panel": [{"h": "The panel", "p": "A made-up verdict."}]}}
    return [song(0, ids[0], "First Song", "Party Pupils"), song(1, ids[1], "Second Song", "Somebody Else")]


def fixtures(out):
    sys.path.insert(0, str(ROOT))
    from liner_notes import build
    from liner_notes.bank import Bank

    out = Path(out)
    for d in ("dist", "media/artists", "media/culture", "media/covers", "depth/artists", "depth/culture", "depth/eras", "video"):
        (out / d).mkdir(parents=True, exist_ok=True)
    slug = "downtempo-drift"
    empty_cache = Path(tempfile.mkdtemp())  # lettered covers, the way a public build draws them
    data, _, meta = build.compose(slug, Bank(), cache=empty_cache)
    (out / "dist" / f"{slug}.html").write_text(build.render(data, meta))
    # A second journey, to check the switch between journeys: the same notes under another slug.
    other = dict(data, slug="drift-b", title="Drift B", titleA="Drift", titleB="B")
    (out / "dist" / "drift-b.html").write_text(build.render(other, dict(meta, title="Drift B")))
    live = dict(LIVE_DATA, provenance=build.provenance([]))
    (out / "dist" / "live.html").write_text(build.render(live, {"title": "Liner Notes", "description": "What cliamp is playing", "fonts": ""}))

    stops = data["stops"]
    intro = next(s for s in stops if any(re.search(r"open|first|intro|start|from the", x["where"], re.I) for x in s["listen"]))
    artist_stop, culture_stop = stops[5], stops[6]
    stage = {"tracks": {s["yt"]: {"embed": True, "motion": True} for s in stops[:4] if s["yt"]},
             "years": {}, "songs": {culture_stop["title"]: [{"kind": "film", "title": "A Made-up Film", "line": "Opened that year. This is test text.", "img": "culture/made_up.png"}]},
             "artists": {artist_stop["artist"]: "artists/test_artist.png"}}
    for s in stops[4:8]:
        stage["tracks"][s["yt"]] = {"embed": True, "motion": False}
    (out / "dist" / f"{slug}.stage.json").write_text(json.dumps(stage))
    (out / "media/artists/test_artist.png").write_bytes(picture(40))
    (out / "media/culture/made_up.png").write_bytes(picture(160))
    (out / "depth/artists/test_artist.png").write_bytes(depth())
    (out / "depth/culture/made_up.png").write_bytes(depth())
    (out / "media/covers/offsong-1.webp").write_bytes(picture(220))  # PNG bytes under a .webp name; browsers sniff it
    for e in data["eras"]:
        (out / f"depth/eras/era{e['n']}.webp").write_bytes(depth())
    vids = [s["yt"] for s in stops[:4] if s["yt"]]
    have_video = bool(vids) and video(out / "video" / f"{vids[0]}.mp4", 240)
    if have_video:
        for v in vids[1:]:
            shutil.copy(out / "video" / f"{vids[0]}.mp4", out / "video" / f"{v}.mp4")

    remix_ids = ["rmxAAAAAAA1", "rmxBBBBBBB2"]
    tpl = (ROOT / "liner_notes/web/remix_template.html").read_text()
    (out / "dist" / "remix-test.html").write_text(tpl.replace("/*DATA*/null", json.dumps(remix_data(remix_ids)).replace("</", "<\\/"), 1))

    def pick(s):
        return {k: s[k] for k in ("id", "n", "yt", "title", "artist", "year", "era", "album")}

    manifest = {"slug": slug, "stops": [pick(s) for s in stops], "intro": pick(intro), "artist_stop": pick(artist_stop), "culture_stop": pick(culture_stop),
                "still_stops": [pick(s) for s in stops[4:8]], "video_stops": [pick(s) for s in stops[:4]], "videos": have_video, "remix": remix_ids,
                "offsong": {"song": "offsong-1", "yt": "offTrack001", "title": "A Song With No Journey", "artist": "Somebody Else", "album": "Some Album", "playlist": "Soul Rap Covers"}}
    (out / "fixtures.json").write_text(json.dumps(manifest, indent=1))
    shutil.rmtree(empty_cache, ignore_errors=True)
    print(f"fixtures in {out}: {len(stops)} stops, video {'yes' if have_video else 'no (no ffmpeg)'}")


def main():
    ap = argparse.ArgumentParser(description="A stand-in for the Liner Notes bridge, for headless page checks.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fixtures", help="build test pages and media into DIR")
    f.add_argument("dir")
    s = sub.add_parser("serve", help="serve DIR the way the bridge does")
    s.add_argument("dir")
    s.add_argument("--port", type=int, default=47411)
    s.add_argument("--token", help="the page token; random on each start unless given")
    a = ap.parse_args()
    if a.cmd == "fixtures":
        fixtures(a.dir)
        return
    srv, _ = make_server(a.dir, a.port, a.token)
    print(f"fake bridge on http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
