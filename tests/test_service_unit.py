import configparser
from pathlib import Path

import liner_notes

UNIT = Path(liner_notes.__file__).parent / "data/liner-notes.service"


def test_unit_runs_the_module_from_a_filled_in_python():
    text = UNIT.read_text()
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(text)
    svc = cp["Service"]
    assert svc["ExecStart"] == "@PYTHON@ -m liner_notes serve"
    assert svc["Restart"] == "on-failure" and svc["RestartSec"] == "2"
    assert cp["Install"]["WantedBy"] == "default.target"
    assert ".local/bin/liner-notes" not in text.split("[Unit]", 1)[1]
