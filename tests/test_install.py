"""What an install carries: the bank and the schemas are package data, so a wheel works without a checkout.

CI's install job builds the wheel and runs list and check in a clean venv. These tests catch the same
mistakes without a network: the shipped data root is inside the package, and every data file under
the package matches a package-data glob in pyproject.toml.
"""

import subprocess
import tomllib

from liner_notes import check, doctor, paths
from liner_notes.bank import Bank
from tests.helpers_build import iso, make_bank  # noqa: F401
from tests.test_play import Fake

REPO = paths.PACKAGE.parent


def test_the_bank_and_schemas_live_in_the_package(monkeypatch):
    monkeypatch.delenv("LINER_NOTES_HOME", raising=False)
    assert paths.home().is_relative_to(paths.PACKAGE), f"{paths.home()} is outside the package, so a wheel won't carry it"
    assert len(list((paths.home() / "songs").glob("*.json"))) > 200
    assert {"song", "journey", "remix-journey", "culture-year"} <= set(check.schemas())
    assert paths.schemas_dir().is_relative_to(paths.PACKAGE)


def test_package_data_covers_every_data_file():
    """A data file the globs miss is in the checkout and missing from the wheel."""
    globs = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["liner_notes"]
    shipped = {p for g in globs for p in paths.PACKAGE.glob(g) if p.is_file()}
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "liner_notes"], capture_output=True, check=True)
    tracked = [REPO / n for n in r.stdout.decode().split("\0") if n]
    missed = [p.relative_to(REPO).as_posix() for p in tracked if p.suffix != ".py" and p not in shipped]
    assert not missed, f"not in the wheel: {missed[:5]}"


def test_doctor_says_when_the_bank_is_missing(iso, tmp_path, monkeypatch):
    empty = tmp_path / "site-packages" / "liner_notes" / "shipped"
    empty.mkdir(parents=True)
    monkeypatch.setenv("LINER_NOTES_HOME", str(empty))
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    out = []
    assert doctor.run(Fake(), log=out.append) == 1
    bank = [x for x in out if x[5:].startswith("bank ")]
    assert bank and bank[0].startswith("FAIL") and "no songs" in bank[0] and str(empty) in bank[0]


def test_doctor_counts_the_shipped_bank(iso, monkeypatch):
    make_bank(iso["home"])
    monkeypatch.setenv("LINER_NOTES_HOME", str(iso["home"]))
    monkeypatch.setattr(doctor, "port_owner", lambda port: None)
    out = []
    doctor.run(Fake(), log=out.append)
    assert any(x.startswith("ok") and "2 songs, 1 journeys" in x for x in out)
    assert len(Bank().songs) == 2
