"""What an install carries: the bank and the schemas are package data, so a wheel works without a checkout.

CI's install job builds the wheel and runs list and check in a clean venv. These tests catch the same
mistakes without a network: the shipped data root is inside the package, and every data file under
the package matches a package-data glob in pyproject.toml.
"""

import subprocess
import tomllib

import pytest

from liner_notes import check, doctor, paths
from liner_notes.bank import Bank
from tests.helpers_build import iso, make_bank  # noqa: F401
from tests.test_play import Fake

REPO = paths.PACKAGE.parent


def short(p):
    return str(p).replace(str(paths.Path.home()) + "/", "~/", 1)


def test_the_version_marks_the_new_layout():
    """Copies record the version they were made under; 0.2.0 was the layout without a bank in the wheel."""
    from liner_notes import __version__

    declared = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert declared == __version__ == "0.3.0.dev0"


def test_the_bank_and_schemas_live_in_the_package(monkeypatch):
    monkeypatch.delenv("LINER_NOTES_HOME", raising=False)
    assert paths.home().is_relative_to(paths.PACKAGE), f"{paths.home()} is outside the package, so a wheel won't carry it"
    assert len(list((paths.home() / "songs").glob("*.json"))) > 200
    assert {"song", "journey", "remix-journey", "culture-year"} <= set(check.schemas())
    assert paths.schemas_dir().is_relative_to(paths.PACKAGE)


NOT_SHIPPED = {"REVIEW.md", "CORRECTIONS.md"}  # the journeys' working notes stay in the repo


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="needs the git checkout")
def test_package_data_covers_every_data_file():
    """A data file the globs miss is in the checkout and missing from the wheel."""
    st = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["setuptools"]
    shipped = {p for g in st["package-data"]["liner_notes"] for p in paths.PACKAGE.glob(g) if p.is_file()}
    shipped -= {p for g in st.get("exclude-package-data", {}).get("liner_notes", []) for p in paths.PACKAGE.glob(g)}
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "liner_notes"], capture_output=True, check=True)
    tracked = [REPO / n for n in r.stdout.decode().split("\0") if n]
    missed = [p.relative_to(REPO).as_posix() for p in tracked if p.suffix != ".py" and p not in shipped and p.name not in NOT_SHIPPED]
    assert not missed, f"not in the wheel: {missed[:5]}"
    notes = [p for p in tracked if p.name in NOT_SHIPPED]
    assert notes and not [p for p in notes if p in shipped], "REVIEW.md and CORRECTIONS.md stay out of the wheel"


def test_doctor_says_when_the_bank_is_missing(iso, tmp_path, monkeypatch):
    empty = tmp_path / "site-packages" / "liner_notes" / "shipped"
    empty.mkdir(parents=True)
    monkeypatch.setenv("LINER_NOTES_HOME", str(empty))
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    out = []
    assert doctor.run(Fake(), log=out.append) == 1
    bank = [x for x in out if x[5:].startswith("bank ")]
    assert bank and bank[0].startswith("FAIL") and "no songs" in bank[0] and short(empty) in bank[0]
    assert f"LINER_NOTES_HOME is set to {short(empty)}" in bank[0]


def test_doctor_says_how_to_reinstall(iso, tmp_path, monkeypatch):
    monkeypatch.delenv("LINER_NOTES_HOME", raising=False)
    monkeypatch.setattr(paths, "PACKAGE", tmp_path / "site-packages" / "liner_notes")
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    out = []
    doctor.run(Fake(), log=out.append)
    bank = [x for x in out if x[5:].startswith("bank ")]
    assert bank and "pipx reinstall liner-notes" in bank[0] and "LINER_NOTES_HOME" not in bank[0]


def test_check_says_one_problem(iso, tmp_path, monkeypatch):
    monkeypatch.setenv("LINER_NOTES_HOME", str(tmp_path / "empty"))
    out = []
    assert check.check(log=lambda *a: out.append(" ".join(map(str, a)))) == 1
    assert out[-1].endswith(", 1 problem")


def test_doctor_counts_the_shipped_bank(iso, monkeypatch):
    make_bank(iso["home"])
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    out = []
    doctor.run(Fake(), log=out.append)
    assert any(x.startswith("ok") and "2 songs and 1 journey shipped" in x for x in out)
    assert len(Bank().songs) == 2
