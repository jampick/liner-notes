"""The method as files: six prompts with front matter, and one style.toml the prompts and check share."""

import pytest

from liner_notes import generate, method, paths

NAMES = ["song", "era", "heuristic", "vote", "front", "picture"]


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
    assert "song, era, heuristic, vote, front, picture" in str(e.value)
