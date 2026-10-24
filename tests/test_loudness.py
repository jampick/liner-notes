import json
import math
import shutil
import subprocess
import sys
import time

import pytest

from liner_notes import loudness
from liner_notes.adapters import NowPlaying
from liner_notes.bridge.now import Library, Now

from .test_now import wait_for

SUMMARY = """[Parsed_ebur128_0 @ 0x1] t: 262.4  TARGET:-23 LUFS    M: -12.1 S: -12.9     I: -13.4 LUFS       LRA:   3.0 LU  FTPK: -1.0 dBFS  TPK:  0.1 dBFS
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -13.0 LUFS
    Threshold: -23.1 LUFS

  Loudness range:
    LRA:         3.0 LU

  True peak:
    Peak:        0.1 dBFS
"""


def test_parse_summary_reads_the_summary_not_the_last_frame():
    assert loudness.parse_summary(SUMMARY) == (-13.0, 0.1)


def test_parse_summary_without_a_summary_is_none():
    assert loudness.parse_summary(SUMMARY.split("Summary:")[0]) is None
    assert loudness.parse_summary("") is None


def test_parse_summary_of_silence():
    assert loudness.parse_summary("Summary:\n I: -70.0 LUFS\n Peak: -inf dBFS\n") == (-70.0, -math.inf)


def test_gain_cuts_a_loud_master_to_the_target():
    assert loudness.gain_for({"lufs": -8.0, "peak": 0.5}) == -10.0


def test_gain_boost_stops_at_the_peak_ceiling_and_max_boost():
    assert loudness.gain_for({"lufs": -22.0, "peak": -3.0}) == 2.0  # -1 dBTP ceiling
    assert loudness.gain_for({"lufs": -40.0, "peak": -30.0}) == loudness.MAX_BOOST
    assert loudness.gain_for({"lufs": -22.0, "peak": 0.0}) == 0.0
    assert loudness.gain_for({"lufs": -22.0, "peak": None}) == 0.0  # no peak, no boost
    assert loudness.gain_for({"lufs": -12.0, "peak": None}) == -6.0


def test_gain_without_a_reading_is_none():
    assert loudness.gain_for({}) is None
    assert loudness.gain_for({"lufs": None}) is None
    assert loudness.gain_for({"lufs": "-inf"}) is None


def test_key_is_the_youtube_id_online_and_offline():
    assert loudness.key_for({"path": "https://www.youtube.com/watch?v=0Rw-UsRPNqc"}) == "0Rw-UsRPNqc"
    assert loudness.key_for({"path": "/home/u/.cache/journeys/audio/u7K72X4eo_s.webm"}) == "u7K72X4eo_s"
    assert loudness.key_for({"path": "/music/x.flac"}) == "/music/x.flac"
    assert loudness.key_for({}) is None


def test_table_round_trip(tmp_path):
    t = loudness.Table(tmp_path / "l.json")
    t.put("abc", -9.04, -0.31, "file")
    again = loudness.Table(tmp_path / "l.json")
    assert again.get("abc")["lufs"] == -9.0 and again.get("abc")["peak"] == -0.3 and again.get("abc")["from"] == "file"
    assert again.get("nope") is None and again.get(None) is None


def test_a_broken_table_starts_empty(tmp_path):
    (tmp_path / "l.json").write_text("[1, 2")
    assert loudness.Table(tmp_path / "l.json").rows == {}


