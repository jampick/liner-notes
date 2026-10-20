"""cliamp's visualizer bands for every /bands client from one `cliamp visstream` process.

The reader starts with the first subscriber and stops (kill, then wait, so no zombie is left)
when the last one leaves. If visstream exits while clients are still listening, it is started
again after a second. Each subscriber gets a small queue; a slow one loses old frames rather
than holding up the others.
"""

import logging
import queue
import select
import subprocess
import threading
import time

from .. import cliamp

log = logging.getLogger("liner_notes.bridge")


class Bands:
    def __init__(self, cmd=None, retry=1.0):
        self.cmd = cmd
        self.retry = retry
        self.lock = threading.Lock()
        self.subs = set()
        self.proc = None
        self.thread = None
        self.spawned = 0  # how many visstream processes this has started, for tests and the log

    def command(self):
        return self.cmd or [cliamp.binary(), "visstream", "--fps", "24"]

    def subscribe(self):
        q = queue.Queue(maxsize=8)
        with self.lock:
            self.subs.add(q)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="liner-notes-bands", daemon=True)
                self.thread.start()
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)
            proc = self.proc if not self.subs else None
        if proc:
            self._stop(proc)

    def children(self):
        """visstream processes still running (0 or 1)."""
        with self.lock:
            return int(self.proc is not None and self.proc.poll() is None)

    def _stop(self, proc):
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def _publish(self, line):
        with self.lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(line)
                except (queue.Empty, queue.Full):
                    pass

    def _run(self):
        while True:
            with self.lock:
                if not self.subs:
                    self.thread, self.proc = None, None
                    return
                try:
                    proc = self.proc = subprocess.Popen(
                        self.command(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
                    )
                    self.spawned += 1
                except OSError as e:
                    log.warning("bands: can't start visstream: %s", e)
                    proc = None
            if proc:
                self._pump(proc)
                self._stop(proc)
                proc.stdout.close()
                with self.lock:
                    if self.proc is proc:
                        self.proc = None
            with self.lock:
                if not self.subs:
                    continue
            time.sleep(self.retry)

    def _pump(self, proc):
        """Forward lines until visstream exits or nobody is listening. select() keeps a silent stream
        from pinning this thread: the subscriber check runs at least every half second."""
        buf = b""
        fd = proc.stdout.fileno()
        while True:
            with self.lock:
                if not self.subs:
                    return
            r, _, _ = select.select([fd], [], [], 0.5)
            if not r:
                continue
            try:
                chunk = proc.stdout.read1(65536)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                if ln.strip():
                    self._publish(ln.strip().decode(errors="replace"))
            if len(buf) > 1 << 16:
                buf = b""
