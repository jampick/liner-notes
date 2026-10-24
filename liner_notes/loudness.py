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
SETTLE = 8.0  # seconds after a change before positions are checked for jumps
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

    def _out(self, *cmd):
        try:
            return self.run(list(cmd), capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def stream(self):
        """(sink-input index, sink name) for cliamp's stream, or None."""
        if not shutil.which("pactl"):
            return None
        try:
            sinks = {s["index"]: s["name"] for s in json.loads(self._out("pactl", "-f", "json", "list", "short", "sinks") or "[]")}
            for s in json.loads(self._out("pactl", "-f", "json", "list", "sink-inputs") or "[]"):
                if self.APP in ((s.get("properties") or {}).get("application.name") or ""):
                    return s["index"], sinks.get(s.get("sink"), "")
        except (ValueError, KeyError, TypeError):
            pass
        return None

    def set_gain(self, index, db):
        self._out("pactl", "set-sink-input-volume", str(index), f"{db:+.1f}dB")

    def bypass(self, on):
        if shutil.which("easyeffects"):
            self._out("easyeffects", "-b", "1" if on else "2")

    def recorder(self, sink):
        """A process writing what goes into `sink` as raw s16le 48 kHz stereo on stdout, or None."""
        exe = shutil.which("pw-record")
        if not exe:
            return None
        cmd = [
            exe,
            "-P",
            "{ stream.capture.sink=true node.dont-reconnect=true }",
            "--target",
            sink,
            "--rate",
            "48000",
            "--channels",
            "2",
            "--format",
            "s16",
            "-",
        ]
        try:
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return None


class Listener:
    """Measures one song as it plays: pw-record into ffmpeg's ebur128, stopped at the next change."""

    def __init__(self, sound, sink):
        self.rec = sound.recorder(sink)
        self.meter, self.text, self.started = None, "", time.monotonic()
        exe = ffmpeg()
        if not self.rec or not exe:
            self.close()
            return
        cmd = [
            "nice",
            "-n",
            "19",
            exe,
            "-nostats",
            "-hide_banner",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-i",
            "-",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ]
        try:
            self.meter = subprocess.Popen(cmd, stdin=self.rec.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        except OSError:
            self.close()
            return
        self.rec.stdout.close()  # ffmpeg owns the pipe now
        threading.Thread(target=self._read, name="liner-notes-loudness", daemon=True).start()

    def _read(self):
        self.text = self.meter.stderr.read()

    @property
    def alive(self):
        return self.meter is not None

    def finish(self):
        """Stop and return (lufs, peak, seconds heard), or None."""
        if not self.alive:
            return None
        heard = time.monotonic() - self.started
        self.close()
        try:
            self.meter.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.meter.kill()
            return None
        time.sleep(0.05)  # let the reader thread take the last of stderr
        got = parse_summary(self.text)
        return (*got, heard) if got else None

    def close(self):
        if self.rec and self.rec.poll() is None:
            self.rec.terminate()
            try:
                self.rec.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.rec.kill()


class Leveler:
    """Sets cliamp's gain at every song change, and hears the songs it has no reading for.

    observe(np, changed) is called by the bridge with every NowPlaying, changed=True when the track
    is a new one; the work happens on its own thread so the bridge never waits on pactl. A position
    that jumps (a seek) spoils a live reading, since part of the song went unheard."""

    def __init__(self, table=None, sound=None, target=TARGET):
        self.table, self.sound, self.target = table or Table(), sound or Sound(), target
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self.pending, self.closed = None, False
        self.listening = None  # (key, duration, Listener, clean)
        self.last = None  # (position, monotonic time, state) of the last update, for jumps
        self.changed_at = 0.0
        self.applied = None  # (key, gain or None)
        threading.Thread(target=self._loop, name="liner-notes-leveler", daemon=True).start()

    def observe(self, np, changed):
        with self.lock:
            now = time.monotonic()
            if changed:
                self.pending, self.last, self.changed_at = ("track", np), None, now
                self.wake.notify()
                return
            if not np or not self.listening:
                return
            if now - self.changed_at < SETTLE:  # cliamp shows the old position until the stream starts
                self.last = None
                return
            if self.last and np.state == "playing" and self.last[2] == "playing":
                expected = self.last[0] + (now - self.last[1])
                if abs(np.position - expected) > JUMP:
                    self.listening = (*self.listening[:3], False)
            self.last = (np.position, now, np.state)

    def close(self):
        with self.lock:
            self.closed, self.pending = True, ("close", None)
            self.wake.notify()

    def _loop(self):
        while True:
            with self.lock:
                self.wake.wait_for(lambda: self.pending is not None)
                what, np = self.pending
                self.pending = None
            try:
                self._finish_listening()
                if what == "close":
                    return
                self._apply(np)
            except Exception:
                log.exception("leveling failed")

    def _finish_listening(self):
        with self.lock:
            cur, self.listening = self.listening, None
        if not cur:
            return
        key, duration, listener, clean = cur
        got = listener.finish()
        if not got or not clean or not duration or got[2] < HEARD_SHARE * duration or not math.isfinite(got[0]):
            log.info(
                "loudness of %s not kept (heard %s of %ss%s)",
                key,
                f"{got[2]:.0f}s" if got else "nothing",
                duration,
                "" if clean else ", jumped",
            )
            return
        row = self.table.put(key, got[0], got[1], "heard")
        log.info("loudness of %s heard: %s LUFS, peak %s", key, row["lufs"], row["peak"])

    def _apply(self, np):
        if not np or np.state == "stopped" or np.source != "cliamp":  # the gain is on cliamp's stream
            return
        track = np.as_track()
        key = key_for(track)
        stream = self.sound.stream()
        if not key or not stream:
            return
        index, sink = stream
        gain = gain_for(self.table.get(key) or {}, self.target)
        if gain is None:
            self.sound.set_gain(index, 0.0)
            self.sound.bypass(False)
            listener = Listener(self.sound, sink)
            if listener.alive:
                with self.lock:
                    self.listening = (key, np.duration, listener, True)
            log.info("%s: no reading yet, Autogain covers it%s", key, " while it's measured" if listener.alive else "")
        else:
            self.sound.set_gain(index, gain)
            self.sound.bypass(True)
            log.info("%s: %+.1f dB", key, gain)
        self.applied = (key, gain)

    def restore(self):
        """Back to 0 dB with Easy Effects on, as if the leveler had never run."""
        stream = self.sound.stream()
        if stream:
            self.sound.set_gain(stream[0], 0.0)
        self.sound.bypass(False)