def test_scan_measures_what_the_table_lacks(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    for n in ("aaaaaaaaaaa.webm", "bbbbbbbbbbb.m4a", "ccccccccccc.webm", "notes.txt"):
        (audio / n).write_bytes(b"x")
    t = loudness.Table(tmp_path / "l.json")
    t.put("aaaaaaaaaaa", -10, -1, "file")
    seen = []

    def measure(p):
        seen.append(p.stem)
        return None if p.stem == "ccccccccccc" else (-12.0, -0.5)

    assert loudness.scan(audio, t, out=open("/dev/null", "w"), measure=measure) == (1, 1)
    assert seen == ["bbbbbbbbbbb", "ccccccccccc"]
    assert t.get("bbbbbbbbbbb")["lufs"] == -12.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_measure_file_reads_a_real_tone(tmp_path):
    f = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "sine=frequency=1000:duration=5", "-af", "volume=-6dB", str(f)], check=True
    )
    lufs, peak = loudness.measure_file(f)
    assert -28 < lufs < -26 and -25 < peak < -23  # lavfi's sine is 1/8 full scale, then -6 dB


class FakeSound:
    def __init__(self, stream=7):
        self.calls, self._stream = [], stream

    def stream(self):
        return self._stream

    def set_gain(self, index, db):
        self.calls.append(("gain", index, db))
        return index == self._stream

    def bypass(self, on):
        self.calls.append(("bypass", on))

    def recorder(self, index):
        self.calls.append(("record", index))
        return None


def np_for(yt, state="playing", position=0.0, source="cliamp", duration=200.0, at=None):
    return NowPlaying(
        title=yt,
        artist="A",
        duration=duration,
        position=position,
        position_at=time.time() if at is None else at,
        state=state,
        source=source,
        ids={"path": f"https://www.youtube.com/watch?v={yt}", "youtube": yt},
    )


def leveler(tmp_path, sound=None, **kw):
    t = loudness.Table(tmp_path / "l.json")
    t.put("loudloudxxx", -8.0, 0.4, "file")
    lev = loudness.Leveler(table=t, sound=sound or FakeSound(), **kw)
    lev.sound.calls.clear()  # the restore every Leveler starts with
    return lev, lev.sound


class Ran:
    def __init__(self, out="", code=0):
        self.argv, self.out, self.code = [], out, code

    def __call__(self, argv, **kw):
        self.argv.append(argv)
        return subprocess.CompletedProcess(argv, self.code, self.out, "")


def test_sound_sets_an_absolute_volume_never_a_step():
    ran = Ran()
    loudness.Sound(run=ran).set_gain(42, -10.0)
    loudness.Sound(run=ran).set_gain(42, 0.0)
    loudness.Sound(run=ran).set_gain(42, 3.0)
    assert ran.argv == [
        ["pactl", "set-sink-input-volume", "42", "44649"],  # 65536 * 10^(-10/60): the cubic scale (pactl shows 62259 as -1.34 dB)
        ["pactl", "set-sink-input-volume", "42", "65536"],
        ["pactl", "set-sink-input-volume", "42", "73533"],
    ]
    assert all(not a[-1].startswith(("+", "-")) for a in ran.argv)


def test_sound_reports_a_refused_gain():
    assert loudness.Sound(run=Ran(code=1)).set_gain(42, -3.0) is False


@pytest.mark.skipif(not shutil.which("pactl"), reason="needs pactl on PATH")
def test_sound_finds_cliamps_stream():
    rows = [
        {"index": 3, "properties": {"application.name": "Firefox"}},
        {"index": 9, "properties": {"application.name": "PipeWire ALSA [cliamp]"}},
    ]
    assert loudness.Sound(run=Ran(json.dumps(rows))).stream() == 9
    assert loudness.Sound(run=Ran("[]")).stream() is None
    assert loudness.Sound(run=Ran("not json")).stream() is None


def test_a_leveler_starts_by_undoing_what_a_crashed_one_left():
    sound = FakeSound()
    lev = loudness.Leveler(table=loudness.Table("/nonexistent/l.json"), sound=sound)
    assert sound.calls == [("gain", 7, 0.0), ("bypass", False)]
    lev.close()


def test_a_known_song_gets_its_gain_with_easy_effects_bypassed(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("loudloudxxx"), True)
    assert wait_for(lambda: len(sound.calls) >= 2)
    assert sound.calls == [("gain", 7, -10.0), ("bypass", True)]
    lev.close()


