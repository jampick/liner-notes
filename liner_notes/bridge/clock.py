"""The player's position as the speakers play it, smooth enough to sync a muted video to.

cliamp reports where its decoder is, in 125 ms steps, and that runs ahead of the speakers by
cliamp's and PipeWire's buffers (about 0.4 s on the owner's machine; route.Latency has the
measured value per output). While a page is polling, one sampler thread reads the player's state
(over cliamp's IPC socket, a fraction of a millisecond, no process) a few times a second and fits
a line through the last few seconds of samples, which smooths out the steps. A jump, pause or song
change starts a new fit and bumps `epoch`, so the page knows to seek instead of easing back into
sync. The latency is taken off every position.

At a song change cliamp shows the new title at once but keeps reporting the old song's position
for a second or so while the stream buffers, then drops to 0 and counts up. Until it does, and
while the position stands still or the sound hasn't reached the speakers yet, `moving` is False
and the position is held, so the page keeps its stills up instead of starting the video early.

The sampler stops after `idle` seconds without a reader and starts again on the next one.
"""

import threading
import time

FIT_WINDOW = 6.0  # seconds of samples in the fit
STILL = 0.5  # no new position for this long means the player isn't moving
START_GIVEUP = 5.0  # a song change that never shows a fresh position stops holding after this


class Clock:
    def __init__(self, sample, latency=lambda: 0.0, interval=0.2, idle=15.0, now=time.monotonic):
        """sample() returns a NowPlaying (or None); latency() returns seconds to take off."""
        self.sample, self.latency_fn, self.interval, self.idle, self.mono = sample, latency, interval, idle, now
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.wake = threading.Event()
        self.thread, self.wanted = None, 0.0
        self.np, self.track, self.raw, self.at, self.samples = None, None, 0.0, 0.0, []
        self.epoch, self.starting, self.changed_at, self.latency, self.moving = 0, None, None, 0.0, False
        self.count = 0  # samples since the sampler started

    # --- the sampler ---------------------------------------------------------------------------
    def want(self):
        """Note a reader and make sure exactly one sampler is running."""
        with self.lock:
            self.wanted = self.mono()
            if self.thread is None:
                self.count = 0
                self.thread = threading.Thread(target=self._run, name="liner-notes-clock", daemon=True)
                self.thread.start()

    def close(self):
        """Stop the sampler for good."""
        with self.lock:
            self.idle = -1.0
        self.wake.set()

    def poke(self):
        """Sample now instead of at the next tick (an event said something changed)."""
        self.wake.set()

    def _run(self):
        while True:
            with self.lock:
                if self.mono() - self.wanted >= self.idle:
                    self.thread = None  # decided under the lock, so a want() after this starts a new one
                    self.np, self.track, self.samples, self.starting = None, None, [], None
                    return
            t0 = self.mono()
            try:
                np = self.sample()
            except Exception:
                np = None
            t = (t0 + self.mono()) / 2
            try:
                lat = float(self.latency_fn() or 0.0)
            except Exception:
                lat = 0.0
            self.feed(np, t, lat)
            self.wake.wait(max(0.0, self.interval - (self.mono() - t0)))
            self.wake.clear()

    # --- the model -----------------------------------------------------------------------------
    def fit(self, t):
        pts = [(a, p) for a, p in self.samples if t - a < FIT_WINDOW]
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < 0.5:
            return None
        n = len(pts)
        mt, mp = sum(a for a, _ in pts) / n, sum(p for _, p in pts) / n
        var = sum((a - mt) ** 2 for a, _ in pts)
        rate = min(1.02, max(0.98, sum((a - mt) * (p - mp) for a, p in pts) / var)) if var else 1.0
        return mp + rate * (t - mt)

    def feed(self, np, t, latency=0.0):
        """Take one sample taken at monotonic time t."""
        with self.lock:
            self.latency = latency
            self.count += 1
            if np is None:
                self.np, self.samples, self.starting, self.track = None, [], None, None
                self.cond.notify_all()
                return
            pos = float(np.position_now() or 0.0)
            playing = np.state == "playing"
            track = np.track_key()
            new_track = self.track is not None and track != self.track
            same = self.np is not None and not new_track and self.np.state == np.state
            old_raw = self.raw if self.np is not None and not new_track else None
            guess = self.fit(t) if same and playing else None
            if new_track:
                self.starting = t
            if self.starting is not None and ((playing and pos < 3 and pos != old_raw) or t - self.starting > START_GIVEUP):
                self.starting, self.samples, self.epoch = None, [], self.epoch + 1
            if (
                not same
                or (guess is not None and abs(pos - guess) > 0.3)
                or (not playing and old_raw is not None and abs(pos - old_raw) > 0.3)
            ):
                self.samples, self.epoch = [], self.epoch + 1
            if old_raw is not None and pos != old_raw:
                self.changed_at = t
            elif old_raw is None:
                self.changed_at = None
            moving = playing and self.starting is None and self.changed_at is not None and t - self.changed_at < STILL and pos > latency
            if not moving:
                self.samples = []  # a stall would bend the fit; start again once the position moves
            else:
                self.samples = [x for x in self.samples if t - x[0] < FIT_WINDOW] + [(t, pos)]
            self.np, self.raw, self.at, self.track, self.moving = np, pos, t, track, moving
            self.cond.notify_all()

    def read(self, t=None):
        """{np, position, epoch, latency, moving} for now, or None with no sample yet."""
        t = self.mono() if t is None else t
        with self.lock:
            if self.np is None:
                return None
            out = {"np": self.np, "epoch": self.epoch, "latency": self.latency, "moving": False}
            if self.starting is not None:
                return {**out, "position": 0.0}
            pos = self.raw
            if self.np.state == "playing" and self.moving:
                f = self.fit(t)
                pos = f if f is not None else self.raw + (t - self.at)
            return {**out, "position": max(0.0, pos - self.latency), "moving": self.moving}

    def now(self, wait=1.0):
        """read() after making sure the sampler runs; waits up to `wait` for the first sample."""
        self.want()
        with self.lock:
            if self.count == 0:
                self.cond.wait_for(lambda: self.count > 0, wait)
        return self.read()
