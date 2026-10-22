#!/usr/bin/env python3
"""A stand-in for the bridge, for headless page checks that never touch cliamp or real playback.

    python3 tools/fake_bridge.py fixtures <dir>              build test pages and media into <dir>
    python3 tools/fake_bridge.py serve <dir> --port 47411    serve them the way the bridge does

It speaks the page <-> bridge contract with the bridge's own pieces (liner_notes.bridge.contract: the route
patterns, parse_range, the CSP and the token refusal), so the two can't drift apart quietly: /now, /lyrics
(refused for a song that isn't playing), /bands, /history, /theme, /p/<slug>.html (token written in, CSP,
ETag and 304), /p/<slug>.stage.json, /v/ and /v/<id>.mp4 with Range, /d/ and /m/ pictures, and POST /cmd and
/play behind X-Liner-Token. What the player says comes from <dir>/state.json, which a test rewrites whenever it
wants the player to change (song, pause, seek, ok:false, an off-journey song). The file is read again on every
request, so a test can kill this process and start it again on the same port and the story carries on: that's
how bridge death is played. The token stays the same across starts, as the real bridge's does.

    {"now": {"ok": true, "state": "playing", "title": ..., "artist": ..., "yt": ..., "song": ..., "journey": ...,
             "playlist": ..., "duration": 240, "pos0": 12.0, "t0": <unix time pos0 was true>, "epoch": 3, "moving": true},
     "lyrics": {"<yt>" or "song:<id>": [{"t": 1.5, "text": "..."}]},  "lyrics_delay": 0,
     "history": [{"yt": ..., "song": ..., "at": "<iso>", "secs": 240}],  "theme": {...} or null,
     "videos": ["<id>", ...] (default: every file in <dir>/video),  "video_broken": ["<id>"] (listed, but 404),
     "bands": true, "bands_close_after": 0,  "refuse_token": false (every POST gets the token refusal)}

Every request goes to <dir>/requests.log and every POST to <dir>/posts.log, one JSON object a line. The fixtures
are made up (synthetic lyrics, pictures and video) apart from the journey text that ships in this repo.
"""

import argparse
import hashlib
import hmac
import json
import re
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
sys.path.insert(0, str(ROOT))

from liner_notes.bridge import contract as C  # noqa: E402  (the shared contract: routes, Range, CSP, token, answers)

VERSION = "fake"
TOKEN = "fake-bridge-page-token-0001"
CAN = ["bands", "history", "jump", "lyrics", "next", "prev", "toggle"]


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
        return C.not_playing(n.get("reason", "stopped"), VERSION, "cliamp", CAN)
    t = time.time() if t is None else t
    playing, moving = n.get("state") == "playing", n.get("moving", True)
    pos = float(n.get("pos0") or 0)
    if playing and moving:
        pos += max(0.0, t - float(n.get("t0") or t))
    if n.get("duration"):
        pos = min(pos, float(n["duration"]))
    vals = {
        "ok": True,
        "version": VERSION,
        "source": n.get("source", "cliamp"),
        "state": n.get("state", "playing"),
        "position": round(pos, 3),
        "epoch": n.get("epoch", 0),
        "moving": bool(playing and moving),
        "latency": n.get("latency", 0.0),
        "can": CAN,
    }
    return {k: vals[k] if k in vals else n.get(k) for k in C.NOW_KEYS}


def lyrics_payload(st, q):
    """Only for the song playing (never another song's lines), each line with both t and start."""
    n = st.get("now") or {}
    if not n.get("ok"):
        return {"ok": False, "lines": [], "source": None, "error": "nothing playing"}
    yt, song = n.get("yt"), n.get("song")
    want_yt, want_song = q.get("yt", ""), q.get("song", "")
    if (want_yt or want_song) and not ((want_yt and want_yt == yt) or (want_song and want_song == song)):
        return C.lyrics_not_playing(yt, song)
    lyr = st.get("lyrics") or {}
    lines = lyr.get(yt or "") or lyr.get("song:" + (song or "")) or []
    return C.lyrics_answer([C.lyric_line(ln["t"], ln["text"]) for ln in lines], "retimed", yt, song)


