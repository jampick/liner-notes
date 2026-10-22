"""Stage files and staleness (phase 1 item 5): a journey in the making is a work folder of artefacts,
journey.toml keeps the hash of every stage's inputs and of its signed-off output, and status says
what's stale or edited without rerunning anything. Everything goes through the CLI, the way the
console will."""

import fcntl
import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from liner_notes import cli, paths
from liner_notes.bank import Bank, problems, read_json, write_json
from tests.helpers_build import iso, make_bank, song  # noqa: F401

FIX = Path(__file__).parent / "fixtures" / "workflow"
SLUG = "small-trip"
STAGES = ["heuristic", "panel", "dates", "eras", "front"]
FILES = {"heuristic": "heuristic.md", "panel": "panel.json", "dates": "dates.json", "eras": "eras.json", "front": "front.json"}
PICKS = ["ann-artist--first-song", "bob-band--second-song"]
MORE = [("cat-crew--third-song", "Third Song", "Cat Crew", 1990), ("dee-duo--fourth-song", "Fourth Song", "Dee Duo", 1991)]
MORE += [("eve-echo--fifth-song", "Fifth Song", "Eve Echo", 1990), ("fay-five--sixth-song", "Sixth Song", "Fay Five", 1991)]


@pytest.fixture
def ws(iso, tmp_path, monkeypatch):
    """A shipped bank with the fixture songs, our own prompts folder, and no Anthropic SDK to import."""
    make_bank(iso["home"])
    for sid, title, artist, year in MORE:
        write_json(iso["home"] / "songs" / f"{sid}.json", song(sid, title, artist, year, "CCCCCCCCCCC"))
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    prompts = tmp_path / "prompts"
    shutil.copytree(FIX / "prompts", prompts)
    monkeypatch.setenv("LINER_NOTES_PROMPTS", str(prompts))
    monkeypatch.setitem(sys.modules, "anthropic", None)  # nothing here may create a client
    return {"dir": iso["data"] / "work" / SLUG, "prompts": prompts, **iso}


def run(*argv):
    return cli.main([str(a) for a in argv])


def out_json(capsys, *argv, rc=0):
    capsys.readouterr()
    try:
        got = run(*argv, "--json")
    except SystemExit as e:
        got = e.code
    assert got == rc
    return json.loads(capsys.readouterr().out)


def status(capsys, slug=SLUG):
    return out_json(capsys, "status", slug)


def states(capsys):
    return {s["name"]: s for s in status(capsys)["stages"]}


def write(p, obj):
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def edit_json(p, fn):
    d = read_json(p)
    fn(d)
    write(p, d)


def touch_later(p):
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def bump_brief(ws, name, old="version = 1", new="version = 2"):
    p = ws["prompts"] / f"{name}.md"
    p.write_text(p.read_text().replace(old, new))
    touch_later(p)


def eras_skeleton(ws):
    """The person's part of eras.json: names and bounds, and the era's picture."""
    shutil.copy(FIX / "eras.json", ws["dir"] / "eras.json")
    (ws["dir"] / "media/eras").mkdir(parents=True, exist_ok=True)
    (ws["dir"] / "media/eras/era1.webp").write_bytes(b"RIFF0000WEBPVP8 ")


def fill(ws, capsys, upto=None):
    """Make the journey and take every stage through prompt, reply and accept, as a paste would."""
    assert run("new", "Small Trip") == 0
    for name in STAGES:
        if name == "eras":
            eras_skeleton(ws)
            assert run("prompt", SLUG, "eras", "--era", 1) == 0
            assert run("reply", SLUG, "eras", FIX / "era-1.json", "--era", 1) == 0
        else:
            assert run("prompt", SLUG, name) == 0
            assert run("reply", SLUG, name, FIX / FILES[name]) == 0
        assert run("accept", SLUG, name) == 0
        if name == upto:
            break
    capsys.readouterr()


def refused(*argv):
    with pytest.raises(SystemExit) as e:
        run(*argv)
    return str(e.value)


# new


def test_new_makes_a_work_folder_with_the_rule(ws, capsys):
    assert run("new", "Small Trip") == 0
    t = tomllib.loads((ws["dir"] / "journey.toml").read_text())
    assert t["slug"] == SLUG and t["panel"] is True and t["rule"] == {"yes": 4, "of": 5, "veto": True}
    st = status(capsys)
    assert [s["name"] for s in st["stages"]] == STAGES
    assert {s["state"] for s in st["stages"]} == {"not started"}
    assert st["rule"] == {"yes": 4, "of": 5, "veto": True}


