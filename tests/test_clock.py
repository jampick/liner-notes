import threading
import time

from liner_notes.adapters import NowPlaying
from liner_notes.bridge.clock import Clock


def np(pos, path="a", state="playing"):
    return NowPlaying(title=path, artist="x", position=pos, state=state, ids={"path": path})


def play(clock, start, t0, t1, path="a", step=0.2, rate=1.0, quantum=0.125, at0=None):
    """Feed samples of a track that was at `start` at time `at0` (default t0), quantised the way cliamp is."""
    at0 = t0 if at0 is None else at0
    t = t0
    while t < t1:
        p = int((start + (t - at0) * rate) / quantum) * quantum
        clock.feed(np(p, path), t)
        t += step
    return t


HALF_STEP = 0.0625  # cliamp's 125 ms steps floor the position; the fit sits half a step behind, which avsync measures in


def test_smoothed_position_drifts_under_30ms():
    c = Clock(lambda: None)
    t = play(c, 50.0, 0.0, 6.0)
    for dt in (0.0, 0.05, 0.11, 0.17):
        r = c.read(t + dt)
        assert r["moving"]
        assert abs(r["position"] - (50.0 + t + dt - HALF_STEP)) < 0.03


def test_latency_comes_off():
    c = Clock(lambda: None)
    t = 0.0
    while t < 4:
        c.feed(np(int((60 + t) / 0.125) * 0.125), t, latency=0.437)
        t += 0.2
    r = c.read(t)
    assert r["latency"] == 0.437 and abs(r["position"] - (60 + t - 0.437 - HALF_STEP)) < 0.03


def test_seek_and_pause_bump_epoch():
    c = Clock(lambda: None)
    t = play(c, 10.0, 0.0, 3.0)
    e = c.read(t)["epoch"]
    t = play(c, 120.0, t, t + 2.0, at0=t)  # a jump
    e2 = c.read(t)["epoch"]
    assert e2 > e and abs(c.read(t)["position"] - 122.0) < 0.2
    p = c.read(t)["position"]
    for k in range(5):
        c.feed(np(p, state="paused"), t + k * 0.2)
    r = c.read(t + 1.0)
    assert r["epoch"] > e2 and not r["moving"] and abs(r["position"] - p) < 0.2


def test_song_change_holds_still_through_the_stale_position():
    c = Clock(lambda: None)
    t = play(c, 170.0, 0.0, 3.0, path="a")
    e = c.read(t)["epoch"]
    # cliamp shows song b at once but keeps a's last position for 1.3 s while the stream buffers.
    stale = c.read(t)["position"]
    end = t + 1.3
    while t < end:
        c.feed(np(int(stale / 0.125) * 0.125, "b"), t)
        r = c.read(t)
        assert not r["moving"] and r["position"] == 0.0
        t += 0.2
    t = play(c, 0.0, t, t + 0.25, path="b", at0=t)
    assert c.read(t)["epoch"] > e
    t = play(c, 0.25, t, t + 2.0, path="b", at0=t - 0.25)
    r = c.read(t)
    assert r["moving"] and r["np"].title == "b" and 1.5 < r["position"] < 3.0


def test_new_song_that_waits_at_zero_is_not_moving():
    c = Clock(lambda: None)
    t = play(c, 100.0, 0.0, 2.0, path="a")
    for k in range(5):
        c.feed(np(0.0, "b"), t + k * 0.2)
    assert not c.read(t + 1.0)["moving"]
    t = play(c, 0.0, t + 1.0, t + 2.0, path="b")
    assert c.read(t)["moving"]


def test_not_moving_until_past_the_latency():
    c = Clock(lambda: None)
    t = 0.0
    while t < 0.3:
        c.feed(np(round(t, 3), "a"), t, latency=0.4)
        t += 0.1
    assert not c.read(t)["moving"]


def test_one_sampler_however_many_readers():
    calls = []

    def sample():
        calls.append(threading.get_ident())
        return np(12.0)

    c = Clock(sample, interval=0.05, idle=0.3)
    ts = [threading.Thread(target=c.now) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    time.sleep(0.2)
    assert len(set(calls)) == 1
    time.sleep(0.5)  # idle: the sampler stops
    assert c.thread is None
    assert c.now()["np"].position == 12.0  # and comes back for the next reader
    c.close()


def test_now_waits_for_the_first_sample_only():
    c = Clock(lambda: None, interval=0.05)
    t0 = time.monotonic()
    assert c.now() is None
    assert c.now() is None
    assert time.monotonic() - t0 < 0.5
    c.close()
