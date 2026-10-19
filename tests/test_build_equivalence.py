"""The new format builds the same page data as the pilot's build.py did.

Runs the pilot's own engine/build.py on a copy of its playlist folder (the pilot repo is never
written to), migrates the same folder into a temporary data root, composes the new data, and
compares the two with ids, covers and images set aside.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from liner_notes.bank import Bank
from liner_notes.build import compose
from liner_notes.migrate import migrate

PILOT = Path.home() / "Projects/Music/journeys"
FOLDER = PILOT / "playlists/downtempo-drift"

pytestmark = pytest.mark.skipif(not FOLDER.exists(), reason="the journeys pilot isn't on this machine")


def old_data(tmp):
    work = tmp / "pilot"
    (work / "engine").mkdir(parents=True)
    shutil.copy2(PILOT / "engine/build.py", work / "engine/build.py")
    shutil.copy2(PILOT / "engine/template.html", work / "engine/template.html")
    shutil.copytree(FOLDER, work / "playlists/downtempo-drift", ignore=shutil.ignore_patterns("originals", "culture"))
    subprocess.run([sys.executable, str(work / "engine/build.py"), str(work / "playlists/downtempo-drift")], check=False, capture_output=True)
    html = (work / "dist/downtempo-drift.html").read_text()
    tpl = (PILOT / "engine/template.html").read_text()
    pre = tpl.split("/*__DATA__*/null")[0].split("\n")[-1]
    line = next(l for l in html.split("\n") if l.startswith(pre))
    blob = line[len(pre):]
    post = tpl.split("/*__DATA__*/null")[1].split("\n")[0]
    return json.loads(blob[: len(blob) - len(post)] if post else blob)


def strip(d):
    d = json.loads(json.dumps(d))
    d.pop("product", None)
    for e in d["eras"]:
        e["image"] = bool(e["image"])
    for s in d["stops"]:
        for k in ("id", "draft", "cover"):
            s.pop(k, None)
    return d


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