def test_new_prints_a_next_command_that_works(ws, capsys):
    assert run("new", "Small Trip") == 0
    line = capsys.readouterr().out
    cmd = line.split("Next: liner-notes ", 1)[1].split(",")[0].split()
    assert cmd[0] == "prompt" and run(*cmd) == 0


def test_new_refuses_a_journey_that_exists(ws, capsys):
    assert run("new", "Small Trip") == 0
    assert "already" in refused("new", "Small Trip")
    assert "already" in refused("new", "Test Trip")  # the shipped fixture journey


M3U = "#EXTM3U\n#EXTINF:-1,Ann Artist - First Song\nhttps://example.org/1\n#EXTINF:-1,Bob Band - Second Song (Remastered)\nhttps://example.org/2\n"


def test_a_playlist_gets_no_panel_unless_asked(ws, tmp_path, capsys):
    m3u = tmp_path / "boogie.m3u"  # made here: the publish check doesn't carry .m3u files in the repo
    m3u.write_text(M3U)
    assert run("new", "Boogie Down", "--from", m3u) == 0
    st = status(capsys, "boogie-down")
    assert st["panel"] is False and st["rule"] is None
    assert [s["name"] for s in st["stages"]] == ["heuristic", "dates", "eras", "front"]
    assert st["songs"] == PICKS
    assert run("new", "Boogie Panel", "--from", m3u, "--panel") == 0
    assert "panel" in [s["name"] for s in status(capsys, "boogie-panel")["stages"]]


def test_new_from_a_missing_file_says_so(ws, tmp_path, capsys):
    said = refused("new", "Nope", "--from", tmp_path / "nope.m3u")
    assert "can't find" in said and "nope.m3u" in said and "cliamp" not in said


def test_a_title_with_control_characters_round_trips(ws, capsys):
    title = 'Bad\x7fTitle "with" a \\ and\ta tab, ünïcode'
    assert run("new", title, "--slug", "bad") == 0
    assert status(capsys, "bad")["title"] == title


# prompt


def test_prompt_fills_the_brief_with_inputs_and_a_readable_field_list(ws, capsys):
    fill(ws, capsys, upto="dates")
    eras_skeleton(ws)
    assert run("prompt", SLUG, "eras", "--era", 1) == 0
    out = capsys.readouterr().out
    assert "Fixture brief for the era stage" in out
    assert "{inputs}" not in out and "{output_schema}" not in out and "{style_rules}" not in out
    assert "In: recordings from 1990 and 1991" in out and "The small years" in out and "First Song" in out
    assert "Everyone in the room sings the chorus." in out and "ros" in out  # the quote to check, with its judge
    assert "essay" in out and "cities" in out and "panel_check" in out
    assert "$id" not in out and "$schema" not in out
    assert "'Small Trip'" not in out and "Small Trip" in out


def test_the_dates_prompt_uses_the_dates_brief(ws, capsys):
    fill(ws, capsys, upto="panel")
    assert run("prompt", SLUG, "dates") == 0
    out = capsys.readouterr().out
    assert "Fixture brief for the dates stage" in out and "Second Song" in out and "1991" in out


def test_prompt_refuses_while_an_input_is_missing(ws, capsys):
    assert run("new", "Small Trip") == 0
    eras_skeleton(ws)
    assert "dates.json" in refused("prompt", SLUG, "eras", "--era", 1)


# reply


def test_a_reply_is_a_draft_until_someone_accepts_it(ws, capsys):
    assert run("new", "Small Trip") == 0
    assert run("prompt", SLUG, "heuristic") == 0
    assert run("reply", SLUG, "heuristic", FIX / "heuristic.md") == 0
    st = states(capsys)["heuristic"]
    assert st["state"] == "draft" and st["by"] == "reply" and f"liner-notes accept {SLUG} heuristic" in st["next"]
    assert run("accept", SLUG, "heuristic") == 0
    st = states(capsys)["heuristic"]
    assert st["state"] == "accepted" and st["by"] == "accept"


