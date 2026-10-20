import hashlib

from liner_notes import depth
from liner_notes.bank import Bank
from tests.helpers_build import iso, make_bank  # noqa: F401


class FakeMaker:
    def __init__(self):
        self.made = []

    def make(self, src, out):
        self.made.append(src.name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")


def test_maps_keyed_by_sha(iso):
    make_bank(iso["home"])
    (iso["cache"] / "covers").mkdir()
    (iso["cache"] / "covers/a.webp").write_bytes(b"cover a")
    (iso["cache"] / "covers/notes.txt").write_bytes(b"not a picture")
    m = FakeMaker()
    assert depth.ensure(Bank([iso["home"]]), iso["cache"], maker=m, log=lambda *a: None) == (2, 2)
    assert sorted(m.made) == ["a.webp", "era1.webp"]
    assert (iso["cache"] / "depth" / (hashlib.sha256(b"cover a").hexdigest() + ".png")).is_file()
    again = FakeMaker()
    assert depth.ensure(Bank([iso["home"]]), iso["cache"], maker=again, log=lambda *a: None) == (0, 2) and not again.made


def test_no_model_does_nothing(iso, monkeypatch):
    make_bank(iso["home"])
    monkeypatch.setenv("LINER_NOTES_DEPTH_MODEL", str(iso["home"] / "missing.onnx"))
    log = []
    assert depth.ensure(Bank([iso["home"]]), iso["cache"], log=log.append) is None
    assert "no model" in log[0] and not (iso["cache"] / "depth").exists()
