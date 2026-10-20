"""A stand-in for cliamp's V2 IPC socket, so the bridge can be tested without touching real playback.

    fake = FakeCliamp(sock, tracks)              position stays at 12.5, like a snapshot
    fake = FakeCliamp(sock, tracks, clock=True)  position counts up in 125 ms steps while playing, and a
                                                 song change keeps the old position for `stale` seconds
                                                 (1.3 s, as real cliamp does while the stream buffers)

fake.lyrics_delay makes the `lyrics` operation slow; it answers for whatever is playing when it
finishes, the way cliamp does. fake.restart() removes the socket and makes a new one. Every
operation is logged in fake.ops; fake.calls counts IPC methods.
"""
import json
import os
import socket
import threading
import time
import uuid
from collections import Counter


class FakeCliamp:
    def __init__(self, path, tracks, playing=0, clock=False, stale=1.3):
        self.path, self.tracks, self.index = str(path), tracks, playing
        self.state, self.ops, self.jobs, self.subs = "playing", [], {}, []
        self.history = []
        self.lyrics = None  # None: lyrics of whatever is playing, timed; or a fixed result
        self.lyrics_delay, self.lyrics_calls = 0.0, 0
        self.search = {}
        self.shuffle = False
        self.hang = set()  # operations whose jobs never finish
        self.calls = Counter()
        self.clock, self.stale = clock, stale
        self.base, self.t0, self.stale_until, self.stale_pos = 0.0, time.monotonic(), 0.0, 0.0
        self.revision = 1
        self.lock = threading.Lock()
        self.conns = []
        self._listen()

    def _listen(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.sock.bind(self.path)
        self.sock.listen(64)
        threading.Thread(target=self._accept, args=(self.sock,), daemon=True).start()

    # --- the clock -----------------------------------------------------------------------------
    def position(self):
        if not self.clock:
            return 12.5
        now = time.monotonic()
        if now < self.stale_until:
            return self.stale_pos
        p = self.base + (now - self.t0 if self.state == "playing" else 0.0)
        return int(max(0.0, p) / 0.125) * 0.125

    def seek(self, pos):
        self.base, self.t0 = pos, time.monotonic()

    def set_state(self, state):
        self.base, self.t0 = self.position(), time.monotonic()
        self.state = state
        self._emit("runtime.state")

    def snapshot(self):
        t = dict(self.tracks[self.index], index=self.index)
        return {"revision": self.revision, "playlist_revision": 1, "state": self.state, "track": t, "logical_track": t,
                "position": self.position(), "duration": t.get("duration_secs", 200), "index": self.index,
                "total": len(self.tracks), "shuffle": self.shuffle}

    def play(self, index):
        """Change song the way cliamp does: the new title at once, the old position for a while."""
        old = self.position()
        self.index = index
        now = time.monotonic()
        if self.clock:
            self.stale_pos, self.stale_until = old, now + self.stale
            self.base, self.t0 = 0.0, now + self.stale
        self.revision += 1
        self._emit("runtime.playback")

    def _emit(self, event):
        ev = json.dumps({"event": event, "seq": self.revision, "data": self.snapshot()}) + "\n"
        for f in list(self.subs):
            try:
                f.write(ev.encode())
                f.flush()
            except (OSError, ValueError):
                self.subs.remove(f)

    # --- the socket ----------------------------------------------------------------------------
    def _accept(self, sock):
        while True:
            try:
                c, _ = sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        f = c.makefile("rwb")
        try:
            for line in f:
                msg = json.loads(line)
                assert msg.get("version") == 2
                m = msg["method"]
                with self.lock:
                    self.calls[m] += 1
                if m == "state.get":
                    r = {"ok": True, "snapshot": self.snapshot()}
                elif m == "subscribe":
                    f.write((json.dumps({"version": 2, "id": msg["id"], "ok": True}) + "\n").encode())
                    f.flush()
                    self.subs.append(f)
                    continue
                elif m == "operation.submit":
                    op, params = msg["operation"], msg.get("params") or {}
                    with self.lock:
                        self.ops.append((op, params))
                    jid = uuid.uuid4().hex
                    self.jobs[jid] = {"id": jid, "operation": op, "state": "running"}
                    threading.Thread(target=self._job, args=(jid, op, params), daemon=True).start()
                    r = {"ok": True, "job": {"id": jid, "operation": op, "state": "queued"}}
                elif m == "job.get" and msg.get("job_id") in self.jobs:
                    r = {"ok": True, "job": self.jobs[msg["job_id"]]}
                else:
                    r = {"ok": False, "error": {"code": "unknown_method"}}
                f.write((json.dumps({"version": 2, "id": msg["id"], **r}) + "\n").encode())
                f.flush()
        except (OSError, ValueError):
            pass

    def _job(self, jid, op, params):
        if op in self.hang:
            return
        result = self._op(op, params)
        self.jobs[jid] = {"id": jid, "operation": op, "state": "succeeded" if result is not None else "failed", "result": result}

    def lyrics_for(self, track):
        return {"ok": True, "lyrics": [{"start": 1.5, "text": f"lyrics of {track['title']}"}, {"start": 4.0, "text": "second line"},
                                       {"start": 7.0, "text": "third line"}]}

    def _op(self, op, params):
        if op == "queue.list":
            return {"ok": True, "index": self.index, "total": len(self.tracks), "tracks": self.tracks}
        if op == "queue.play":
            self.play(params["index"])
            return {"ok": True}
        if op in ("toggle", "next", "prev"):
            return {"ok": True}
        if op in ("shuffle", "shuffle.set"):
            self.shuffle = bool(params.get("enabled", params.get("on", not self.shuffle)))
            return {"ok": True, "shuffle": self.shuffle}
        if op == "history":
            return {"ok": True, "history": self.history}
        if op == "lyrics":
            with self.lock:
                self.lyrics_calls += 1
            time.sleep(self.lyrics_delay)
            return self.lyrics if self.lyrics is not None else self.lyrics_for(self.tracks[self.index])
        if op == "provider.search":
            return {"ok": True, "tracks": self.search.get(params.get("query"), [])}
        if op == "provider.tracks":
            return {"ok": True, "tracks": self.tracks}
        return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        self.conns, self.subs = [], []
        if os.path.exists(self.path):
            os.unlink(self.path)

    def restart(self, gap=0.0):
        """cliamp quits and comes back: the socket goes away, then a new one appears."""
        self.close()
        time.sleep(gap)
        self._listen()