def test_reply_takes_the_one_fenced_block_out_of_prose(ws, tmp_path, capsys):
    fill(ws, capsys, upto="heuristic")
    r = tmp_path / "reply.txt"
    r.write_text("Here's the panel after a lot of reading.\n\n```json\n" + (FIX / "panel.json").read_text() + "```\n")
    assert run("reply", SLUG, "panel", r) == 0
    assert read_json(ws["dir"] / "panel.json")["judges"]["pia"]["name"] == "Pia Hartnell"


@pytest.mark.parametrize(
    "text, said",
    [
        ('```json\n{"a": 1}\n```\nand\n```json\n{"b": 2}\n```', "2 fenced blocks"),
        ('{"public": false}\nThanks, hope that helps!', "text around the JSON"),
        ('{"public": false, "public": true}', "public"),
    ],
)
def test_a_reply_that_cant_be_read_says_why_in_words(ws, tmp_path, capsys, text, said):
    fill(ws, capsys, upto="heuristic")
    r = tmp_path / "reply.txt"
    r.write_text(text)
    msg = refused("reply", SLUG, "panel", r)
    assert said in msg and "Expecting" not in msg and "Extra data" not in msg


def test_a_reply_saved_with_a_byte_order_mark_is_read(ws, tmp_path, capsys):
    fill(ws, capsys, upto="heuristic")
    r = tmp_path / "reply.json"
    r.write_bytes(b"\xef\xbb\xbf" + (FIX / "panel.json").read_bytes())
    assert run("reply", SLUG, "panel", r) == 0


def test_a_bad_reply_names_the_field_in_json_and_writes_nothing(ws, tmp_path, capsys):
    fill(ws, capsys, upto="dates")
    eras_skeleton(ws)
    toml_before = (ws["dir"] / "journey.toml").read_bytes()
    eras_before = (ws["dir"] / "eras.json").read_bytes()
    bad = read_json(FIX / "era-1.json")
    del bad["essay"]
    bad["scene"] = "Leeds"
    r = tmp_path / "reply.json"
    write(r, bad)
    got = out_json(capsys, "reply", SLUG, "eras", r, "--era", 1, rc=1)
    fields = {e["field"] for e in got["errors"]}
    assert got["ok"] is False and "$.essay" in fields and "$.scene" in fields
    assert (ws["dir"] / "journey.toml").read_bytes() == toml_before
    assert (ws["dir"] / "eras.json").read_bytes() == eras_before


@pytest.mark.parametrize(
    "stage, fn, field",
    [
        ("eras", lambda d: d.update(essay="A vibrant room above a shop."), "$.essay"),
        (
            "panel",
            lambda d: d["candidates"][0]["votes"]["pia"].update(why="The original take \u2014 no question."),
            "$.candidates[0].votes.pia.why",
        ),
    ],
)
def test_a_reply_that_breaks_the_house_style_is_refused_by_field(ws, tmp_path, capsys, stage, fn, field):
    fill(ws, capsys, upto="dates" if stage == "eras" else "heuristic")
    d = read_json(FIX / ("era-1.json" if stage == "eras" else "panel.json"))
    fn(d)
    r = tmp_path / "reply.json"
    write(r, d)
    if stage == "eras":
        eras_skeleton(ws)
        msg = refused("reply", SLUG, stage, r, "--era", 1)
    else:
        msg = refused("reply", SLUG, stage, r)
    assert field in msg


def test_an_era_reply_fills_one_era_and_cant_move_its_bounds(ws, tmp_path, capsys):
    fill(ws, capsys, upto="dates")
    eras_skeleton(ws)
    edit_json(ws["dir"] / "eras.json", lambda d: d["eras"].append({"name": "Later", "from": 1992, "to": 1993}))
    assert run("reply", SLUG, "eras", FIX / "era-1.json", "--era", 1) == 0
    eras = read_json(ws["dir"] / "eras.json")["eras"]
    assert eras[0]["scene"]["cities"] == ["Leeds"] and eras[0]["from"] == 1990 and eras[0]["name"] == "The small years"
    assert eras[1] == {"name": "Later", "from": 1992, "to": 1993}
    r = tmp_path / "moved.json"
    write(r, read_json(FIX / "era-1.json") | {"from": 1985})
    assert "$.from" in refused("reply", SLUG, "eras", r, "--era", 2)
    assert read_json(ws["dir"] / "eras.json")["eras"][1] == {"name": "Later", "from": 1992, "to": 1993}


