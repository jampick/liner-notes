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
        ["not-x-its-y", "drop the contrast"],
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
    ("whether you're", "description", lambda j: j.update(description="Whether you're new to it or not, two songs."), ["whether-youre"]),
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
