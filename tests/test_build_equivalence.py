"""The new format builds the same page data as the pilot's build.py did.

test_fixture_equivalent needs nothing outside the repo: tests/fixtures/pilot-mini is a made-up
pilot playlist folder, and expected.json is what the pilot's engine/build.py made of it (run once,
on a copy). test_downtempo_equivalent does the same against a real pilot checkout, when
LINER_NOTES_PILOT names one (the pilot repo is never written to). Ids, covers and images are set aside.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from liner_notes.bank import Bank
from liner_notes.build import compose
from liner_notes.migrate import migrate

# Your pilot checkout, only when you point at one: LINER_NOTES_PILOT=<checkout> pytest
PILOT = Path(os.environ["LINER_NOTES_PILOT"]).expanduser() if os.environ.get("LINER_NOTES_PILOT") else None
FOLDER = PILOT / "playlists/downtempo-drift" if PILOT else None

FIXTURE = Path(__file__).parent / "fixtures/pilot-mini"


def old_data(tmp):
    work = tmp / "pilot"
    (work / "engine").mkdir(parents=True)
    shutil.copy2(PILOT / "engine/build.py", work / "engine/build.py")
    shutil.copy2(PILOT / "engine/template.html", work / "engine/template.html")
    shutil.copytree(FOLDER, work / "playlists/downtempo-drift", ignore=shutil.ignore_patterns("originals", "culture"))
    subprocess.run(
        [sys.executable, str(work / "engine/build.py"), str(work / "playlists/downtempo-drift")], check=False, capture_output=True
    )
    html = (work / "dist/downtempo-drift.html").read_text()
    tpl = (PILOT / "engine/template.html").read_text()
    pre = tpl.split("/*__DATA__*/null")[0].split("\n")[-1]
    line = next(ln for ln in html.split("\n") if ln.startswith(pre))
    blob = line[len(pre) :]
    post = tpl.split("/*__DATA__*/null")[1].split("\n")[0]
    return json.loads(blob[: len(blob) - len(post)] if post else blob)


def strip(d):
    d = json.loads(json.dumps(d))
    for k in ("product", "video", "provenance"):
        d.pop(k, None)
    d["copy"]["credits"] = d["copy"]["credits"].split(" An AI model wrote")[0]
    for e in d["eras"]:
        e["image"] = bool(e["image"])
    for s in d["stops"]:
        for k in ("id", "draft", "cover"):
            s.pop(k, None)
    return d


def test_fixture_equivalent(tmp_path):
    old = json.loads((FIXTURE / "expected.json").read_text())
    root, cache = tmp_path / "root", tmp_path / "cache"
    migrate(FIXTURE / "playlists/mini-trip", root, cache, log=lambda *a: None)
    new, problems, _ = compose("mini-trip", Bank([root]), cache=cache)
    assert problems == []
    a, b = strip(old), strip(new)
    assert (
        [s["title"] for s in a["stops"]]
        == [s["title"] for s in b["stops"]]
        == ["Second Wind", "Harbour Lights", "Low Tide", "Paper Boats", "Night Ferry"]
    )
    for so, sn in zip(a["stops"], b["stops"], strict=True):
        assert so == sn, so["title"]
    a.pop("stops"), b.pop("stops")
    assert a == b
    # The journey lists its songs in the order the page numbers them.
    j = json.loads((root / "journeys/mini-trip/journey.json").read_text())
    assert [s.split("--")[1] for s in j["songs"]] == ["second-wind", "harbour-lights", "low-tide", "paper-boats", "night-ferry"]


@pytest.mark.skipif(not (FOLDER and FOLDER.exists()), reason="set LINER_NOTES_PILOT to a pilot checkout to compare against it")
def test_downtempo_equivalent(tmp_path):
    old = old_data(tmp_path)
    root, cache = tmp_path / "root", tmp_path / "cache"
    migrate(FOLDER, root, cache, log=lambda *a: None)
    new, problems, _ = compose("downtempo-drift", Bank([root]), cache=cache)
    assert not [p for p in problems if "cover" not in p], problems
    a, b = strip(old), strip(new)
    assert [s["title"] for s in a["stops"]] == [s["title"] for s in b["stops"]]
    for so, sn in zip(a["stops"], b["stops"], strict=True):
        assert so == sn, so["title"]
    a.pop("stops"), b.pop("stops")
    assert a == b
    assert re.fullmatch(r"[a-z0-9-]+--[a-z0-9-]+", new["stops"][0]["id"])