def test_an_era_reply_checks_its_panel_check(ws, tmp_path, capsys):
    fill(ws, capsys, upto="dates")
    eras_skeleton(ws)
    bad = read_json(FIX / "era-1.json")
    bad["panel_check"][0]["verdict"] = "fine"
    r = tmp_path / "reply.json"
    write(r, bad)
    assert "$.panel_check[0].verdict" in refused("reply", SLUG, "eras", r, "--era", 1)


def test_reply_refuses_while_an_input_is_missing(ws, capsys):
    assert run("new", "Small Trip") == 0
    assert "panel" in refused("reply", SLUG, "dates", FIX / "dates.json")
    assert not (ws["dir"] / "dates.json").exists()


# status


def test_a_filled_journey_is_all_accepted_and_ready(ws, capsys):
    fill(ws, capsys)
    st = status(capsys)
    assert {s["name"]: s["state"] for s in st["stages"]} == dict.fromkeys(STAGES, "accepted")
    assert st["ready"] is True
    t = tomllib.loads((ws["dir"] / "journey.toml").read_text())
    assert set(t["stages"]["eras"]["inputs"]) == {"dates.json", "heuristic.md"}
    assert t["stages"]["eras"]["prompt"]["id"] == "era" and len(t["stages"]["eras"]["prompt"]["hash"]) == 64
    assert "rendered" not in t["stages"]["eras"]


def test_changing_a_songs_year_makes_eras_stale(ws, capsys):
    fill(ws, capsys)
    edit_json(ws["dir"] / "dates.json", lambda d: d["songs"][1].update(year=1992))
    st = states(capsys)
    assert st["eras"]["state"] == "stale" and st["eras"]["stale"] == ["dates.json changed"]
    assert f"liner-notes accept {SLUG} eras" in st["eras"]["next"]
    assert st["dates"]["state"] == "edited"
    assert st["heuristic"]["state"] == st["panel"]["state"] == st["front"]["state"] == "accepted"


def test_editing_the_heuristic_makes_the_panel_stale(ws, capsys):
    fill(ws, capsys)
    with open(ws["dir"] / "heuristic.md", "a") as f:
        f.write("Also out: anything over six minutes.\n")
    st = states(capsys)
    assert st["panel"]["state"] == "stale" and "heuristic.md changed" in st["panel"]["stale"]
    assert st["eras"]["state"] == "stale"
    assert st["heuristic"]["state"] == "edited" and st["dates"]["state"] == "accepted"


def test_editing_eras_makes_front_stale_and_stale_beats_edited(ws, capsys):
    fill(ws, capsys)
    edit_json(ws["dir"] / "eras.json", lambda d: d["eras"][0].update(essay="A shorter essay."))
    assert states(capsys)["front"]["stale"] == ["eras.json changed"]
    edit_json(ws["dir"] / "dates.json", lambda d: d["songs"][1].update(note="Checked again."))
    st = states(capsys)["eras"]
    assert st["state"] == "stale" and st["edited"] is True


def test_changing_the_picks_makes_dates_stale(ws, capsys):
    fill(ws, capsys)
    edit_json(ws["dir"] / "panel.json", lambda p: p["candidates"][1]["votes"]["pia"].update(vote="no"))
    st = states(capsys)
    assert st["dates"]["state"] == "stale" and st["dates"]["stale"] == ["the song list changed"]


def test_a_song_year_fixed_in_the_bank_makes_dates_stale(ws, capsys):
    fill(ws, capsys)
    f = ws["home"] / "songs" / "bob-band--second-song.json"
    edit_json(f, lambda s: s.update(year=1992, year_source="person"))
    assert states(capsys)["dates"]["stale"] == ["a song's year in the bank changed"]


def test_editing_an_output_after_sign_off_shows_edited(ws, capsys):
    fill(ws, capsys)
    edit_json(ws["dir"] / "front.json", lambda d: d.update(lede="A different lede, typed in by hand."))
    st = states(capsys)
    assert st["front"]["state"] == "edited" and st["front"]["edited"] is True and st["front"]["stale"] == []
    assert all(st[n]["state"] == "accepted" for n in STAGES if n != "front")


