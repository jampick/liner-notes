import json
import time

from liner_notes.adapters import NowPlaying, homeassistant, lastfm, macos, mpris, smtc


def test_position_extrapolates_only_while_playing():
    n = NowPlaying(title="x", duration=100, position=10, position_at=1000, state="playing")
    assert n.position_now(1005) == 15 and n.position_now(2000) == 100
    n.state = "paused"
    assert n.position_now(1005) == 10


def test_mpris_metadata():
    meta = json.loads(
        '{"type":"a{sv}","data":{"mpris:trackid":{"type":"o","data":"/com/spotify/track/4uLU6hMCjMI75M1A2tKUQC"},'
        '"xesam:title":{"type":"s","data":"Sing It Back"},"xesam:artist":{"type":"as","data":["Moloko"]},'
        '"xesam:album":{"type":"s","data":"I Am Not a Doctor"},"mpris:length":{"type":"t","data":267000000},'
        '"xesam:url":{"type":"s","data":"https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"}}}'
    )
    n = mpris.parse_metadata(meta, "Playing", 61_500_000, "org.mpris.MediaPlayer2.spotify")
    assert (n.title, n.artist, n.album, n.duration, n.position, n.state) == (
        "Sing It Back",
        "Moloko",
        "I Am Not a Doctor",
        267,
        61.5,
        "playing",
    )
    assert n.ids["spotify_id"] == "4uLU6hMCjMI75M1A2tKUQC" and n.ids["mpris_player"] == "spotify"
    local = json.loads(
        '{"type":"a{sv}","data":{"xesam:title":{"type":"s","data":"Tinseltown"},"xesam:url":{"type":"s","data":"file:///music/The%20Blue%20Nile/02.flac"}}}'
    )
    assert mpris.parse_metadata(local, "Paused").as_track()["path"] == "/music/The Blue Nile/02.flac"
    assert mpris.parse_metadata({"data": {}}) is None


def test_macos_stream_merges_diffs():
    st = {}
    n = macos.parse(
        st,
        json.dumps(
            {
                "type": "data",
                "diff": False,
                "payload": {
                    "title": "Avalon",
                    "artist": "Roxy Music",
                    "duration": 256.1,
                    "elapsedTime": 12,
                    "timestamp": "2026-10-18T20:00:00Z",
                    "playing": True,
                    "bundleIdentifier": "com.apple.Music",
                },
            }
        ),
    )
    assert n.title == "Avalon" and n.position == 12 and n.ids["app"] == "com.apple.Music"
    n = macos.parse(st, json.dumps({"type": "data", "diff": True, "payload": {"playing": False, "elapsedTime": 30}}))
    assert n.title == "Avalon" and n.state == "paused" and n.position == 30


def test_smtc_contract():
    n = smtc.parse(
        json.dumps(
            {
                "title": "Smooth Operator",
                "artist": "Sade",
                "durationSec": 298,
                "positionSec": 40,
                "status": "Playing",
                "lastUpdated": "2026-10-18T20:00:00Z",
                "appId": "Spotify.exe",
            }
        )
    )
    assert n.state == "playing" and n.duration == 298 and n.position_at > 0 and n.ids["app"] == "Spotify.exe"


def test_homeassistant_compressed_state():
    st, e = {}, "media_player.lounge"
    first = {
        "a": {
            e: {
                "s": "playing",
                "a": {
                    "media_title": "Sailing",
                    "media_artist": "Christopher Cross",
                    "media_duration": 255,
                    "media_position": 20,
                    "media_position_updated_at": "2026-10-18T20:00:00+00:00",
                    "media_content_id": "spotify:track:abc",
                },
            }
        }
    }
    n = homeassistant.merge(st, first, e)
    assert n.title == "Sailing" and n.ids["spotify_id"] == "abc" and n.position_at == 1792353600.0
    n = homeassistant.merge(st, {"c": {e: {"+": {"s": "paused", "a": {"media_position": 31}}}}}, e)
    assert n.state == "paused" and n.position == 31 and n.title == "Sailing"
    assert homeassistant.merge(st, {"c": {e: {"-": {"a": ["media_title"]}}}}, e) is None


def test_lastfm_nowplaying():
    doc = {
        "recenttracks": {
            "track": [
                {"name": "Avalon", "artist": {"#text": "Roxy Music"}, "album": {"#text": "Avalon"}, "@attr": {"nowplaying": "true"}},
                {"name": "Older", "artist": {"#text": "X"}, "date": {"uts": "1"}},
            ]
        }
    }
    n = lastfm.parse(doc)
    assert n.title == "Avalon" and n.artist == "Roxy Music"
    assert lastfm.parse({"recenttracks": {"track": {"name": "Older", "artist": {"#text": "X"}}}}) is None
    assert time.time() > 0
