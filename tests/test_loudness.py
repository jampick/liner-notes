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
    def __init__(self, stream=(7, "easyeffects_sink")):
        self.calls, self._stream = [], stream

    def stream(self):
        return self._stream

    def set_gain(self, index, db):
        self.calls.append(("gain", index, db))

    def bypass(self, on):
        self.calls.append(("bypass", on))

    def recorder(self, sink):
        self.calls.append(("record", sink))
        return None


def np_for(yt, state="playing", position=0.0, source="cliamp", duration=200.0):
    return NowPlaying(
        title=yt,
        artist="A",
        duration=duration,
        position=position,
        position_at=time.time(),
        state=state,
        source=source,
        ids={"path": f"https://www.youtube.com/watch?v={yt}", "youtube": yt},
    )


def leveler(tmp_path, sound=None):
    t = loudness.Table(tmp_path / "l.json")
    t.put("loudloudxxx", -8.0, 0.4, "file")
    lev = loudness.Leveler(table=t, sound=sound or FakeSound())
    return lev, lev.sound


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
    assert sound.calls == [("gain", 7, 0.0), ("bypass", False), ("record", "easyeffects_sink")]
    lev.close()


def test_other_players_and_no_stream_change_nothing(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.observe(np_for("loudloudxxx", source="mpris"), True)
    lev.observe(np_for("loudloudxxx", state="stopped"), True)
    time.sleep(0.3)
    assert sound.calls == []
    lev.close()
    lev, sound = leveler(tmp_path, FakeSound(stream=None))
    lev.observe(np_for("loudloudxxx"), True)
    time.sleep(0.3)
    assert sound.calls == []
    lev.close()


def test_restore_puts_back_0_db_and_autogain(tmp_path):
    lev, sound = leveler(tmp_path)
    lev.restore()
    assert sound.calls == [("gain", 7, 0.0), ("bypass", False)]
    lev.close()


class StubListener:
    def __init__(self, got):
        self.got, self.alive = got, True

    def finish(self):
        return self.got


def listening(lev, got, duration=200.0):
    with lev.lock:
        lev.listening = ("songsongxxx", duration, StubListener(got), True)


def test_a_song_heard_through_is_kept(tmp_path):
    lev, _ = leveler(tmp_path)
    listening(lev, (-11.0, -0.8, 199.0))
    lev._finish_listening()
    assert lev.table.get("songsongxxx")["lufs"] == -11.0 and lev.table.get("songsongxxx")["from"] == "heard"
    lev.close()


def test_a_song_skipped_early_is_not_kept(tmp_path):
    lev, _ = leveler(tmp_path)
    listening(lev, (-11.0, -0.8, 60.0))
    lev._finish_listening()
    assert lev.table.get("songsongxxx") is None
    lev.close()


def test_a_seek_spoils_the_reading(tmp_path):
    lev, _ = leveler(tmp_path)
    listening(lev, (-11.0, -0.8, 199.0))
    lev.changed_at = time.monotonic() - 60  # past the settle time
    lev.observe(np_for("songsongxxx", position=50.0), False)
    lev.observe(np_for("songsongxxx", position=120.0), False)  # 70 s on in no time
    lev._finish_listening()
    assert lev.table.get("songsongxxx") is None
    lev.close()


def test_the_old_position_right_after_a_change_is_not_a_seek(tmp_path):
    lev, _ = leveler(tmp_path)
    listening(lev, (-11.0, -0.8, 199.0))
    lev.changed_at = time.monotonic()
    lev.observe(np_for("songsongxxx", position=210.0), False)  # cliamp still shows the last song's place
    lev.observe(np_for("songsongxxx", position=0.2), False)
    lev._finish_listening()
    assert lev.table.get("songsongxxx")["lufs"] == -11.0
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
    tone = [
        sys.executable,
        "-c",
        "import math,struct,sys; sys.stdout.buffer.write(b''.join(struct.pack('<hh', v, v) for v in (int(8000*math.sin(i/48000*2*math.pi*1000)) for i in range(48000*4))))",
    ]

    class ToneSound(FakeSound):
        def recorder(self, sink):
            return subprocess.Popen(tone, stdout=subprocess.PIPE)

    lst = loudness.Listener(ToneSound(), "x")
    assert lst.alive
    time.sleep(1.5)  # the tone ends by itself; finish() must still read the summary
    lufs, peak, heard = lst.finish()
    assert -16 < lufs < -9 and peak < -10 and heard > 1