def test_an_unknown_song_plays_flat_with_autogain_on(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("neverheardx"), True)
    assert wait_for(lambda: len(sound.calls) >= 3)
    assert sound.calls == [("gain", 7, 0.0), ("bypass", False), ("record", 7)]
    lev.close()


def test_updates_within_a_play_set_nothing_again(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("loudloudxxx"), True)
    assert wait_for(lambda: len(sound.calls) >= 2)
    for pos in (1, 2, 3):
        lev.observe(np_for("loudloudxxx", position=pos), False)
    lev.observe(np_for("loudloudxxx", position=4), True)  # back from a blip in the player
    time.sleep(0.3)
    assert sound.calls == [("gain", 7, -10.0), ("bypass", True)]
    lev.close()


def test_a_new_stream_index_is_found_again(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("loudloudxxx"), True)
    assert wait_for(lambda: len(sound.calls) >= 2)
    sound._stream = 8  # cliamp restarted
    lev.observe(np_for("neverheardx"), True)
    assert wait_for(lambda: ("gain", 8, 0.0) in sound.calls)
    assert ("gain", 7, 0.0) in sound.calls  # the old index was tried first and refused
    lev.close()


def test_other_players_and_no_stream_change_nothing(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("loudloudxxx", source="mpris"), True)
    for pos in (1, 2):
        lev.observe(np_for("loudloudxxx", source="mpris", position=pos), False)
    time.sleep(0.3)
    lev.observe(np_for("othersongxx", state="stopped"), True)
    time.sleep(0.3)
    assert sound.calls == []
    lev.close()
    lev, sound = leveler(tmp_path, FakeSound(stream=None))
    lev.observe(np_for("loudloudxxx"), True)
    time.sleep(0.3)
    assert sound.calls == []  # no stream to set
    lev.close()


def test_restore_puts_back_0_db_and_autogain():
    sound = FakeSound()
    loudness.restore(sound)
    assert sound.calls == [("gain", 7, 0.0), ("bypass", False)]


# --- hearing a song through ----------------------------------------------------------------------


def heard(play, samples, t0=1000.0):
    """Feed (seconds after t0, position, state) samples to a Play past its settle time."""
    play.started = True
    for dt_, pos, state in samples:
        play.see(np_for(play.key, state=state, position=pos, at=t0 + dt_))
    return play


def test_a_play_heard_through_counts():
    p = heard(loudness.Play("s", "cliamp", 200.0), [(t, t, "playing") for t in range(0, 200, 2)])
    assert p.clean and p.counts()


def test_a_pause_is_not_listening():
    samples = [(t, t, "playing") for t in range(0, 140, 2)] + [(t, 140, "paused") for t in range(140, 200, 2)]
    p = heard(loudness.Play("s", "cliamp", 200.0), samples)
    assert p.clean and not p.counts()


def test_a_seek_spoils_the_play_paused_or_not():
    p = heard(
        loudness.Play("s", "cliamp", 200.0),
        [(0, 0, "playing"), (2, 2, "playing"), (4, 80, "playing")] + [(t, t + 76, "playing") for t in range(6, 130, 2)],
    )
    assert not p.clean and not p.counts()
    p = heard(loudness.Play("s", "cliamp", 200.0), [(0, 50, "paused"), (2, 120, "paused")])
    assert not p.clean


def test_the_last_songs_position_right_after_a_change_is_ignored():
    p = loudness.Play("s", "cliamp", 200.0)
    p.see(np_for("s", position=212.0))  # cliamp still shows where the last song was
    t0 = time.time()
    for t in range(0, 200, 2):
        p.see(np_for("s", position=t + 0.2, at=t0 + t))
    assert p.clean and p.counts()


class StubListener:
    def __init__(self, got):
        self.got, self.alive, self.stopped = got, True, False

    def stop(self):
        self.stopped = True

    def result(self):
        return self.got


