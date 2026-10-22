"""Stage files and staleness (phase 1 item 5): a journey is a folder of artefacts, journey.toml keeps
the hash of every stage's inputs and of its signed-off output, and status says what's stale or
unsigned without rerunning anything. Everything goes through the CLI, the way the console will."""

import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from liner_notes import build, cli
from liner_notes.bank import Bank, problems, read_json
from tests.helpers_build import iso, make_bank  # noqa: F401

FIX = Path(__file__).parent / "fixtures" / "workflow"
SLUG = "small-trip"
STAGES = ["heuristic", "panel", "dates", "eras", "front"]
FILES = {"heuristic": "heuristic.md", "panel": "panel.json", "dates": "dates.json", "eras": "eras.json", "front": "front.json"}


@pytest.fixture
def ws(iso, tmp_path, monkeypatch):
    """A shipped bank with the two fixture songs, a prompts folder of our own, and no Anthropic SDK to import."""
    make_bank(iso["home"])
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    prompts = tmp_path / "prompts"
    shutil.copytree(FIX / "prompts", prompts)
    monkeypatch.setenv("LINER_NOTES_PROMPTS", str(prompts))
    monkeypatch.setitem(sys.modules, "anthropic", None)  # nothing here may create a client
    return {"dir": iso["data"] / "journeys" / SLUG, "prompts": prompts, **iso}


def run(*argv):
    return cli.main([str(a) for a in argv])


def status(capsys, slug=SLUG):
    capsys.readouterr()
    assert run("status", slug, "--json") == 0
    return json.loads(capsys.readouterr().out)


def states(capsys):
    return {s["name"]: s for s in status(capsys)["stages"]}


def fill(capsys):
    """Make the journey and accept every stage from the fixtures, as a paste would."""
    assert run("new", "Small Trip") == 0
    for name in STAGES:
        assert run("stage", SLUG, name, "--prompt") == 0
        assert run("stage", SLUG, name, "--accept", FIX / FILES[name]) == 0
    capsys.readouterr()


def test_new_makes_a_journey_with_a_panel_for_an_idea(ws, capsys):
    assert run("new", "Small Trip") == 0
    t = tomllib.loads((ws["dir"] / "journey.toml").read_text())
    assert t["slug"] == SLUG and t["title"] == "Small Trip" and t["panel"] is True
    assert t["seed"]["idea"] == "Small Trip"
    st = status(capsys)
    assert [s["name"] for s in st["stages"]] == STAGES
    assert {s["state"] for s in st["stages"]} == {"empty"}
    assert not (ws["dir"] / "journey.json").exists()


def test_new_refuses_a_journey_that_exists(ws, capsys):
    assert run("new", "Small Trip") == 0
    with pytest.raises(SystemExit) as e:
        run("new", "Small Trip")
    assert "already" in str(e.value)
    with pytest.raises(SystemExit) as e:
        run("new", "Test Trip")  # the shipped fixture journey
    assert "already" in str(e.value)


def test_a_playlist_gets_no_panel_unless_asked(ws, capsys):
    assert run("new", "Boogie Down", "--from", FIX / "boogie.m3u") == 0
    st = status(capsys, "boogie-down")
    assert st["panel"] is False
    assert [s["name"] for s in st["stages"]] == ["heuristic", "dates", "eras", "front"]
    assert st["songs"] == ["ann-artist--first-song", "bob-band--second-song"]
    assert run("new", "Boogie Panel", "--from", FIX / "boogie.m3u", "--panel") == 0
    assert "panel" in [s["name"] for s in status(capsys, "boogie-panel")["stages"]]