def song_payload(now):
    """GET /song: /now plus the bank's notes for the matched song; the fake has no bank, so the notes are empty."""
    if not now.get("ok"):
        return now
    out = {k: now.get(k) for k in ("ok", "source", "state", "title", "artist", "album", "song")}
    out.update(year=None, journey=None, era=None, notes=None, review=None, draft=False)
    return out


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

        def route(self):
            return self.path.split("?", 1)[0]

        def host_ok(self):
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def cors_origin(self):
            o = self.headers.get("Origin")
            if not o:
                return None
            if o in origins:
                return o
            if o == "null" and self.route() in C.NULL_CORS and self.command in ("GET", "HEAD"):
                return o
            return None

        def head(self, code, ctype="application/json", extra=None, cache=C.CACHE["api"], csp=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", csp or C.CSP)
                self.send_header("Referrer-Policy", "no-referrer")
            o = self.cors_origin()
            if o:
                self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Vary", "Origin")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.head(code, extra={"Content-Length": str(len(body))})
            if self.command != "HEAD":
                self.wfile.write(body)

        def send_body(self, body, ctype, cache, csp=None):
            etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
            if self.headers.get("If-None-Match") == etag:
                return self.head(304, ctype, {"ETag": etag}, cache, csp)
            self.head(200, ctype, {"ETag": etag, "Content-Length": str(len(body))}, cache, csp)
            if self.command != "HEAD":
                self.wfile.write(body)

        def gate(self, post=False):
            if not self.host_ok():
                self.send_json({"ok": False, "error": "host"}, 403)
                return False
            o = self.headers.get("Origin")
            if o is not None and o not in origins and (post or o != "null"):
                self.send_json({"ok": False, "error": "origin"}, 403)
                return False
            return True

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path, _, qs = self.path.partition("?")
            log("requests.log", {"t": time.time(), "m": self.command, "p": path})
            if not self.gate():
                return
            st = state.get()
            q = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            if path == "/now":
                return self.send_json(now_payload(st))
            if path == "/song":
                return self.send_json(song_payload(now_payload(st)))
            if self.command == "HEAD" and path == "/bands":
                return self.send_json({"ok": False, "error": "streams answer GET only"}, 405)
            if path == "/lyrics":
                time.sleep(float(st.get("lyrics_delay") or 0))
                return self.send_json(lyrics_payload(st, q))
            if path == "/history":
                return self.send_json({"ok": True, "plays": st.get("history") or []})
            if path == "/theme":
                return self.send_json(st.get("theme") or {"ok": False})
            if path == "/bands":
                return self.bands(st)
            if path == C.VIDEO_LIST:
                have = sorted(p.stem for p in (root / "video").glob("*.mp4"))
                allowed = st.get("videos")
                return self.send_json({"ok": True, "ids": [i for i in have if allowed is None or i in allowed]})
            m = C.VIDEO.fullmatch(path)
            if m:
                f = root / "video" / f"{m.group(1)}.mp4"
                listed = st.get("videos") is None or m.group(1) in st["videos"]
                if f.is_file() and listed and m.group(1) not in (st.get("video_broken") or []):
                    return self.video(f)
            m = C.PAGE.fullmatch(path)
            if m and (root / "dist" / f"{m.group(1)}.html").is_file():
                page = C.inject_token((root / "dist" / f"{m.group(1)}.html").read_bytes(), token)
                return self.send_body(page, "text/html; charset=utf-8", C.CACHE["page"], C.csp(C.script_hashes(page)))
            m = C.STAGE.fullmatch(path)
            if m and (root / "dist" / f"{m.group(1)}.stage.json").is_file():
                return self.send_body((root / "dist" / f"{m.group(1)}.stage.json").read_bytes(), "application/json", C.CACHE["page"])
            m = C.COVER.fullmatch(path)
            if m and (root / "media/covers" / f"{m.group(2)}.webp").is_file():
                return self.send_body((root / "media/covers" / f"{m.group(2)}.webp").read_bytes(), "image/webp", C.CACHE["picture"])
            for rx, folder in ((C.PICTURE, "media"), (C.DEPTH, "depth")):
                m = rx.fullmatch(path)
                if m and (root / folder / m.group(2) / m.group(3)).is_file():
                    f = root / folder / m.group(2) / m.group(3)
                    return self.send_body(f.read_bytes(), C.TYPES[f.suffix[1:]], C.CACHE["picture"])
            self.send_json({"ok": False, "error": "not found"}, 404)

        def video(self, f):
            size = f.stat().st_size
            base = {"Accept-Ranges": "bytes"}
            r = C.parse_range(self.headers.get("Range"), size)
            if r == C.UNSATISFIABLE:
                return self.head(416, "video/mp4", {**base, "Content-Range": f"bytes */{size}", "Content-Length": "0"}, C.CACHE["video"])
            start, end = r or (0, size - 1)
            extra = {**base, "Content-Length": str(end - start + 1), **({"Content-Range": f"bytes {start}-{end}/{size}"} if r else {})}
            self.head(206 if r else 200, "video/mp4", extra, C.CACHE["video"])
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

        def read_body(self):
            cl = (self.headers.get("Content-Length") or "0").strip()
            if not re.fullmatch(r"\d{1,9}", cl) or int(cl) > C.MAX_BODY:
                return None, "bad Content-Length"
            raw = self.rfile.read(int(cl)) if int(cl) else b""
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return None, "bad json"
            return (body, None) if isinstance(body, dict) else (None, "the body must be a JSON object")

        def do_POST(self):
            path = self.route()
            log("requests.log", {"t": time.time(), "m": "POST", "p": path})
            if not self.gate(post=True):
                self.close_connection = True
                return
            body, err = self.read_body()
            st = state.get()
            ok = hmac.compare_digest(self.headers.get("X-Liner-Token", "").encode(), token.encode()) and not st.get("refuse_token")
            log("posts.log", {"t": time.time(), "path": path, "body": body, "token_ok": ok, "origin": self.headers.get("Origin")})
            if not ok:  # the bridge checks the token before it looks at the body
                return self.send_json(C.token_refusal(), 403)
            if err:
                self.close_connection = True
                return self.send_json({"ok": False, "error": err}, 400)
            if path == "/cmd" and body.get("op") in C.COMMANDS:
                return self.send_json({"ok": True})
            if path == "/play" and (C.YT_ID.fullmatch(str(body.get("yt") or "")) or C.SONG_ID.fullmatch(str(body.get("song") or ""))):
                return self.send_json({"ok": True, "index": 0})
            self.send_json({"ok": False, "error": "unknown"}, 400)

    return H


def make_server(root, port, token=TOKEN):
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

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def picture(hue):
    return png(96, 64, lambda x, y: ((x * 2 + hue) % 256, (y * 3 + hue // 2) % 256, (x + y + 90) % 256))


def depth():
    return png(48, 32, lambda x, y: (int(255 * y / 31),) * 3)


def video(path, secs):
    """A synthetic test-pattern video, for sync checks. Needs ffmpeg; the suite fails without it under CI."""
    if not shutil.which("ffmpeg"):
        return False
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=256x144:rate=10:duration={secs}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "10", "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, check=True)
    return True


def remix_data(ids):
    """Two made-up remixes in the shape the remix build writes: one sung in loops, one with a chop."""

    def song(i, rid, title, remixer):
        lines = [{"start": 10.0 + 8 * k, "text": f"{title} line {k + 1} goes like this"} for k in range(6)]
        events = [
            {"line": 0, "text": lines[0]["text"], "orig": 10.0, "times": [8.0, 12.0, 16.0, 20.0], "end": 24.0, "chop": False},
            {"line": 2, "text": "goes like", "orig": 26.0, "times": [40.0, 44.0], "end": 48.0, "chop": True},
            {"line": 4, "text": lines[4]["text"], "orig": 42.0, "times": [70.0], "end": 76.0, "chop": False},
        ]
        return {
            "id": rid,
            "match": f"{remixer} - {title} (Remix)",
            "title": title,
            "artist": "Test Artist",
            "year": 1979 + i,
            "remixer": remixer,
            "remixYear": 2020 + i,
            "volume": i + 1,
            "video": None,
            "remix": {"length": 180.0, "bpm": 122.0, "energy": [0.3 + 0.4 * ((k * 7) % 5) / 5 for k in range(180)], "cover": None},
            "original": {"length": 200.0, "bpm": 98.0, "energy": None, "lines": lines, "cover": None},
            "events": events,
            "photo": None,
            "notes": {
                "orig": [{"h": "How it was made", "p": "A made-up note about the original, long enough to read."}],
                "flip": [{"h": "About 98 to 122 BPM", "p": "The remix runs 1.24 times the original's tempo."}],
                "panel": [{"h": "The panel", "p": "A made-up verdict."}],
            },
        }

    return [song(0, ids[0], "First Song", "Party Pupils"), song(1, ids[1], "Second Song", "Somebody Else")]


def fixtures(out):
    from liner_notes import build
    from liner_notes.bank import Bank

    out = Path(out)
    for d in ("dist", "media/artists", "media/culture", "media/covers", "depth/artists", "depth/culture", "depth/eras", "video"):
        (out / d).mkdir(parents=True, exist_ok=True)
    slug = "downtempo-drift"
    empty_cache = Path(tempfile.mkdtemp())  # no covers, the way a fresh install starts
    bank = Bank()
    data, _, meta = build.compose(slug, bank, cache=empty_cache)
    (out / "dist" / f"{slug}.html").write_text(build.render(data, meta))
    # A second journey, to check the switch between journeys: the same notes under another slug.
    other = dict(data, slug="drift-b", title="Drift B", titleA="Drift", titleB="B")
    (out / "dist" / "drift-b.html").write_text(build.render(other, dict(meta, title="Drift B")))
    live, live_meta = build.live_data()
    (out / "dist" / "live.html").write_text(build.render(live, live_meta))

    stops = data["stops"]
    intro = next(s for s in stops if any(re.search(r"open|first|intro|start|from the", x["where"], re.I) for x in s["listen"]))
    artist_stop, culture_stop = stops[5], stops[6]
    stage = {
        "tracks": {s["yt"]: {"embed": True, "motion": True} for s in stops[:4] if s["yt"]},
        "years": {},
        "songs": {
            culture_stop["title"]: [
                {"kind": "film", "title": "A Made-up Film", "line": "Opened that year. This is test text.", "img": "culture/made_up.png"}
            ]
        },
        "artists": {artist_stop["artist"]: "artists/test_artist.png"},
    }
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
    tpl = (ROOT / "liner_notes/web/remix.html").read_text()
    (out / "dist" / "remix-test.html").write_text(tpl.replace("/*DATA*/null", json.dumps(remix_data(remix_ids)).replace("</", "<\\/"), 1))

    def pick(s):
        return {k: s[k] for k in ("id", "n", "yt", "title", "artist", "year", "era", "album")}

    manifest = {
        "slug": slug,
        "stops": [pick(s) for s in stops],
        "intro": pick(intro),
        "artist_stop": pick(artist_stop),
        "culture_stop": pick(culture_stop),
        "still_stops": [pick(s) for s in stops[4:8]],
        "video_stops": [pick(s) for s in stops[:4]],
        "videos": have_video,
        # The journey's threads by song id, so scenarios needn't know where the bank sits on disk.
        "threads": bank.journeys[slug].get("threads") or [],
        "remix": remix_ids,
        # A song from a playlist with no journey, with a cover in the cache.
        "offsong": {
            "song": "offsong-1",
            "yt": "offTrack001",
            "title": "A Song With No Journey",
            "artist": "Somebody Else",
            "album": "Some Album",
            "playlist": "Soul Rap Covers",
        },
        # A raw upload title with no artist, from a playlist that does have a journey (drift-b), and no cover.
        "rawsong": {
            "song": None,
            "yt": "rawTrack002",
            "title": "Biggie Smalls – Big Poppa (50s Soul Version)",
            "artist": "",
            "album": None,
            "playlist": "Drift B",
        },
    }
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
    s.add_argument("--token", default=TOKEN, help="the page token; the same on every start unless given")
    a = ap.parse_args()
    if a.cmd == "fixtures":
        fixtures(a.dir)
        return
    srv, _ = make_server(a.dir, a.port, a.token)
    print(f"fake bridge on http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