def test_a_file_that_doesnt_parse_is_broken_not_draft(ws, capsys):
    fill(ws, capsys, upto="heuristic")
    (ws["dir"] / "panel.json").write_text("{ not json")
    st = states(capsys)["panel"]
    assert st["state"] == "broken" and st["problems"]


def test_accept_clears_stale_without_rerunning_and_nothing_reruns_by_itself(ws, capsys):
    fill(ws, capsys)
    edit_json(ws["dir"] / "dates.json", lambda d: d["songs"][1].update(year=1991, note="Looked again."))
    before = {n: (ws["dir"] / FILES[n]).read_bytes() for n in STAGES}
    for _ in range(2):
        assert states(capsys)["eras"]["state"] == "stale"
    assert {n: (ws["dir"] / FILES[n]).read_bytes() for n in STAGES} == before
    assert run("accept", SLUG, "eras") == 0
    assert run("accept", SLUG, "dates") == 0
    assert {n: (ws["dir"] / FILES[n]).read_bytes() for n in STAGES} == before
    assert {s["name"]: s["state"] for s in status(capsys)["stages"]} == dict.fromkeys(STAGES, "accepted")


def test_one_broken_journey_toml_doesnt_hide_the_others(ws, capsys):
    fill(ws, capsys, upto="heuristic")
    bad = ws["data"] / "work" / "bad"
    bad.mkdir()
    (bad / "journey.toml").write_text("this isn't = = toml")
    got = out_json(capsys, "status")
    by = {j["slug"]: j for j in got["journeys"]}
    assert by[SLUG]["stages"][0]["state"] == "accepted" and "error" in by["bad"]


# staleness from the prompt and the brief


def test_an_input_changed_between_prompt_and_reply_shows_stale(ws, capsys):
    fill(ws, capsys, upto="dates")
    eras_skeleton(ws)
    assert run("prompt", SLUG, "eras", "--era", 1) == 0
    with open(ws["dir"] / "heuristic.md", "a") as f:
        f.write("One more rule.\n")
    assert run("accept", SLUG, "heuristic") == 0
    assert run("reply", SLUG, "eras", FIX / "era-1.json", "--era", 1) == 0
    assert states(capsys)["eras"]["stale"] == ["heuristic.md changed"]


def test_a_new_brief_version_makes_its_stage_stale(ws, capsys):
    fill(ws, capsys)
    bump_brief(ws, "era")
    st = states(capsys)
    assert st["eras"]["state"] == "stale" and st["eras"]["stale"] == ["the era brief changed (version 1, now 2)"]
    assert st["front"]["state"] == "accepted"


def test_a_changed_brief_text_with_the_same_version_makes_its_stage_stale(ws, capsys):
    fill(ws, capsys)
    bump_brief(ws, "front", "Fixture brief", "A reworded fixture brief")
    st = states(capsys)["front"]
    assert st["state"] == "stale" and "front brief" in st["stale"][0] and "text" in st["stale"][0]


def test_a_reply_without_a_fresh_prompt_records_the_brief_as_it_is_now(ws, capsys):
    fill(ws, capsys)
    bump_brief(ws, "era")
    assert run("reply", SLUG, "eras", FIX / "era-1.json", "--era", 1) == 0
    st = states(capsys)["eras"]
    assert st["stale"] == [] and st["state"] == "draft"


# the panel


def test_four_yes_votes_and_a_veto_make_a_close_call_not_a_pick(ws, capsys):
    fill(ws, capsys)
    st = status(capsys)
    assert st["picks"] == PICKS
    assert st["close_calls"] == ["cat-crew--third-song", "dee-duo--fourth-song"]
    f = ws["dir"] / "panel.json"
    assert "picks" not in read_json(f) and "close_calls" not in read_json(f)
    edit_json(f, lambda p: p["candidates"][2].pop("veto"))
    assert status(capsys)["picks"] == [*PICKS, "cat-crew--third-song"]
    assert "picks" not in tomllib.loads((ws["dir"] / "journey.toml").read_text())


def test_two_yes_votes_are_a_cut_not_a_close_call(ws, capsys):
    fill(ws, capsys)

    def two(p):
        c = json.loads(json.dumps(p["candidates"][4]))
        c.update(song="fay-five--sixth-song", title="Sixth Song", artist="Fay Five")
        c["votes"]["dov"]["vote"] = "yes"
        p["candidates"].append(c)

    edit_json(ws["dir"] / "panel.json", two)
    st = status(capsys)
    assert "fay-five--sixth-song" not in st["close_calls"] and "fay-five--sixth-song" not in st["picks"]


