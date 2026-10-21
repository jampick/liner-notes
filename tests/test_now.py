import json
import os
import time

from liner_notes import adapters, ids
from liner_notes.adapters import Adapter, NowPlaying
from liner_notes.bridge.now import Library, Now, PlaylistFile, playlist_slug
from liner_notes.bridge.route import Latency
from liner_notes.bridge.theme import Theme

from .bridge_kit import TRACKS
from .fake_cliamp import FakeCliamp


def wait_for(cond, limit=3.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


class Other(Adapter):
    name = "other"
    capabilities = frozenset({"control"})

    def available(self):
        return True

    def now(self):
        return NowPlaying(title="Sailing", artist="Christopher Cross", state="playing", source="other", position=5, position_at=time.time())


def test_auto_moves_to_cliamp_when_its_socket_appears(start, env, monkeypatch):
    real = adapters.registry()
    monkeypatch.setattr(adapters, "registry", lambda: {"cliamp": real["cliamp"], "other": Other})
    picked = adapters.pick()
    assert picked.name == "other" and picked.auto
    b = start(fake=False, adapter=picked)
    assert b.get("/now")["source"] == "other"
    fake = FakeCliamp(env["sock"], [dict(t) for t in TRACKS])
    try:
        assert wait_for(lambda: b.get("/now")["source"] == "cliamp", 2.0)
        assert b.get("/now")["song"] == "massive-attack--protection"
    finally:
        fake.close()


def test_an_adapter_named_by_hand_stays(start, env):
    b = start(fake=False, adapter=Other())
    fake = FakeCliamp(env["sock"], [dict(t) for t in TRACKS])
    try:
        time.sleep(0.6)
        assert b.get("/now")["source"] == "other"
    finally:
        fake.close()


def test_cliamp_restart(start):
    b = start()
    assert b.get("/now")["ok"]
    b.fake.close()
    assert wait_for(lambda: b.get("/now").get("reason") == "no-player")
    b.fake.restart()
    assert wait_for(lambda: b.get("/now")["ok"])
    b.fake.play(1)
    assert wait_for(lambda: b.get("/now")["song"] == "christopher-cross--sailing")


def test_polling_spawns_nothing_and_uses_one_connection(start, monkeypatch):
    import subprocess

    spawned = []
    real = subprocess.Popen.__init__

    def spy(self, args, *a, **kw):
        spawned.append(args)
        return real(self, args, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    b = start()
    for _ in range(20):
        b.get("/now")
        time.sleep(0.05)
    assert spawned == []
    assert b.fake.calls["state.get"] >= 5


def test_moving_clock_end_to_end(start):
    b = start(clock=True, stale=1.3)
    b.fake.seek(40.0)
    assert wait_for(lambda: b.get("/now")["moving"], 2.0)
    r = b.get("/now")
    assert 40 < r["position"] < 43 and r["latency"] == 0
    e = r["epoch"]
    b.fake.play(1)
    time.sleep(0.15)
    r = b.get("/now")
    assert r["song"] == "christopher-cross--sailing" and not r["moving"] and r["position"] == 0.0
    time.sleep(0.8)
    assert not b.get("/now")["moving"]  # still the stale position
    assert wait_for(lambda: b.get("/now")["moving"], 2.0)
    r = b.get("/now")
    assert r["epoch"] > e and r["position"] < 1.5
    b.fake.set_state("paused")
    assert wait_for(lambda: b.get("/now")["state"] == "paused" and not b.get("/now")["moving"], 1.0)


def test_latency_from_the_table(env, monkeypatch):
    state = env["tmp"] / "state/liner-notes"
    state.mkdir(parents=True)
    (env["tmp"] / "pilot-av.json").write_text(json.dumps({"sink-a": 0.4, "sink-b": 0.3}))
    lat = Latency(find_route=lambda: "sink-a")
    assert lat.get() == 0.4  # from the pilot's table
    (state / "av-latency.json").write_text(json.dumps({"sink-a": 0.437}))
    assert lat.get() == 0.4  # looked up at most once a minute
    lat.at = None
    assert lat.get() == 0.437  # the new table wins
    assert Latency(find_route=lambda: "sink-b").get() == 0.3  # and the old one fills in
    assert Latency(find_route=lambda: "").get() == 0.0


def test_offline_paths_have_ids():
    assert ids.youtube_id("/home/u/.cache/journeys/audio/u7K72X4eo_s.webm") == "u7K72X4eo_s"
    assert ids.youtube_id("/home/u/.cache/liner-notes/audio/u7K72X4eo_s.m4a") == "u7K72X4eo_s"
    assert ids.youtube_id("/music/journeys/audio-notes/u7K72X4eo_s.webm") is None
    assert ids.youtube_id("https://youtu.be/u7K72X4eo_s") == "u7K72X4eo_s"


def test_offline_playlist_names():
    assert playlist_slug("Yacht Rock (offline)") == "yacht-rock"
    assert playlist_slug("Downtempo Drift") == "downtempo-drift"
    assert playlist_slug(None) is None


def test_journey_resolution(env):
    lib = Library([env["root"]])
    song = "massive-attack--protection"  # in demo and trip-hop
    assert lib.journey_for(song) == "demo"  # the first in the bank
    assert lib.journey_for(song, "Trip-Hop") == "trip-hop"  # the loaded playlist's journey
    assert lib.journey_for(song, "Trip-Hop (offline)") == "trip-hop"
    assert lib.journey_for(song, None, hint="trip-hop") == "trip-hop"  # cliamp says playlist: null; the queue decides
    assert lib.journey_for(song, "Soul Rap Covers") == "demo"  # a playlist with no journey
    assert lib.journey_for("christopher-cross--sailing", "Trip-Hop") == "demo"  # not on that journey
    assert lib.journey_for(None) is None
    tracks = [
        {"title": "Teardrop", "artist": "Massive Attack", "path": "/x/journeys/audio/u7K72X4eo_s.webm"},
        {"title": "Protection", "artist": "Massive Attack", "path": "https://www.youtube.com/watch?v=0Rw-UsRPNqc"},
        {"title": "?", "path": "y"},
    ]
    assert lib.queue_journey(tracks) == "trip-hop"
    assert lib.queue_journey(tracks[1:]) is None  # a tie says nothing


def test_playlist_file_names_the_journey(start, env):
    b = start()
    st = env["tmp"] / "state/liner-notes"
    st.mkdir(parents=True, exist_ok=True)
    (st / "playlist").write_text("Trip-Hop (offline)\n")
    r = b.get("/now")
    assert r["playlist"] == "Trip-Hop (offline)" and r["journey"] == "trip-hop"


def test_playlist_file_missing(tmp_path):
    assert PlaylistFile(tmp_path / "nope").get() is None


def test_theme_is_cached_and_survives_a_slow_font(tmp_path):
    root = tmp_path / "current"
    (root / "theme").mkdir(parents=True)
    (root / "theme/colors.toml").write_text('background = "#111111"\nforeground = "#eeeeee"\n')
    (root / "theme.name").write_text("astro-arc\n")
    slow = tmp_path / "font"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    calls = tmp_path / "calls"
    t = Theme(root, font_bin=str(slow))
    t0 = time.monotonic()
    r = t.get()
    assert r == {"ok": True, "name": "astro-arc", "mode": "dark", "colors": {"background": "#111111", "foreground": "#eeeeee"}, "font": ""}
    assert time.monotonic() - t0 < 4.5
    fast = tmp_path / "font2"
    fast.write_text(f"#!/bin/sh\necho x >> {calls}\necho 'JetBrains Mono'\n")
    fast.chmod(0o755)
    t = Theme(root, font_bin=str(fast))
    for _ in range(5):
        assert t.get()["font"] == "JetBrains Mono"
    assert calls.read_text().count("x") == 1
    (root / "theme/light.mode").write_text("")
    assert t.get()["mode"] == "light"  # a changed file is noticed at once


def test_theme_without_omarchy(tmp_path):
    assert Theme(tmp_path / "none", font_bin="/nonexistent").get() == {"ok": False}


def test_now_closes(env):
    fake = FakeCliamp(env["sock"], [dict(t) for t in TRACKS])
    from liner_notes.adapters.cliamp import CliampAdapter

    n = Now(Library([env["root"]]), CliampAdapter(), watch_every=0.1)
    assert n.payload()["ok"]
    n.close()
    time.sleep(0.4)
    assert n.clock.thread is None
    fake.close()
    assert not os.path.exists(env["sock"])


def test_exact_offline_name_from_play(start, env):
    """port/build's play writes the cliamp playlist's exact name, "(offline)" and all, no newline."""
    from liner_notes.bank import write_json

    write_json(
        env["root"] / "journeys/yacht-rock/journey.json",
        {"slug": "yacht-rock", "title": "Yacht Rock", "songs": ["christopher-cross--sailing"], "eras": []},
    )
    st = env["tmp"] / "state/liner-notes"
    st.mkdir(parents=True, exist_ok=True)
    (st / "playlist").write_text("Yacht Rock (offline)")
    b = start(playing=1)
    r = b.get("/now")
    assert r["song"] == "christopher-cross--sailing" and r["playlist"] == "Yacht Rock (offline)" and r["journey"] == "yacht-rock"


def test_theme_requests_dont_wait_on_the_font(tmp_path):
    import threading

    root = tmp_path / "current"
    (root / "theme").mkdir(parents=True)
    (root / "theme/colors.toml").write_text('background = "#111111"\n')
    (root / "theme.name").write_text("x\n")
    slow = tmp_path / "font"
    slow.write_text("#!/bin/sh\nsleep 1\necho Mono\n")
    slow.chmod(0o755)
    t = Theme(root, font_bin=str(slow))
    first = threading.Thread(target=t.get)
    first.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    assert t.get()["ok"] and time.monotonic() - t0 < 0.3  # answered from what's there
    first.join()
    assert t.get()["font"] == "Mono"


def test_library_reload_doesnt_block_readers(env, monkeypatch):
    import threading

    lib = Library([env["root"]], every=0)
    real = lib._load

    def slow():
        time.sleep(0.8)
        return real()

    monkeypatch.setattr(lib, "_stamp", lambda: ("changed",))
    monkeypatch.setattr(lib, "_load", slow)
    t = threading.Thread(target=lib.get)
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    bank, _ = lib.get()
    assert "demo" in bank.journeys and time.monotonic() - t0 < 0.2
    t.join()


def test_remix_uploads_name_their_remix_journey(tmp_path):
    d = tmp_path / "journeys" / "house"
    d.mkdir(parents=True)
    remixes = [
        {"title": "Ride Like the Wind (Yacht House Mix)", "path": "https://www.youtube.com/watch?v=8ODf0VI3Pp4", "id": "8ODf0VI3Pp4"},
        {"title": "What You Won't Do for Love (Party Pupils Remix)", "path": "https://soundcloud.com/x/y", "id": "2297020100"},
    ]
    (d / "journey.json").write_text(json.dumps({"slug": "house", "kind": "remix", "title": "House", "songs": [], "remixes": remixes}))
    (tmp_path / "songs").mkdir()
    lib = Library([tmp_path])
    assert lib.remix_journey({"path": "https://www.youtube.com/watch?v=8ODf0VI3Pp4", "title": "anything"}) == "house"
    assert lib.remix_journey({"path": "", "title": "what you won't do for love (party pupils remix)"}) == "house"
    assert lib.remix_journey({"path": "https://www.youtube.com/watch?v=zzzzzzzzzzz", "title": "Ride Like the Wind"}) is None