def test_prompt_has_the_brief_the_inputs_and_the_schema_and_is_recorded(ws, capsys):
    assert run("new", "Small Trip") == 0
    for name in ("heuristic", "panel", "dates"):
        run("stage", SLUG, name, "--accept", FIX / FILES[name])
    capsys.readouterr()
    assert run("stage", SLUG, "eras", "--prompt") == 0
    out = capsys.readouterr().out
    assert "Fixture brief for the era stage" in out
    assert "1991" in out and "rehearsal" not in out  # dates.json is in, eras.json isn't written yet
    assert "In: recordings from 1990 and 1991" in out  # the heuristic
    assert '"essay"' in out  # the output schema
    t = tomllib.loads((ws["dir"] / "journey.toml").read_text())
    assert t["stages"]["eras"]["rendered"] == {"id": "era", "version": "1"}


def test_prompt_refuses_while_an_input_is_missing(ws, capsys):
    assert run("new", "Small Trip") == 0
    with pytest.raises(SystemExit) as e:
        run("stage", SLUG, "eras", "--prompt")
    assert "dates.json" in str(e.value)


def test_accept_rejects_a_bad_reply_and_names_the_field(ws, tmp_path, capsys):
    assert run("new", "Small Trip") == 0
    bad = read_json(FIX / "eras.json")
    del bad["eras"][0]["name"]
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps(bad))
    with pytest.raises(SystemExit) as e:
        run("stage", SLUG, "eras", "--accept", reply)
    assert "name" in str(e.value)
    assert not (ws["dir"] / "eras.json").exists()
    assert states(capsys)["eras"]["state"] == "empty"


def test_accept_rejects_a_vote_from_a_judge_who_isnt_on_the_panel(ws, tmp_path, capsys):
    assert run("new", "Small Trip") == 0
    p = read_json(FIX / "panel.json")
    p["candidates"][0]["votes"]["zed"] = {"vote": "yes", "why": "Me too."}
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps(p))
    with pytest.raises(SystemExit) as e:
        run("stage", SLUG, "panel", "--accept", reply)
    assert "zed" in str(e.value)
    assert not (ws["dir"] / "panel.json").exists()


def test_a_filled_journey_is_all_ok(ws, capsys):
    fill(capsys)
    assert {s["name"]: s["state"] for s in status(capsys)["stages"]} == dict.fromkeys(STAGES, "ok")
    t = tomllib.loads((ws["dir"] / "journey.toml").read_text())
    assert set(t["stages"]["eras"]["inputs"]) == {"dates.json", "heuristic.md"}
    assert t["stages"]["eras"]["prompt"] == {"id": "era", "version": "1"}


def test_changing_a_songs_year_makes_eras_stale(ws, capsys):
    fill(capsys)
    f = ws["dir"] / "dates.json"
    d = read_json(f)
    d["songs"][1]["year"] = 1992
    f.write_text(json.dumps(d, indent=1))
    st = states(capsys)
    assert st["eras"]["state"] == "stale" and st["eras"]["stale"] == ["dates.json changed"]
    assert st["dates"]["state"] == "unsigned"  # it was edited after sign-off
    assert st["heuristic"]["state"] == st["panel"]["state"] == st["front"]["state"] == "ok"


def test_editing_the_heuristic_makes_the_panel_stale(ws, capsys):
    fill(capsys)
    with open(ws["dir"] / "heuristic.md", "a") as f:
        f.write("Also out: anything over six minutes.\n")
    st = states(capsys)
    assert st["panel"]["state"] == "stale" and "heuristic.md changed" in st["panel"]["stale"]
    assert st["eras"]["state"] == "stale"  # eras reads the heuristic too
    assert st["heuristic"]["state"] == "unsigned" and st["dates"]["state"] == "ok"


def test_editing_an_output_after_sign_off_shows_unsigned(ws, capsys):
    fill(capsys)
    f = ws["dir"] / "front.json"
    d = read_json(f)
    d["lede"] = "A different lede, typed in by hand."
    f.write_text(json.dumps(d, indent=1))
    st = states(capsys)
    assert st["front"]["state"] == "unsigned" and st["front"]["unsigned"] is True and st["front"]["stale"] == []
    assert all(st[n]["state"] == "ok" for n in STAGES if n != "front")


