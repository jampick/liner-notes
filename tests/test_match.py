from liner_notes.ids import clean_title, song_id, youtube_id
from liner_notes.match import Matcher

SONGS = {
    s["id"]: s
    for s in [
        {"id": "massive-attack--protection", "title": "Protection", "artist": "Massive Attack", "aliases": {"youtube": ["0Rw-UsRPNqc"]}},
        {"id": "massive-attack--unfinished-sympathy", "title": "Unfinished Sympathy", "artist": "Massive Attack", "aliases": {}},
        {"id": "air--la-femme-dargent", "title": "La Femme d'Argent", "artist": "Air", "aliases": {}},
        {"id": "khruangbin-leon-bridges--texas-sun", "title": "Texas Sun", "artist": "Khruangbin & Leon Bridges", "aliases": {}},
        {"id": "roisin-murphy--x", "title": "Sing It Back", "artist": "Moloko", "aliases": {"spotify": ["4uLU6hMCjMI75M1A2tKUQC"]}},
        {
            "id": "the-blue-nile--tinseltown-in-the-rain",
            "title": "Tinseltown in the Rain",
            "artist": "The Blue Nile",
            "aliases": {"local": ["/music/blue nile/02.flac"]},
        },
        {"id": "christopher-cross--sailing", "title": "Sailing", "artist": "Christopher Cross", "aliases": {}},
        {"id": "roxy-music--more-than-this", "title": "More Than This", "artist": "Roxy Music", "aliases": {}},
    ]
}
M = Matcher(SONGS)


def test_song_id():
    assert song_id("Massive Attack", "Unfinished Sympathy (2012 Mix)") == "massive-attack--unfinished-sympathy"
    assert song_id("Röyksopp", "Eple") == "royksopp--eple"
    assert song_id("Khruangbin & Leon Bridges", "Texas Sun") == "khruangbin-leon-bridges--texas-sun"
    assert song_id("Air", "La Femme d'Argent") == "air--la-femme-dargent"


def test_youtube_id():
    assert youtube_id("https://www.youtube.com/watch?v=ZWmrfgj0MZI") == "ZWmrfgj0MZI"
    assert youtube_id("https://youtu.be/ZWmrfgj0MZI?t=3") == "ZWmrfgj0MZI"
    assert youtube_id("https://music.youtube.com/watch?v=ZWmrfgj0MZI&list=x") == "ZWmrfgj0MZI"
    assert youtube_id("/music/a.flac") is None


def test_clean_title():
    assert clean_title("Protection (Remastered 2006)") == "Protection"
    assert clean_title("Sailing - 2019 Remaster") == "Sailing"
    assert clean_title("More Than This (Official Video)") == "More Than This"
    assert clean_title("Teardrop [Official Music Video]") == "Teardrop"
    assert clean_title("Clint Eastwood (feat. Del the Funky Homosapien)") == "Clint Eastwood"
    assert clean_title("Smooth Operator (Single Version)") == "Smooth Operator"
    assert clean_title("Avalon (1999 Remaster)") == "Avalon"


def test_alias_beats_title():
    assert (
        M.match({"title": "whatever", "artist": "x", "path": "https://www.youtube.com/watch?v=0Rw-UsRPNqc"}) == "massive-attack--protection"
    )
    assert (
        M.match({"title": "?", "path": "spotify:track:1", "provider_meta": {"spotify_id": "4uLU6hMCjMI75M1A2tKUQC"}}) == "roisin-murphy--x"
    )
    assert M.match({"title": "02", "path": "/music/blue nile/02.flac"}) == "the-blue-nile--tinseltown-in-the-rain"


def test_messy_titles():
    cases = [
        ({"title": "Protection (Remastered 2006)", "artist": "Massive Attack - Topic"}, "massive-attack--protection"),
        ({"title": "Massive Attack - Unfinished Sympathy", "artist": "MassiveAttackVEVO"}, "massive-attack--unfinished-sympathy"),
        ({"title": "AIR - La femme d'argent (from Moon Safari - Official Audio)", "artist": "AIRfrench"}, "air--la-femme-dargent"),
        ({"title": "Texas Sun", "artist": "Khruangbin"}, "khruangbin-leon-bridges--texas-sun"),
        ({"title": "Sailing - 2019 Remaster", "artist": "Christopher Cross"}, "christopher-cross--sailing"),
        ({"title": "More Than This (1999 Remaster)", "artist": "Roxy Music"}, "roxy-music--more-than-this"),
        ({"title": "Tinseltown In The Rain", "artist": "Blue Nile"}, "the-blue-nile--tinseltown-in-the-rain"),
    ]
    for track, want in cases:
        assert M.match(track) == want, track


def test_no_false_match():
    assert M.match({"title": "Sailing", "artist": "Rod Stewart"}) is None
    assert M.match({"title": "Protection", "artist": "Everything but the Girl"}) is None
    assert M.match(None) is None