def test_overrides_beat_the_count_and_show_with_their_reason(ws, capsys):
    fill(ws, capsys)

    def over(p):
        p["candidates"][3]["override"] = {"to": "pick", "why": "The owner wants the opener."}
        p["candidates"][0]["override"] = {"to": "cut", "why": "Too familiar."}

    edit_json(ws["dir"] / "panel.json", over)
    st = status(capsys)
    assert st["picks"] == ["bob-band--second-song", "dee-duo--fourth-song"]
    assert {o["song"]: (o["to"], o["why"]) for o in st["overrides"]} == {
        "dee-duo--fourth-song": ("pick", "The owner wants the opener."),
        "ann-artist--first-song": ("cut", "Too familiar."),
    }


def test_the_rule_is_the_journeys_and_a_reply_cant_change_it(ws, tmp_path, capsys):
    assert run("new", "Small Trip", "--rule", "3/5") == 0
    assert status(capsys)["rule"] == {"yes": 3, "of": 5, "veto": True}
    run("reply", SLUG, "heuristic", FIX / "heuristic.md")
    assert run("reply", SLUG, "panel", FIX / "panel.json") == 0
    assert "dee-duo--fourth-song" in status(capsys)["picks"]  # 3 yes votes pass a 3 of 5 rule
    r = tmp_path / "reply.json"
    write(r, read_json(FIX / "panel.json") | {"rule": {"yes": 1, "of": 5}})
    assert "$.rule" in refused("reply", SLUG, "panel", r)


def _drop_judge(p):
    p["judges"].pop("ros")
    for c in p["candidates"]:
        c["votes"].pop("ros")


@pytest.mark.parametrize(
    "fn, field",
    [
        (lambda p: p["candidates"][0]["votes"].update(zed={"vote": "yes", "why": "Me too."}), "$.candidates[0].votes.zed"),
        (lambda p: p["candidates"][1]["votes"].pop("dov"), "$.candidates[1].votes.dov"),
        (lambda p: p["candidates"][1]["votes"]["ula"].update(why=" "), "$.candidates[1].votes.ula.why"),
        (lambda p: p["judges"].update(pia="Pia"), "$.judges.pia"),
        (lambda p: p["judges"]["dov"].update(name="Pia Hartnell"), "$.judges.dov.name"),
        (_drop_judge, "$.judges"),
        (lambda p: p["candidates"][2].update(veto={"by": "dov", "why": "No."}), "$.candidates[2].veto.by"),
        (lambda p: p["candidates"][4].update(song="", title="Nobody Knows", artist="No Band"), "$.candidates[4].song"),
    ],
)
def test_a_panel_reply_that_breaks_the_rules_is_refused_by_field(ws, tmp_path, capsys, fn, field):
    fill(ws, capsys, upto="heuristic")
    p = read_json(FIX / "panel.json")
    fn(p)
    r = tmp_path / "reply.json"
    write(r, p)
    assert field in refused("reply", SLUG, "panel", r)
    assert not (ws["dir"] / "panel.json").exists()


def test_a_panel_reply_can_name_songs_by_title_and_artist(ws, tmp_path, capsys):
    fill(ws, capsys, upto="heuristic")
    p = read_json(FIX / "panel.json")
    del p["candidates"][1]["song"]
    r = tmp_path / "reply.json"
    write(r, p)
    assert run("reply", SLUG, "panel", r) == 0
    assert read_json(ws["dir"] / "panel.json")["candidates"][1]["song"] == "bob-band--second-song"


# dates


def test_dates_rows_must_match_the_journeys_songs(ws, tmp_path, capsys):
    fill(ws, capsys, upto="panel")
    d = read_json(FIX / "dates.json")
    d["songs"][1]["song"] = "cat-crew--third-song"
    r = tmp_path / "reply.json"
    write(r, d)
    msg = refused("reply", SLUG, "dates", r)
    assert "$.songs[1].song" in msg and "bob-band--second-song" in msg


def _corrected_dates(tmp_path):
    d = read_json(FIX / "dates.json")
    d["songs"][0].update(year=1991, source="writer", corrected_from=1990, url="https://example.org/b", note="The single came first.")
    r = tmp_path / "dates.json"
    write(r, d)
    return r


