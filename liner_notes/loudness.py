"""Every song at the same level from its first beat.

A reactive leveler (Easy Effects Autogain) only learns a song's loudness by listening to it. It
starts each song with the last song's gain, or none after the silence between tracks, and chases
quiet intros, so a loud master opens up to 10 dB too hot for a few seconds. This measures each
song once instead (EBU R128 integrated loudness and true peak, like ReplayGain) and sets the gain
on cliamp's stream when the title changes, while the next stream is still buffering.

    state()/loudness.json   {"<youtube id or path>": {"lufs", "peak", "from": "file" | "heard", "at"}}

Where a measurement comes from:
  - `liner-notes loudness scan` reads the audio files in cache()/audio (named <youtube id>.<ext>)
    with ffmpeg, one at a time;
  - a song that isn't in the table yet is heard while it plays: the bridge records what cliamp
    sends to the sound server and keeps the reading when the whole song played through once. That
    reading includes cliamp's own volume and EQ, so leave those flat (they are on this machine).

A song with a reading gets target - lufs (never louder than the peak allows, never more than
MAX_BOOST up) on cliamp's PipeWire stream, with Easy Effects bypassed. A song without one plays at
0 dB with Easy Effects on, so Autogain covers it the first time. Only the bridge's leveler
changes anything, and only when [loudness] enabled is true in config.toml (the default).
`liner-notes loudness restore` puts back 0 dB and Autogain; the service runs it after every stop.
"""

import datetime as dt
import json
import logging
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import paths
from .ids import youtube_id

log = logging.getLogger("liner_notes.loudness")

TARGET = -18.0  # LUFS, the level Autogain was set to
MAX_BOOST = 6.0  # dB; a very quiet master stays a little quiet rather than hiss
CEILING = -1.0  # dBTP: the boost stops where the loudest peak would reach this
HEARD_SHARE = 0.9  # a song heard live counts once this much of it played without a jump
JUMP = 3.0  # seconds off the expected position that count as a seek
SETTLE = 8.0  # seconds after a change to wait for the new song's own position
AUDIO_EXT = (".webm", ".m4a", ".opus", ".mp3", ".ogg", ".flac", ".wav")
SUMMARY_I = re.compile(r"\bI:\s+(-?[\d.]+|-inf) LUFS")
SUMMARY_PEAK = re.compile(r"\bPeak:\s+(-?[\d.]+|-inf) dBFS")


def key_for(track):
    """The table key for a track: its YouTube id (online or an offline copy), else its path."""
    path = (track or {}).get("path") or ""
    return youtube_id(path) or path or None


def gain_for(entry, target=TARGET):
    """dB to apply for a reading, or None when there's no usable one."""
    try:
        lufs = float(entry["lufs"])
        peak = float(entry["peak"]) if entry.get("peak") is not None else math.inf  # unknown: no boost
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(lufs):
        return None
    g = min(target - lufs, MAX_BOOST)
    if g > 0:
        g = min(g, max(0.0, CEILING - peak))
    return round(g, 1)


def parse_summary(text):
    """(lufs, peak) from the Summary block ffmpeg's ebur128 prints at the end, or None."""
    _, sep, summary = (text or "").rpartition("Summary:")
    i, p = SUMMARY_I.search(summary), SUMMARY_PEAK.search(summary)
    if not sep or not i:
        return None
    lufs, peak = (float(m.group(1)) if m and m.group(1) != "-inf" else -math.inf for m in (i, p))
    return lufs, peak


def ffmpeg():
    return shutil.which("ffmpeg")


def measure_file(path, timeout=300):
    """(lufs, peak) for an audio file, or None."""
    exe = ffmpeg()
    if not exe:
        return None
    cmd = ["nice", "-n", "19", exe, "-nostats", "-hide_banner", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_summary(r.stderr)


class Table:
    """state()/loudness.json, read once and written whole on every new reading."""

    def __init__(self, path=None):
        self.path = Path(path or paths.state() / "loudness.json")
        self.lock = threading.Lock()
        try:
            data = json.loads(self.path.read_text())
            self.rows = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.rows = {}

    def get(self, key):
        with self.lock:
            return self.rows.get(key) if key else None

    def put(self, key, lufs, peak, source):
        row = {"lufs": round(lufs, 1), "peak": round(peak, 1) if math.isfinite(peak) else None, "from": source}
        row["at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with self.lock:
            self.rows[key] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.rows, indent=1, sort_keys=True) + "\n")
            tmp.replace(self.path)
        return row


