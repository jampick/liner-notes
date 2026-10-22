"""check applies style.toml to every text field we write, and refuses quoted lyrics.

Each case starts from the two-song test bank (helpers_build.make_bank), which passes check, breaks
one field and expects check to name the file, the field and the rule. The shipped bank must pass
with no style problem at all, so a rule that trips on it gets fixed before it ships.
"""

import pytest

from liner_notes import check, paths
from liner_notes.bank import Bank, read_json, write_json
from tests.helpers_build import iso, make_bank  # noqa: F401

EM = "\N{EM DASH}"
SONG = "songs/ann-artist--first-song.json"
JOURNEY = "journeys/test-trip/journey.json"


@pytest.fixture
def home(iso, monkeypatch):  # noqa: F811
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    make_bank(iso["home"])
    # make_bank's culture year has no source, which the schema wants; give it one so the bank starts clean
    edit(iso["home"] / "culture/years/1990.json", lambda y: y["items"][0].update(source="https://example.org/news"))
    return iso["home"]


def run_check():
    said = []
    code = check.check(log=lambda *a: said.append(" ".join(map(str, a))))
    return code, [x for x in said if x.startswith("problem:")]


def edit(path, change):
    d = read_json(path)
    change(d)
    write_json(path, d)


def test_the_test_bank_passes(home):
    assert run_check() == (0, [])


SONG_CASES = [
    # (what, field the message names, change to the song, words the message must carry)
    ("banned word", "notes.made", lambda s: s["notes"].update(made="A vibrant horn line opens it."), ["'vibrant'"]),
    (
        "inflected banned word",
        "culture[0].line",
        lambda s: s["culture"][0].update(line="It was navigating the charts for a year."),
        ["'navigating'"],
    ),
    ("banned word in a year note", "year_note", lambda s: s.update(year_note="The crucial date is the single."), ["'crucial'"]),
    ("em dash in the notes", "notes.context", lambda s: s["notes"].update(context=f"It charted {EM} twice."), ["em dash"]),
    ("em dash in a culture line", "culture[0].line", lambda s: s["culture"][0].update(line=f"It opened {EM} in May."), ["em dash"]),
    (
        "em dash in leads_to.why",
        "notes.leads_to.why",
        lambda s: s["notes"].update(leads_to={"artist": "X", "title": "Y", "year": 1980, "why": f"The source {EM} sampled."}),
        ["em dash"],
    ),
    (
        "quoted lyric",
        "notes.context",
        lambda s: s["notes"].update(context='In the last chorus she sings "I keep my heart in a paper bag" over the strings.'),
        ["lyric", "paper bag"],
    ),
    (
        "quoted lyric in single quotes",
        "notes.listen_for[0].note",
        lambda s: s["notes"].update(
            listen_for=[{"where": "the first verse", "note": "The opening line, 'we were dancing on the roof all night', sets it up."}]
        ),
        ["lyric"],
    ),
    (
        "it's not X, it's Y",
        "notes.made",
        lambda s: s["notes"].update(made="It's not a ballad, it's a slow burn."),
        ["it's-not-X-it's-Y framing", "drop the contrast"],
    ),
]


@pytest.mark.parametrize("what,field,change,words", SONG_CASES, ids=[c[0] for c in SONG_CASES])
def test_check_fails_a_song(home, what, field, change, words):
    f = home / SONG
    edit(f, change)
    code, probs = run_check()
    assert code == 1, what
    hit = [p for p in probs if f"{f}: {field}:" in p]
    assert hit, probs
    for w in words:
        assert w.lower() in hit[0].lower(), hit[0]


