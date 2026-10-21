import json
import threading
import time
import urllib.parse

from liner_notes.bank import write_json


def test_lyrics_from_cliamp_with_source(start):
    b = start()
    r = b.get("/lyrics?yt=0Rw-UsRPNqc")
    assert r["ok"] and r["source"] == "cliamp" and r["song"] == "massive-attack--protection"
    assert r["lines"][0] == {"t": 1.5, "start": 1.5, "text": "lyrics of Protection (Remastered 2006)"}
    assert b.get("/lyrics?song=massive-attack--protection")["ok"]
    r = b.get("/lyrics?yt=AAAAAAAAAAA&song=christopher-cross--sailing")
    assert not r["ok"] and r["error"] == "not playing"


def test_lyrics_never_answer_for_another_song(start):
    """The race: cliamp's lookup takes a while and answers for whatever plays by then."""
    b = start()
    b.fake.lyrics_delay = 1.0
    out = {}
    t = threading.Thread(target=lambda: out.update(b.get("/lyrics?yt=0Rw-UsRPNqc")))
    t.start()
    time.sleep(0.3)
    b.fake.play(1)  # A -> B while A's lookup runs
    t.join()
    assert "Sailing" not in json.dumps(out) and not out["ok"]
    b.fake.lyrics_delay = 0.0
    b.fake.play(0)  # back to A: a fresh lookup, nothing from B was kept
    time.sleep(0.3)
    r = b.get("/lyrics?yt=0Rw-UsRPNqc")
    assert r["ok"] and r["lines"][0]["text"].startswith("lyrics of Protection")
    assert b.fake.lyrics_calls == 2


def test_one_lookup_for_many_pages(start):
    b = start()
    b.fake.lyrics_delay = 0.5
    results = []
    ts = [threading.Thread(target=lambda: results.append(b.get("/lyrics?yt=0Rw-UsRPNqc"))) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(results) == 10 and all(r["ok"] for r in results)
    assert b.fake.lyrics_calls == 1
    b.get("/lyrics")
    assert b.fake.lyrics_calls == 1  # and it's cached


def test_misses_are_remembered_for_a_while(start, env):
    b = start(lyrics_ttl=0.4)
    b.fake.lyrics = {"ok": False, "error": "no lyrics found"}
    assert not b.get("/lyrics")["ok"]
    assert not b.get("/lyrics")["ok"]
    assert b.fake.lyrics_calls == 1 and len(env["lrclib"].hits) == 1
    time.sleep(0.5)
    b.get("/lyrics")
    assert b.fake.lyrics_calls == 2 and len(env["lrclib"].hits) == 2


def test_retimed_file_wins(start, env):
    b = start()
    write_json(env["tmp"] / "data/lyrics/0Rw-UsRPNqc.json", {"ytid": "0Rw-UsRPNqc", "lines": [{"start": 2.25, "text": "retimed line"}]})
    r = b.get("/lyrics?yt=0Rw-UsRPNqc")
    assert r["ok"] and r["source"] == "retimed" and r["lines"] == [{"t": 2.25, "start": 2.25, "text": "retimed line"}]
    assert b.fake.lyrics_calls == 0


def test_pilot_retimed_file_is_the_fallback(start, env):
    b = start()
    write_json(env["tmp"] / "pilot/lyrics/0Rw-UsRPNqc.json", {"lines": [{"start": 3, "text": "from the pilot"}]})
    r = b.get("/lyrics")
    assert r["source"] == "retimed" and r["lines"][0]["text"] == "from the pilot"


def test_lrclib_when_cliamp_has_only_plain_lyrics(start, env):
    b = start()
    b.fake.lyrics = {"ok": True, "lyrics": [{"start": 0, "text": "plain"}, {"start": 0, "text": "lines"}]}
    env["lrclib"].rows = [
        {"duration": 300, "syncedLyrics": "[00:01.00] too short"},
        {"duration": 468, "syncedLyrics": "[00:02.50] the right one\n[01:00.00] second"},
        {"duration": 500, "plainLyrics": "no timing"},
    ]
    r = b.get("/lyrics")
    assert r["ok"] and r["source"] == "lrclib" and r["lines"][0] == {"t": 2.5, "start": 2.5, "text": "the right one"}
    assert r["lines"][1]["t"] == 60.0
    q = urllib.parse.parse_qs(env["lrclib"].hits[0].split("?", 1)[1])
    assert q == {"artist_name": ["Massive Attack"], "track_name": ["Protection"]}  # the bank's names, not the upload's


def test_a_change_and_back_during_the_lookup_is_caught(start):
    """A -> B -> A inside one cliamp call, with no events: cliamp fetched B's lyrics, A is playing again."""
    b = start()
    b.fake.quiet = True
    b.fake.lyrics_mid = True
    b.fake.lyrics_delay = 1.2
    out = {}
    t = threading.Thread(target=lambda: out.update(b.get("/lyrics?yt=0Rw-UsRPNqc")))
    t.start()
    time.sleep(0.35)
    b.fake.index = 1
    time.sleep(0.5)
    b.fake.index = 0
    t.join()
    assert "Sailing" not in json.dumps(out) and not out["ok"]
    b.fake.lyrics_mid, b.fake.lyrics_delay = False, 0.0
    r = b.get("/lyrics?yt=0Rw-UsRPNqc")
    assert r["ok"] and "Protection" in r["lines"][0]["text"]  # nothing bad was cached


def test_followers_wait_longer_than_the_leader_can_take():
    from liner_notes.bridge.lyrics import Lyrics

    ly = Lyrics(lambda: None, lambda: (None, 0))
    assert ly.wait > Lyrics.CLIAMP_TIMEOUT + Lyrics.LRCLIB_TIMEOUT