def scan(audio_dir=None, table=None, out=None, measure=measure_file):
    """Measure every cached audio file the table doesn't have yet. Returns (measured, failed)."""
    table = table or Table()
    d = Path(audio_dir or paths.cache() / "audio")
    files = sorted(p for p in d.glob("*") if p.suffix.lower() in AUDIO_EXT) if d.is_dir() else []
    done = failed = 0
    for p in files:
        if table.get(p.stem) and table.get(p.stem).get("from") == "file":
            continue
        got = measure(p)
        if not got or not math.isfinite(got[0]):
            failed += 1
            print(f"  {p.name}: no reading", file=out)
            continue
        table.put(p.stem, got[0], got[1], "file")
        done += 1
        print(f"  {p.stem}  {got[0]:6.1f} LUFS  peak {got[1]:5.1f}  gain {gain_for({'lufs': got[0], 'peak': got[1]}):+5.1f} dB", file=out)
    return done, failed


# --- the sound server ----------------------------------------------------------------------------


class Sound:
    """cliamp's stream in PipeWire (through pactl) and Easy Effects' global bypass."""

    APP = "cliamp"

    def __init__(self, run=subprocess.run):
        self.run = run

    def _run(self, *cmd):
        """(ok, stdout); ok is False when the command failed or couldn't start."""
        try:
            r = self.run(list(cmd), capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return False, ""
        return r.returncode == 0, r.stdout or ""

    def stream(self):
        """cliamp's sink-input index, or None."""
        if not shutil.which("pactl"):
            return None
        try:
            for s in json.loads(self._run("pactl", "-f", "json", "list", "sink-inputs")[1] or "[]"):
                if self.APP in ((s.get("properties") or {}).get("application.name") or ""):
                    return s["index"]
        except (ValueError, KeyError, TypeError):
            pass
        return None

    @staticmethod
    def volume(db):
        """pactl's absolute volume for a gain in dB. A value with a sign (+3dB, -3dB) would be a step
        from the current volume, so this is the raw number: PulseAudio's scale is cubic, 65536 = 0 dB."""
        return str(round(65536 * 10 ** (db / 60)))

    def set_gain(self, index, db):
        """True when pactl took it (False also when the index is gone, e.g. cliamp restarted)."""
        return self._run("pactl", "set-sink-input-volume", str(index), self.volume(db))[0]

    def bypass(self, on):
        if shutil.which("easyeffects"):
            self._run("easyeffects", "-b", "1" if on else "2")

    def recorder(self, index):
        """A process writing cliamp's stream alone (sink input `index`) as raw s16le 48 kHz stereo, or None."""
        exe = shutil.which("parec")
        if not exe:
            return None
        cmd = [exe, f"--monitor-stream={index}", "--raw", "--format=s16le", "--rate=48000", "--channels=2", "--latency-msec=200"]
        try:
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return None


METER = ["-nostats", "-hide_banner", "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "-"]
METER += ["-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"]


class Listener:
    """Measures one play as it happens: parec into ffmpeg's ebur128. stop() ends the recording at
    once; result() then waits for ffmpeg's summary."""

    def __init__(self, sound, index):
        self.rec, self.meter, self.text = sound.recorder(index), None, ""
        exe = ffmpeg()
        if not self.rec or not exe:
            self.stop()
            return
        try:
            self.meter = subprocess.Popen(
                ["nice", "-n", "19", exe, *METER], stdin=self.rec.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
            )
        except OSError:
            self.stop()
            return
        self.rec.stdout.close()  # ffmpeg owns the pipe now
        self.reader = threading.Thread(target=self._read, name="liner-notes-loudness", daemon=True)
        self.reader.start()

    def _read(self):
        self.text = self.meter.stderr.read()

    @property
    def alive(self):
        return self.meter is not None

    def stop(self):
        if self.rec and self.rec.poll() is None:
            self.rec.terminate()
            try:
                self.rec.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.rec.kill()
                self.rec.wait()

    def result(self):
        """(lufs, peak) once ffmpeg has finished, or None."""
        if not self.alive:
            return None
        self.stop()
        try:
            self.meter.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.meter.kill()
            self.meter.wait()
            return None
        self.reader.join(timeout=1)
        return parse_summary(self.text)


class Play:
    """What the leveler knows about the play under way: which song, and how much of it was heard.

    Coverage is judged by position, not wall time, so a pause doesn't count as listening: the
    span from the lowest to the highest position seen must cover HEARD_SHARE of the song, with no
    jump in between (a paused sample should stay put, a playing one move with the clock)."""

    def __init__(self, key, source, duration, listener=None):
        self.key, self.source, self.duration, self.listener = key, source, duration, listener
        self.low = self.high = None
        self.last = None  # (position, position_at, state)
        self.clean, self.started, self.settle_until = True, False, time.monotonic() + SETTLE

    def see(self, np):
        if not self.started:  # cliamp shows the last song's position until the new stream starts
            if np.position > 3 and time.monotonic() < self.settle_until:
                return
            self.started = True
        if self.last:
            pos, at, state = self.last
            expected = pos + (np.position_at - at if state == "playing" else 0.0)
            if abs(np.position - expected) > JUMP:
                self.clean = False
        self.last = (np.position, np.position_at, np.state)
        self.low = np.position if self.low is None else min(self.low, np.position)
        self.high = np.position if self.high is None else max(self.high, np.position)

    def heard(self):
        return 0.0 if self.low is None else self.high - self.low

    def counts(self):
        return self.clean and bool(self.duration) and self.heard() >= HEARD_SHARE * self.duration


class Leveler:
    """Sets cliamp's gain at every song change, and hears the songs it has no reading for.

    observe(np, changed) is called by the bridge with every NowPlaying, changed=True when the track
    is a new one; the work happens on its own thread so the bridge never waits on pactl. The same
    song coming back from a blip in the player (None and back) isn't a new play; the same song
    starting over (repeat) is."""

    def __init__(self, table=None, sound=None, target=TARGET, poll=None, poll_every=2.0):
        """poll() returns the player's NowPlaying; while a song is being heard it's asked every
        poll_every seconds, since cliamp only sends events on changes."""
        self.table, self.sound, self.target = table or Table(), sound or Sound(), target
        self.poll, self.poll_every = poll, poll_every
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self.pending = None
        self.play = None  # the Play under way
        self.index = None  # cliamp's sink-input, looked up again when pactl refuses it
        self.restore()  # whatever a crashed bridge left behind
        self.thread = threading.Thread(target=self._loop, name="liner-notes-leveler", daemon=True)
        self.thread.start()

    def observe(self, np, changed):
        if not np:
            return
        key = key_for(np.as_track())
        with self.lock:
            play = self.play
            same = play is not None and (play.key, play.source) == (key, np.source)
            restart = same and play.last is not None and np.position < 3 and play.last[0] > np.position + 10
            if (changed and not same) or restart or (play is None and self.pending is None):
                self.pending = np
                self.wake.notify()
                return
            if same and play.listener:
                play.see(np)

    def close(self, wait=3.0):
        with self.lock:
            self.pending = "close"
            self.wake.notify()
        self.thread.join(wait)

    def _loop(self):
        while True:
            with self.lock:
                if not self.wake.wait_for(lambda: self.pending is not None, self.poll_every):
                    listening = self.play is not None and self.play.listener is not None
                    np = None
                else:
                    np, self.pending = self.pending, None
                    old, self.play = self.play, None
            if np is None:
                if listening and self.poll:
                    try:
                        self.observe(self.poll(), False)
                    except Exception:
                        log.debug("poll failed", exc_info=True)
                continue
            try:
                if old and old.listener:
                    old.listener.stop()
                if np == "close":
                    self._keep(old)
                    return
                self._apply(np)  # first, so the gain is in before the new song's first beat
                self._keep(old)
            except Exception:
                log.exception("leveling failed")

    def _keep(self, play):
        """Store a live reading when the play was heard through."""
        if not play or not play.listener:
            return
        got = play.listener.result()
        if not got or not math.isfinite(got[0]) or not play.counts():
            log.info(
                "loudness of %s not kept (heard %.0f of %ss%s)", play.key, play.heard(), play.duration, "" if play.clean else ", jumped"
            )
            return
        row = self.table.put(play.key, got[0], got[1], "heard")
        log.info("loudness of %s heard: %s LUFS, peak %s", play.key, row["lufs"], row["peak"])

    def _gain(self, db):
        """Set cliamp's gain; looks for its stream again when the cached one is gone. False: no stream."""
        if self.index is not None and self.sound.set_gain(self.index, db):
            return True
        self.index = self.sound.stream()
        return self.index is not None and self.sound.set_gain(self.index, db)

    def _apply(self, np):
        key, listener = key_for(np.as_track()), None
        try:
            if np.state == "stopped" or np.source != "cliamp" or not key:  # the gain is on cliamp's stream
                return
            gain = gain_for(self.table.get(key) or {}, self.target)
            if not self._gain(0.0 if gain is None else gain):
                return
            self.sound.bypass(gain is not None)
            if gain is not None:
                log.info("%s: %+.1f dB", key, gain)
                return
            listener = Listener(self.sound, self.index)
            listener = listener if listener.alive else None
            log.info("%s: no reading yet, Autogain covers it%s", key, " while it's measured" if listener else "")
        finally:
            with self.lock:  # remembered even when nothing was set, so the next update isn't a new play
                self.play = Play(key, np.source, np.duration, listener)

    def restore(self):
        """Back to 0 dB with Easy Effects on, as if the leveler had never run."""
        self._gain(0.0)
        self.sound.bypass(False)


def restore(sound=None):
    """0 dB on cliamp's stream and Easy Effects on: `liner-notes loudness restore`, after the service stops."""
    sound = sound or Sound()
    index = sound.stream()
    if index is not None:
        sound.set_gain(index, 0.0)
    sound.bypass(False)