JOURNEY_CASES = [
    (
        "em dash in an era essay",
        "eras[0].essay",
        lambda j: j["eras"][0].update(essay=f"The scene moved south {EM} to London."),
        ["em dash"],
    ),
    ("banned word in an era essay", "eras[0].essay", lambda j: j["eras"][0].update(essay="A landscape of clubs."), ["'landscape'"]),
    ("banned word in the lede", "lede", lambda j: j.update(lede="A tapestry of records."), ["'tapestry'"]),
    ("em dash in the kicker", "kicker", lambda j: j.update(kicker=f"2 songs {EM} 1990"), ["em dash"]),
    ("em dash in front copy", "copy.credits", lambda j: j.update(copy={"credits": f"Picked {EM} by us."}), ["em dash"]),
    (
        "whether you're",
        "description",
        lambda j: j.update(description="Whether you're new to it or not, two songs."),
        ["whether-you're framing"],
    ),
    (
        "banned word in a panel quote",
        "panel.quotes.ann-artist--first-song.q",
        lambda j: j.update(panel={"quotes": {"ann-artist--first-song": {"j": "a", "q": "Robust drums."}}}),
        ["'robust'"],
    ),
    (
        "em dash in a judge's lens",
        "panel.judges.a.lens",
        lambda j: j.update(panel={"judges": {"a": {"name": "A", "title": "the Purist", "lens": f"Canon {EM} only."}}}),
        ["em dash"],
    ),
    (
        "banned word in a thread note",
        "threads[0].note",
        lambda j: j.update(
            threads=[{"from": "ann-artist--first-song", "to": "bob-band--second-song", "kind": "city", "note": "An iconic city."}]
        ),
        ["'iconic'"],
    ),
    (
        "quoted lyric in an era essay",
        "eras[0].essay",
        lambda j: j["eras"][0].update(essay='The chorus line "dancing on the roof all night" was everywhere that summer.'),
        ["lyric"],
    ),
]


@pytest.mark.parametrize("what,field,change,words", JOURNEY_CASES, ids=[c[0] for c in JOURNEY_CASES])
def test_check_fails_a_journey(home, what, field, change, words):
    f = home / JOURNEY
    edit(f, change)
    code, probs = run_check()
    assert code == 1, what
    hit = [p for p in probs if f"{f}: {field}:" in p]
    assert hit, probs
    for w in words:
        assert w.lower() in hit[0].lower(), hit[0]


def test_check_fails_a_culture_year(home):
    f = home / "culture/years/1990.json"
    edit(f, lambda y: y["items"][0].update(line=f"Things happened {EM} a lot."))
    code, probs = run_check()
    assert code == 1 and any(f"{f}: items[0].line: em dash" in p for p in probs), probs


PASSES = [
    ("an allowed name", lambda s: s["notes"].update(made="Mick Hucknall sang it before Simply Red had a deal.")),
    ("a banned word in a title", lambda s: s.update(title="Timeless")),
    (
        "a banned word as the lead's title",
        lambda s: s["notes"].update(leads_to={"artist": "X", "title": "Iconic", "year": 1980, "why": "Same room."}),
    ),
    (
        "a quoted song title next to a cue",
        lambda s: s["notes"].update(made="The hook is lifted from Bob James's \"You're as Right as Rain\" (1975)."),
    ),
    ("an interview quote with no cue", lambda s: s["notes"].update(made='He called the result "dorky, white disco funk" in an interview.')),
    ("a short quoted phrase", lambda s: s["notes"].update(made='The chorus repeats "hey, hey" twice.')),
    ("an en dash in a year range", lambda s: s.update(year_note="Recorded 1976\N{EN DASH}78.")),
    ("a not that isn't the contrast", lambda s: s["notes"].update(made="It was not until 1979 that the band, it was said, broke up.")),
]


@pytest.mark.parametrize("what,change", PASSES, ids=[c[0] for c in PASSES])
def test_check_passes(home, what, change):
    edit(home / SONG, change)
    assert run_check() == (0, []), what


def test_the_shipped_bank_has_no_style_problems(monkeypatch):
    """The rules hold on everything that ships, so a new rule can't land with the bank failing it."""
    monkeypatch.delenv("LINER_NOTES_HOME", raising=False)
    monkeypatch.setenv("LINER_NOTES_DATA", "/nonexistent-liner-notes-data")
    bank = Bank(roots=[paths.home()])
    found = check.style_check(bank)
    assert found == [], "\n".join(found[:20])
    assert len(bank.songs) > 200 and len(bank.journeys) >= 6


# Fix round after the leads' reviews.

BAR, EN = "\N{HORIZONTAL BAR}", "\N{EN DASH}"


def problems_in(text):
    from liner_notes.method import load_style

    return check.style_problems(text, load_style())


FAILS = [
    ("a horizontal bar", f"It came back {BAR} slower.", "em dash"),
    ("a spaced en dash", f"It came back {EN} slower.", "em dash"),
    ("two hyphens as a dash", "It came back -- slower.", "em dash"),
    ("an allowed name inside a longer word", "Simply Redundant drums.", "'Simply'"),
    ("Testament is no longer allowed", "Testament to the hours they put in.", "'Testament'"),
    ("an -ily form", "The legendarily loud drums.", "'legendarily'"),
    ("readable name for the contrast", "It's not a ballad, it's a slow burn.", "it's-not-X-it's-Y framing"),
    ("readable name for whether you're", "Whether you're new to it or not.", "whether-you're framing"),
]


