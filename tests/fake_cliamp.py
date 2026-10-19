"""A stand-in for cliamp's V2 IPC socket, so the bridge can be tested without touching real playback."""
import json
import os
import socket
import threading
import uuid


class FakeCliamp:
    def __init__(self, path, tracks, playing=0):
        self.path, self.tracks, self.index = str(path), tracks, playing
        self.state, self.ops, self.jobs, self.subs = "playing", [], {}, []
        self.history, self.lyrics = [], {"ok": True, "lyrics": [{"start": 1.5, "text": "hello"}]}
        self.search = {}
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if os.path.exists(self.path):
            os.unlink(self.path)
        self.sock.bind(self.path)
        self.sock.listen(16)
        threading.Thread(target=self._accept, daemon=True).start()

    def snapshot(self):
        t = dict(self.tracks[self.index], index=self.index)
        return {"revision": 1, "playlist_revision": 1, "state": self.state, "track": t, "logical_track": t,
                "position": 12.5, "duration": t.get("duration_secs", 200), "index": self.index, "total": len(self.tracks), "shuffle": False}

    def play(self, index):
        self.index = index
        ev = json.dumps({"event": "runtime.playback", "seq": 1, "data": self.snapshot()}) + "\n"
        for f in list(self.subs):
            try:
                f.write(ev.encode())
                f.flush()
            except OSError:
                self.subs.remove(f)

    def _accept(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        f = c.makefile("rwb")
        for line in f:
            msg = json.loads(line)
            assert msg.get("version") == 2
            m = msg["method"]
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
                result = self._op(op, params)
                jid = uuid.uuid4().hex
                self.jobs[jid] = {"id": jid, "operation": op, "state": "succeeded" if result is not None else "failed", "result": result}
                r = {"ok": True, "job": {"id": jid, "operation": op, "state": "queued"}}
            elif m == "job.get":
                r = {"ok": True, "job": self.jobs[msg["job_id"]]}
            else:
                r = {"ok": False, "error": {"code": "unknown_method"}}
            f.write((json.dumps({"version": 2, "id": msg["id"], **r}) + "\n").encode())
            f.flush()

    def _op(self, op, params):
        if op == "queue.list":
            return {"ok": True, "index": self.index, "total": len(self.tracks), "tracks": self.tracks}
        if op == "queue.play":
            self.play(params["index"])
            return {"ok": True}
        if op in ("toggle", "next", "prev"):
            return {"ok": True}
        if op == "history":
            return {"ok": True, "history": self.history}
        if op == "lyrics":
            return self.lyrics
        if op == "provider.search":
            return {"ok": True, "tracks": self.search.get(params.get("query"), [])}
        if op == "provider.tracks":
            return {"ok": True, "tracks": self.tracks}
        return None

    def close(self):
        self.sock.close()