def test_reply_and_accept_never_write_the_bank(ws, tmp_path, capsys):
    fill(ws, capsys, upto="panel")
    shipped = ws["home"] / "songs" / "ann-artist--first-song.json"
    before = shipped.read_bytes()
    assert run("reply", SLUG, "dates", _corrected_dates(tmp_path)) == 0
    assert run("accept", SLUG, "dates") == 0
    assert shipped.read_bytes() == before and not (ws["data"] / "songs").exists()


def test_assemble_refuses_a_year_the_bank_disagrees_with_even_forced(ws, tmp_path, capsys):
    fill(ws, capsys)
    assert run("reply", SLUG, "dates", _corrected_dates(tmp_path)) == 0
    assert run("accept", SLUG, "dates") == 0
    assert status(capsys)["ready"] is False
    msg = refused("assemble", SLUG, "--force")
    assert "dates-apply" in msg and "1991" in msg
    assert not (ws["dir"] / "journey.json").exists()


def test_dates_apply_writes_your_copy_of_the_song_and_names_the_other_journeys(ws, tmp_path, capsys):
    fill(ws, capsys)
    shipped = ws["home"] / "songs" / "ann-artist--first-song.json"
    before = shipped.read_bytes()
    assert run("reply", SLUG, "dates", _corrected_dates(tmp_path)) == 0
    assert run("accept", SLUG, "dates") == 0
    capsys.readouterr()
    assert run("dates-apply", SLUG) == 0
    assert "test-trip" in capsys.readouterr().out  # the shipped fixture journey has the song too
    mine = read_json(ws["data"] / "songs" / "ann-artist--first-song.json")
    assert (mine["year"], mine["year_source"], mine["year_corrected_from"]) == (1991, "writer", 1990)
    assert shipped.read_bytes() == before
    assert states(capsys)["dates"]["state"] == "accepted"
    assert run("accept", SLUG, "eras") == 0  # eras read the old dates.json
    assert run("assemble", SLUG) == 0


# assemble and build


def test_assemble_writes_a_journey_the_page_reads_and_only_build_makes_it_live(ws, tmp_path, capsys):
    fill(ws, capsys)
    assert run("assemble", SLUG) == 0
    j = read_json(ws["dir"] / "journey.json")
    assert problems(j, "journey") == []
    assert j["songs"] == PICKS
    assert set(j["panel"]["quotes"]) == set(j["songs"])  # close calls stay out of the journey
    assert j["panel"]["quotes"]["ann-artist--first-song"] == {"j": "ros", "q": "Everyone in the room sings the chorus."}
    assert j["eras"][0]["n"] == 1 and j["eras"][0]["years"] == "1990–91"
    assert j["eras"][0]["scene"]["cities"] == ["Leeds"]
    # Not live yet: list and the bank don't see it.
    assert SLUG not in Bank().journeys
    capsys.readouterr()
    run("list")
    assert SLUG not in capsys.readouterr().out
    page = tmp_path / "page.html"
    assert run("build", SLUG, "--public", "--out", page) == 0
    assert SLUG in Bank().journeys and (ws["data"] / "journeys" / SLUG / "media/eras/era1.webp").exists()
    data = json.loads(page.read_text().split("window.JOURNEY = ", 1)[1].split(";</script>", 1)[0])
    assert [s["title"] for s in data["stops"]] == ["First Song", "Second Song"]
    assert data["express"]["n"] == [1] and data["stops"][0]["threads"][0]["to"] == 2
    assert data["eras"][0]["scene"]["cities"] == ["Leeds"]


def test_assemble_refuses_until_signed_off_and_force_skips_only_that(ws, capsys):
    fill(ws, capsys, upto="eras")
    assert run("reply", SLUG, "front", FIX / "front.json") == 0
    assert f"liner-notes accept {SLUG} front" in refused("assemble", SLUG)
    got = out_json(capsys, "assemble", SLUG, "--force")
    assert got["ok"] is True and got["skipped"] == ["front"]


def test_force_never_assembles_an_empty_stage(ws, capsys):
    fill(ws, capsys, upto="eras")
    msg = refused("assemble", SLUG, "--force")
    assert "front" in msg and "not started" in msg
    assert not (ws["dir"] / "journey.json").exists()