@pytest.mark.parametrize("what,text,says", FAILS, ids=[c[0] for c in FAILS])
def test_the_style_catches(what, text, says):
    found = problems_in(text)
    assert any(says in p for p in found), found


PASSES_TOO = [
    ("an en dash in a range", f"Recorded 1976{EN}78."),
    ("a word inside a URL", "The label page is at https://example.org/iconic-records/seamless and says 1979."),
    ("a speech verb further back", "Rolling Stone wrote that the hook was 'too clever by half' in its review."),
    ("a speech verb close by", 'In the chorus, as Daly said, "we only had one take left" was the mood.'),
]


@pytest.mark.parametrize("what,text", PASSES_TOO, ids=[c[0] for c in PASSES_TOO])
def test_the_style_lets_through(what, text):
    assert problems_in(text) == []


def your_copy(home, iso):  # noqa: F811
    """The test song as your own copy in user data, where your style additions apply."""
    import shutil

    (iso["data"] / "songs").mkdir(parents=True, exist_ok=True)
    return shutil.copy(home / SONG, iso["data"] / SONG)


def test_a_name_hit_says_how_to_allow_it(home, iso):  # noqa: F811
    edit(your_copy(home, iso), lambda s: s["notes"].update(made="It came out on Robust Records in Leeds."))
    code, probs = run_check()
    from liner_notes.method import user_style_path

    assert code == 1 and probs, probs
    assert "if it's a name, add it to [names] allowed in" in probs[0] and str(user_style_path()) in probs[0], probs[0]


def test_a_lowercase_hit_doesnt_suggest_a_name(home):
    edit(home / SONG, lambda s: s["notes"].update(made="A robust mix."))
    _, probs = run_check()
    assert probs and "[names]" not in probs[0], probs[0]


def test_your_style_file_adds_words_and_names(home, iso):  # noqa: F811
    from liner_notes.method import user_style_path

    f = user_style_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('[words]\nbanned = ["groovesome"]\n\n[names]\nallowed = ["Robust Records"]\n')
    edit(your_copy(home, iso), lambda s: s["notes"].update(made="It came out on Robust Records, a groovesome label."))
    code, probs = run_check()
    assert code == 1 and len(probs) == 1 and "'groovesome'" in probs[0], probs


def test_your_style_file_cant_remove_a_house_rule(home):
    from liner_notes.method import MethodError, load_style, user_style_path

    f = user_style_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('[words]\nbanned = []\nsay = "anything goes"\n')
    with pytest.raises(MethodError) as e:
        load_style()
    assert str(f) in str(e.value) and "words.banned" in str(e.value)


def test_the_missed_lyrics_are_gone_from_the_shipped_files():
    """Four quoted lyrics the cue rule can't see, rewritten by hand. They stay out."""
    gone = {
        "songs/china-crisis--black-man-ray.json": "Yes, yes, I could be wrong",
        "songs/simply-red--holding-back-the-years.json": "Strangled by the wishes",
        "songs/spandau-ballet--true.json": "Marvin all night long",
        "journeys/yacht-rock/journey.json": "between the moon and New York City",
    }
    for rel, line in gone.items():
        assert line not in (paths.home() / rel).read_text(), rel
    assert "keep holding on" not in (paths.home() / "songs/simply-red--holding-back-the-years.json").read_text()
    review = paths.PACKAGE / "shipped/journeys/sophisti-pop/REVIEW.md"
    if review.exists():
        assert "Marvin all night long" not in review.read_text()


# Second fix round.

SUNG = [
    ("a singing cue beats a speech verb", "In an interview he said little, but in the chorus she sings 'between the moon and the city'."),
    ("a lyric cue with called", "Called the best thing on the record, the chorus goes 'we only had one take left' twice."),
]


@pytest.mark.parametrize("what,text", SUNG, ids=[c[0] for c in SUNG])
def test_a_sung_quote_is_a_lyric_whoever_spoke(what, text):
    assert any("quoted lyric" in p for p in problems_in(text)), what


