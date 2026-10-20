"""A small client for cliamp's V2 IPC: newline-delimited JSON over its Unix socket.

    state()                    the runtime snapshot (state.get)
    Client().state()           the same over one kept-open connection, for the bridge's clock
    call(op, params)           operation.submit, then job.get until the job is final
    subscribe(topics)          a generator of event envelopes (runtime.state, runtime.playback, ...)

When the socket isn't there (cliamp not running, or an older cliamp) it falls back to the
`cliamp` CLI, which speaks the same protocol for us. Only the bridge's few control endpoints
and `liner-notes add` ever call an operation that changes anything.
"""
import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
import time

from . import paths

FINAL = ("succeeded", "failed", "canceled")
_ids = itertools.count(1)


class Unavailable(Exception):
    pass


def socket_path():
    return os.environ.get("LINER_NOTES_CLIAMP_SOCKET") or str(paths.cliamp_dir() / "cliamp.sock")


def _connect(timeout):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(socket_path())
    except OSError as e:
        s.close()
        raise Unavailable(str(e)) from e
    return s


def request(msg, timeout=5):
    """Send one V2 request on a fresh connection and return the reply."""
    msg = {"version": 2, "id": msg.get("id") or f"ln{next(_ids)}", **msg}
    s = _connect(timeout)
    try:
        f = s.makefile("rwb")
        f.write((json.dumps(msg) + "\n").encode())
        f.flush()
        line = f.readline()
        if not line:
            raise Unavailable("cliamp closed the connection")
        return json.loads(line)
    except (OSError, json.JSONDecodeError) as e:
        raise Unavailable(str(e)) from e
    finally:
        s.close()


def binary():
    """cliamp's full path, so a subprocess never depends on the caller's PATH order."""
    return os.environ.get("LINER_NOTES_CLIAMP_BIN") or shutil.which("cliamp") or "cliamp"


class Client:
    """One connection kept open for many small requests (state.get five times a second costs
    well under a millisecond this way, against about 6 ms of CPU for a `cliamp status` process).
    It reconnects on the next call after cliamp restarts. Never falls back to the CLI."""

    def __init__(self, timeout=2):
        self.timeout, self.lock, self.sock, self.f = timeout, threading.Lock(), None, None

    def _drop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = self.f = None

    def request(self, msg):
        msg = {"version": 2, "id": f"lnc{next(_ids)}", **msg}
        with self.lock:
            for attempt in (0, 1):
                try:
                    if not self.sock:
                        self.sock = _connect(self.timeout)
                        self.f = self.sock.makefile("rwb")
                    self.f.write((json.dumps(msg) + "\n").encode())
                    self.f.flush()
                    while True:
                        line = self.f.readline()
                        if not line:
                            raise Unavailable("cliamp closed the connection")
                        r = json.loads(line)
                        if r.get("id") == msg["id"]:
                            return r
                except (OSError, ValueError, Unavailable) as e:
                    self._drop()
                    if attempt or not os.path.exists(socket_path()):
                        raise Unavailable(str(e)) from e

    def state(self):
        """The runtime snapshot, or None when cliamp isn't there."""
        try:
            r = self.request({"method": "state.get"})
        except Unavailable:
            return None
        return r.get("snapshot") if r.get("ok") else None

    def close(self):
        with self.lock:
            self._drop()


def _cli(*args, timeout=10):
    try:
        r = subprocess.run([binary(), *args], capture_output=True, text=True, timeout=timeout)
        return json.loads(r.stdout) if r.stdout.strip().startswith("{") else None
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def state():
    """The runtime snapshot, or None when cliamp isn't running."""
    try:
        r = request({"method": "state.get"})
        return r.get("snapshot") if r.get("ok") else None
    except Unavailable:
        s = _cli("status", "--json")
        if not s or not s.get("ok"):
            return None
        return s  # same field names as the snapshot: state, track, position, duration, index, total, shuffle


def call(op, params=None, timeout=20):
    """Run an operation and return the final job record ({state, result, error}), or None.
    A job still running at the timeout comes back failed with code "timeout". The CLI is only
    tried when there is no socket at all, so an operation is never submitted twice."""
    if not os.path.exists(socket_path()):
        r = _cli("remote", "call", "--wait", "--params", json.dumps(params or {}), op, timeout=timeout)
        return (r or {}).get("job")
    try:
        r = request({"method": "operation.submit", "operation": op, "params": params or {}})
        if not r.get("ok"):
            return {"state": "failed", "error": r.get("error")}
        job = r["job"]
        end = time.monotonic() + timeout
        while job.get("state") not in FINAL and time.monotonic() < end:
            time.sleep(0.15)
            job = request({"method": "job.get", "job_id": job["id"]}).get("job") or job
    except Unavailable:
        return None
    if job.get("state") not in FINAL:
        return {"state": "failed", "error": {"code": "timeout"}}
    return job


def result(op, params=None, timeout=20):
    job = call(op, params, timeout)
    return (job or {}).get("result") if (job or {}).get("state") == "succeeded" else None


def subscribe(topics=("runtime.state", "runtime.playback", "runtime.playlist"), timeout=None):
    """Yield event envelopes until the stream ends. Raises Unavailable if cliamp isn't there."""
    s = _connect(10)
    try:
        f = s.makefile("rwb")
        f.write((json.dumps({"version": 2, "id": "ln-events", "method": "subscribe", "topics": list(topics)}) + "\n").encode())
        f.flush()
        ack = json.loads(f.readline() or b"{}")
        if not ack.get("ok"):
            raise Unavailable(f"subscribe refused: {ack.get('error')}")
        s.settimeout(timeout)
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield ev
            if ev.get("event") == "system.overflow":
                return
    finally:
        s.close()


def queue():
    """The live playlist as a list of tracks."""
    res = result("queue.list", {"limit": 10000})
    return (res or {}).get("tracks") or []
