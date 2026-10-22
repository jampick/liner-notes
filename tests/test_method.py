"""The method as files: six prompts with front matter, and one style.toml the prompts and check share."""

import pytest

from liner_notes import generate, method, paths

NAMES = ["song", "era", "heuristic", "vote", "front", "picture", "dates"]


@pytest.mark.parametrize("name", NAMES)
def test_every_prompt_loads_with_an_id_and_a_version(name):
    p = method.load_prompt(name)
    assert p.id == name
    assert isinstance(p.version, int) and p.version >= 1
    assert p.path == paths.PACKAGE / "prompts" / f"{name}.md"
    assert not p.text.startswith("+++") and "id =" not in p.text.split("\n", 1)[0]
    assert "\N{EM DASH}" not in p.text


@pytest.mark.parametrize("name", NAMES)
def test_every_prompt_carries_the_style_from_style_toml(name):
    """The banned list lives in one place, so a prompt can't drift from what check refuses."""
    style = method.load_style()
    p = method.load_prompt(name)
    assert "{style_rules}" not in p.text
    for w in style.banned:
        assert w in p.text, f"{name}.md doesn't name {w!r}"
    for c in style.constructions:
        assert c.say in p.text
    assert style.lyrics_say in p.text


def test_the_style_covers_the_owners_list():
    style = method.load_style()
    stock = "delve tapestry testament landscape navigate leverage robust seamless crucial vibrant".split()
    assert set(stock) <= set(style.banned)
    assert {"truly", "honestly", "genuinely", "simply"} <= set(style.banned)
    assert "Simply Red" in style.allowed
    assert {"em-dash", "not-x-its-y"} <= {c.id for c in style.constructions}
    assert isinstance(style.version, int)


def test_a_new_banned_word_reaches_the_prompt(tmp_path):
    src = (paths.PACKAGE / "style.toml").read_text().replace('"delve",', '"delve",\n  "groovesome",')
    f = tmp_path / "style.toml"
    f.write_text(src)
    style = method.parse_style(src, f)
    assert "groovesome" in method.load_prompt("song", style=style).text


def test_the_song_brief_is_song_md_and_has_the_lyrics_rule():
    assert not (paths.PACKAGE / "prompts" / "SONG-BRIEF.md").exists()
    p = method.load_prompt("song")
    assert "not even one quoted line" in p.text
    assert '"listen_for"' in p.text  # the reply shape generate parses
    assert generate.brief() == p.text


def test_render_fills_only_the_names_it_is_given():
    p = method.load_prompt("era")
    assert "inputs" in p.placeholders
    out = p.render(inputs="ERA INPUTS HERE")
    assert "ERA INPUTS HERE" in out and "{inputs}" not in out
    assert set(method.load_prompt("era").placeholders) - {"inputs"} <= set(method.PLACEHOLDER.findall(out))


def test_render_leaves_json_braces_alone():
    p = method.load_prompt("song")
    assert p.render(anything="x") == p.text
    assert '{"where": ' in p.text


BAD = [
    ("no front matter", "You write songs.\n", "starts with a line of +++"),
    ("unclosed front matter", '+++\nid = "song"\nversion = 1\nYou write.\n', "starts with a line of +++"),
    ("wrong id", '+++\nid = "era"\nversion = 1\n+++\nYou write.\n', 'id = "song"'),
    ("no version", '+++\nid = "song"\n+++\nYou write.\n', "version = <a whole number"),
    ("string version", '+++\nid = "song"\nversion = "1"\n+++\nYou write.\n', "version = <a whole number"),
    ("bad toml", "+++\nid = song\nversion = 1\n+++\nYou write.\n", "front matter isn't TOML"),
]


@pytest.mark.parametrize("what,text,says", BAD, ids=[b[0] for b in BAD])
def test_a_malformed_prompt_says_what_is_wrong(tmp_path, what, text, says):
    f = tmp_path / "song.md"
    with pytest.raises(method.MethodError) as e:
        method.parse_prompt(text, f)
    assert says in str(e.value) and str(f) in str(e.value)


def test_an_unknown_prompt_names_the_real_ones():
    with pytest.raises(method.MethodError) as e:
        method.load_prompt("essay")
    assert "song, era, heuristic, vote, front, picture, dates" in str(e.value)


def test_the_dates_brief_uses_the_dates_schema_field_names():
    """Item 5's dates schema reads songs[].song, year, source, confidence, url, note and corrected_from."""
    text = method.load_prompt("dates").text
    for name in ("`song`", "`year`", "`source`", "`confidence`", "`url`", "`note`", "`corrected_from`"):
        assert name in text, name
    for value in ('"musicbrainz"', '"writer"', '"person"', '"high"', '"medium"', '"low"', '"none"', "null"):
        assert value in text, value
    assert "{output_schema}" in text and "{inputs}" in text


# Fix round after the leads' reviews.


def test_load_style_sees_an_edit(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("LINER_NOTES_CONFIG", str(tmp_path / "config.toml"))
    f = method.user_style_path()
    f.write_text('[words]\nbanned = ["groovesome"]\n')
    assert "groovesome" in method.load_style().banned
    f.write_text('[words]\nbanned = ["funkadelicious"]\n')
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    style = method.load_style()
    assert "funkadelicious" in style.banned and "groovesome" not in style.banned


def test_liner_notes_prompts_points_at_another_folder(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("LINER_NOTES_PROMPTS", str(tmp_path))
    f = tmp_path / "era.md"
    f.write_text('+++\nid = "era"\nversion = 7\n+++\nTry this.\n\n{style_rules}\n\n{inputs}\n')
    p = method.load_prompt("era")
    assert p.version == 7 and p.path == f and "{style_rules}" not in p.text and "vibrant" in p.text
    f.write_text('+++\nid = "era"\nversion = 8\n+++\nAgain.\n')
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert method.load_prompt("era").version == 8
    # a brief the folder doesn't have comes from the package
    assert method.load_prompt("song").path == paths.PACKAGE / "prompts" / "song.md"


def test_era_brief_asks_for_one_era_and_the_panel_check():
    t = method.load_prompt("era").text
    assert "one era" in t
    for f in ("`panel_check`", "`song`", "`judge`", "`quote`", "`verdict`", "`note`", "`source`", '"holds"', '"wrong"', '"unconfirmed"'):
        assert f in t, f
    for f in ("`cities`", "`labels`", "`gear`", "`artifact`"):
        assert f in t, f


def test_vote_brief_matches_the_panel_format():
    t = method.load_prompt("vote").text
    assert "or veto" not in t and "`veto`" in t and "`by`" in t and "`why`" in t
    assert "gets checked later" not in t
    assert "Don't shade a vote" in t
    assert "seed list" in t or "playlist" in t
    assert "Fairweather" not in t and "Oyelaran" not in t


def test_picture_brief_allows_gear_names_not_lettering():
    t = method.load_prompt("picture").text
    assert "never brands" not in t and "lettering" in t
    assert "recording studio live room, 1979" not in t
    assert "It isn't a photograph" not in t


def test_dates_brief_keeps_an_unconfirmed_year():
    t = method.load_prompt("dates").text
    assert '`confidence` "low"' in t and "(or as it was)" not in t


def test_front_brief_leaves_credits_to_the_tool():
    t = method.load_prompt("front").text
    assert "`credits`" not in t and "`companion`" in t