def test_accept_clears_stale_without_rerunning_and_nothing_reruns_by_itself(ws, capsys):
    fill(capsys)
    f = ws["dir"] / "dates.json"
    d = read_json(f)
    d["songs"][1]["year"] = 1992
    f.write_text(json.dumps(d, indent=1))
    before = {n: (ws["dir"] / FILES[n]).read_bytes() for n in STAGES}
    for _ in range(2):  # looking twice changes nothing: no stage reruns on its own
        assert states(capsys)["eras"]["state"] == "stale"
    assert {n: (ws["dir"] / FILES[n]).read_bytes() for n in STAGES} == before
    assert run("accept", SLUG, "eras") == 0
    assert run("accept", SLUG, "dates") == 0
    assert {n: (ws["dir"] / FILES[n]).read_bytes() for n in STAGES} == before
    assert {s["name"]: s["state"] for s in status(capsys)["stages"]} == dict.fromkeys(STAGES, "ok")


def test_a_changed_brief_makes_its_stage_stale(ws, capsys):
    fill(capsys)
    p = ws["prompts"] / "era.md"
    p.write_text(p.read_text().replace('version = "1"', 'version = "2"'))
    st = states(capsys)
    assert st["eras"]["state"] == "stale" and st["eras"]["stale"] == ["the era brief changed (version 1, now 2)"]
    assert st["front"]["state"] == "ok"


def test_four_yes_votes_and_a_veto_make_a_close_call_not_a_pick(ws, capsys):
    fill(capsys)
    st = status(capsys)
    assert st["picks"] == ["ann-artist--first-song", "bob-band--second-song"]
    assert st["close_calls"] == ["cat-crew--third-song", "dee-duo--fourth-song"]
    # Picks are computed from panel.json each time, never stored: drop the veto and the third song is in.
    f = ws["dir"] / "panel.json"
    p = read_json(f)
    assert "picks" not in p and "close_calls" not in p
    del p["candidates"][2]["veto"]
    f.write_text(json.dumps(p, indent=1))
    assert status(capsys)["picks"] == ["ann-artist--first-song", "bob-band--second-song", "cat-crew--third-song"]
    assert "picks" not in tomllib.loads((ws["dir"] / "journey.toml").read_text())


def test_an_override_with_a_reason_beats_the_count(ws, capsys):
    fill(capsys)
    f = ws["dir"] / "panel.json"
    p = read_json(f)
    p["candidates"][3]["override"] = {"to": "pick", "why": "The owner wants the opener."}
    f.write_text(json.dumps(p, indent=1))
    st = status(capsys)
    assert "dee-duo--fourth-song" in st["picks"] and "dee-duo--fourth-song" not in st["close_calls"]


def test_assemble_writes_a_journey_the_page_reads(ws, capsys):
    fill(capsys)
    assert run("assemble", SLUG) == 0
    j = read_json(ws["dir"] / "journey.json")
    assert problems(j, "journey") == []
    assert j["songs"] == ["ann-artist--first-song", "bob-band--second-song"]
    assert set(j["panel"]["quotes"]) == set(j["songs"])  # close calls stay out of the journey
    assert j["panel"]["quotes"]["ann-artist--first-song"] == {"j": "ros", "q": "Everyone in the room sings the chorus."}
    assert j["panel"]["judges"]["pia"]["name"] == "Pia Hartnell"
    assert j["eras"][0]["n"] == 1 and j["eras"][0]["years"] == "1990–91"
    data, probs, _ = build.compose(SLUG, Bank(), public=True)
    assert probs == []
    assert [s["title"] for s in data["stops"]] == ["First Song", "Second Song"]
    assert data["express"]["n"] == [1] and data["stops"][0]["threads"][0]["to"] == 2


def test_assemble_refuses_while_a_stage_is_stale(ws, capsys):
    fill(capsys)
    with open(ws["dir"] / "heuristic.md", "a") as f:
        f.write("One more rule.\n")
    with pytest.raises(SystemExit) as e:
        run("assemble", SLUG)
    assert "panel" in str(e.value) and "stale" in str(e.value)
    assert not (ws["dir"] / "journey.json").exists()