@pytest.mark.parametrize(
    "fn, said",
    [
        (lambda d: d["eras"][0].update({"from": 1991, "to": 1990}), "1991"),
        (lambda d: d["eras"][0].update({"to": 1990}), "Second Song"),
        (lambda d: d["eras"][0].pop("essay"), "essay"),
    ],
)
def test_force_never_assembles_broken_eras(ws, capsys, fn, said):
    fill(ws, capsys)
    edit_json(ws["dir"] / "eras.json", fn)
    assert run("accept", SLUG, "eras") == 0
    assert run("accept", SLUG, "front") == 0
    assert status(capsys)["ready"] is False
    assert said in refused("assemble", SLUG, "--force")


def test_assemble_refuses_while_a_stage_is_stale(ws, capsys):
    fill(ws, capsys)
    with open(ws["dir"] / "heuristic.md", "a") as f:
        f.write("One more rule.\n")
    msg = refused("assemble", SLUG)
    assert "panel" in msg and "stale" in msg
    assert not (ws["dir"] / "journey.json").exists()


# scripting and robustness


def test_every_command_answers_in_json(ws, capsys):
    got = out_json(capsys, "new", "Small Trip")
    assert got["ok"] is True and got["slug"] == SLUG and "next" in got
    got = out_json(capsys, "prompt", SLUG, "heuristic")
    assert got["ok"] is True and "Fixture brief" in got["text"]
    got = out_json(capsys, "reply", SLUG, "heuristic", FIX / "heuristic.md")
    assert got["ok"] is True and got["state"] == "draft"
    got = out_json(capsys, "accept", SLUG, "heuristic")
    assert got["ok"] is True and got["state"] == "accepted"
    got = out_json(capsys, "assemble", SLUG, rc=1)
    assert got["ok"] is False and got["errors"][0]["message"]
    got = out_json(capsys, "dates-apply", SLUG, rc=1)
    assert got["ok"] is False


def test_journey_toml_is_locked_while_a_command_changes_it(ws, capsys):
    from liner_notes.workflow import journeytoml

    assert run("new", "Small Trip") == 0
    with journeytoml.editing(SLUG) as doc:
        assert doc["slug"] == SLUG
        with open(ws["dir"] / ".journey.toml.lock", "a") as f, pytest.raises(BlockingIOError):
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_no_journey_in_the_making_shows_in_the_bank(ws, capsys):
    fill(ws, capsys)
    assert paths.user_data() / "work" / SLUG == ws["dir"]
    assert SLUG not in Bank().journeys


def test_a_build_that_refuses_the_page_leaves_the_journey_off_the_bank(ws, tmp_path, capsys):
    fill(ws, capsys)
    assert run("assemble", SLUG) == 0
    (ws["dir"] / "media/eras/era1.webp").unlink()  # the page refuses an era with no picture
    assert run("build", SLUG, "--public", "--out", tmp_path / "page.html") == 1
    assert SLUG not in Bank().journeys


def test_every_stage_renders_from_the_shipped_briefs_with_nothing_left_unfilled(ws, monkeypatch, capsys):
    monkeypatch.delenv("LINER_NOTES_PROMPTS")
    fill(ws, capsys)
    for name in STAGES:
        args = ("--era", 1) if name == "eras" else ()
        got = out_json(capsys, "prompt", SLUG, name, *args)
        for hole in ("{inputs}", "{output_schema}", "{style_rules}"):
            assert hole not in got["text"], (name, hole)
        assert "Small Trip" in got["text"]


def test_a_dates_reply_missing_a_song_is_refused(ws, tmp_path, capsys):
    fill(ws, capsys, upto="panel")
    d = read_json(FIX / "dates.json")
    d["songs"].pop()
    r = tmp_path / "reply.json"
    write(r, d)
    assert "no row for bob-band--second-song" in refused("reply", SLUG, "dates", r)


def test_accept_clears_a_prompt_nobody_answered(ws, capsys):
    fill(ws, capsys)
    assert run("prompt", SLUG, "front") == 0
    assert "rendered" in tomllib.loads((ws["dir"] / "journey.toml").read_text())["stages"]["front"]
    assert run("accept", SLUG, "front") == 0
    assert "rendered" not in tomllib.loads((ws["dir"] / "journey.toml").read_text())["stages"]["front"]