def test_a_listen_for_note_is_read_with_its_where(home):
    edit(
        home / SONG,
        lambda s: s["notes"].update(
            listen_for=[{"where": "the last lines", "note": "'we were dancing on the roof all night' closes it out."}]
        ),
    )
    code, probs = run_check()
    assert code == 1 and any(f"{home / SONG}: notes.listen_for[0].note: quoted lyric" in p for p in probs), probs


def remix_journey(root):
    jdir = root / "journeys/remix-trip"
    jdir.mkdir(parents=True)
    write_json(
        jdir / "journey.json",
        {
            "slug": "remix-trip",
            "kind": "remix",
            "title": "Remix Trip",
            "description": "Two remixes.",
            "kicker": "2 remixes",
            "songs": ["ann-artist--first-song"],
            "remixes": [
                {
                    "title": "First Song (Club Mix)",
                    "song": "First Song",
                    "artist": "Ann Artist",
                    "path": "https://example.org/remix",
                    "id": "CCCCCCCCCCC",
                    "original": {"song": "ann-artist--first-song"},
                }
            ],
            "panel": {"quotes": {"First Song": {"who": "A, the Keeper", "quote": "Keeps the hook.", "votes": "5/5"}}},
        },
    )
    return jdir / "journey.json"


def test_a_remix_journey_is_checked(home):
    f = remix_journey(home)
    assert run_check() == (0, [])
    edit(f, lambda j: j["panel"]["quotes"]["First Song"].update(quote=f"Keeps the hook {EM} all of it."))
    code, probs = run_check()
    assert code == 1 and any(f"{f}: panel.quotes.First Song.quote: em dash" in p for p in probs), probs
    edit(f, lambda j: j.update(kicker="2 vibrant remixes"))
    assert any(f"{f}: kicker: banned word" in p for p in run_check()[1])


def test_your_banned_words_leave_the_shipped_bank_alone(iso, monkeypatch):  # noqa: F811
    """Shipped files are judged by the house style; your additions judge your own files."""
    from liner_notes.method import user_style_path

    monkeypatch.delenv("LINER_NOTES_HOME", raising=False)
    user_style_path().write_text('[words]\nbanned = ["soulful"]\n')
    shipped = check.style_check(Bank())
    assert shipped == [], shipped[:3]
    mine = iso["data"] / "songs/ann-artist--first-song.json"
    make_bank(iso["data"])
    edit(mine, lambda s: s["notes"].update(made="A soulful take."))
    found = check.style_check(Bank())
    assert len(found) == 1 and str(mine) in found[0] and "'soulful'" in found[0], found


@pytest.mark.parametrize("name", ["Vibrant", "vibrant", "\N{EM DASH}", "Big -- Band"])
def test_your_style_file_cant_allow_a_house_rule_away(name):
    from liner_notes.method import MethodError, load_style, user_style_path

    f = user_style_path()
    f.write_text(f'[names]\nallowed = ["{name}"]\n')
    with pytest.raises(MethodError) as e:
        load_style()
    assert str(f) in str(e.value) and "\n" not in str(e.value)


def test_your_style_file_can_allow_a_real_name():
    from liner_notes.method import load_style, user_style_path

    user_style_path().write_text('[names]\nallowed = ["Robust Records"]\n')
    assert "Robust Records" in load_style().allowed


def test_a_broken_style_file_is_one_line_from_check(home):
    from liner_notes.method import user_style_path

    f = user_style_path()
    f.write_text("[words\nbanned = ")
    said = []
    code = check.check(log=lambda *a: said.append(" ".join(map(str, a))))
    assert code == 2 and len(said) == 1 and str(f) in said[0] and "\n" not in said[0], said


def test_tests_never_read_your_real_style_file():
    from pathlib import Path

    from liner_notes.method import user_style_path

    assert not user_style_path().is_relative_to(Path.home() / ".config"), user_style_path()


LEFTOVERS = {
    "songs/steely-dan--hey-nineteen.json": "The Cuervo Gold, the fine Colombian, make tonight",
    "songs/toto--rosanna.json": "meet you all the way",
    "journeys/yacht-rock/journey.json": "such a long way to go",
    "journeys/yacht-house/journey.json": "and I can't let go",
}


def test_the_leftover_lyrics_are_gone_too():
    for rel, line in LEFTOVERS.items():
        assert line not in (paths.home() / rel).read_text(), rel