def test_a_heard_play_is_stored_and_a_short_one_is_not(tmp_path):
    lev, _ = leveler(tmp_path)
    full = heard(loudness.Play("songsongxxx", "cliamp", 200.0, StubListener((-11.0, -0.8))), [(t, t, "playing") for t in range(0, 200, 2)])
    lev._keep(full)
    assert lev.table.get("songsongxxx")["lufs"] == -11.0 and lev.table.get("songsongxxx")["from"] == "heard"
    short = heard(loudness.Play("shortshort1", "cliamp", 200.0, StubListener((-11.0, -0.8))), [(t, t, "playing") for t in range(0, 60, 2)])
    lev._keep(short)
    assert lev.table.get("shortshort1") is None
    lev.close()


def test_the_gain_goes_in_before_the_last_reading_is_read(tmp_path):
    lev, sound = leveler(tmp_path)
    order = []
    stub = StubListener((-11.0, -0.8))
    stub.stop = lambda: order.append("stop")
    stub.result = lambda: order.append("result") or (-11.0, -0.8)
    sound.set_gain = lambda i, db: order.append(("gain", db)) or True
    with lev.lock:
        lev.play = loudness.Play("songsongxxx", "cliamp", 200.0, stub)
    lev.observe(np_for("loudloudxxx"), True)
    assert wait_for(lambda: "result" in order)
    assert order == ["stop", ("gain", -10.0), "result"]
    lev.close()


def test_a_song_starting_over_is_a_new_play(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("neverheardx"), True)
    assert wait_for(lambda: len(sound.calls) >= 3)
    with lev.lock:
        lev.play.last = (198.0, time.time(), "playing")
    lev.observe(np_for("neverheardx", position=0.5), False)  # repeat: same track from the top
    assert wait_for(lambda: len(sound.calls) >= 6)
    lev.close()


def test_the_leveler_polls_while_it_listens(tmp_path):
    asked = []
    lev, sound = leveler(tmp_path, poll=lambda: asked.append(1) or np_for("neverheardx", position=len(asked)), poll_every=0.05)
    with lev.lock:
        lev.play = loudness.Play("neverheardx", "cliamp", 200.0, StubListener(None))
    assert wait_for(lambda: len(asked) >= 3)
    lev.close()
    lev, sound = leveler(tmp_path, poll=lambda: asked.append(1), poll_every=0.05)
    n = len(asked)
    time.sleep(0.3)
    assert len(asked) == n  # nothing being heard: no polling
    lev.close()


class Fixed:
    name = "fixed"
    capabilities = frozenset()
    auto = False

    def __init__(self):
        self.np = np_for("loudloudxxx")

    def available(self):
        return True

    def now(self):
        return self.np


def test_now_tells_observers_about_new_tracks(env):
    seen = []
    ad = Fixed()
    now = Now(
        Library(),
        ad,
        latency=lambda: 0.0,
        watch_every=0.05,
        observers=[lambda np, changed: seen.append((np.title if np else None, changed))],
    )
    assert wait_for(lambda: any(c for _, c in seen))
    ad.np = np_for("othersongxx")
    assert wait_for(lambda: ("othersongxx", True) in seen)
    assert [t for t, c in seen if c] == ["loudloudxxx", "othersongxx"]
    now.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_listener_measures_what_the_recorder_sends(tmp_path):
    code = "import math,struct,sys; sys.stdout.buffer.write(b''.join(struct.pack('<hh', v, v) for v in (int(8000*math.sin(i/48000*2*math.pi*1000)) for i in range(48000*4))))"

    class ToneSound(FakeSound):
        def recorder(self, index):
            return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE)

    lst = loudness.Listener(ToneSound(), 7)
    assert lst.alive
    time.sleep(1.5)  # the tone ends by itself; result() must still read the summary
    lufs, peak = lst.result()
    assert -16 < lufs < -9 and peak < -10
