import subprocess

import pytest

from liner_notes import build as B
from liner_notes import check
from liner_notes.bank import Bank, write_json
from tests.helpers_build import TEMPLATE, iso, make_bank  # noqa: F401

JPEG = "data:image/" + "jpeg;base64,/9j/4AAQ"


def tree(root):
    write_json(root / "songs/a--b.json", {"id": "a--b"})
    (root / "journeys/x/media/eras").mkdir(parents=True)
    (root / "journeys/x/media/eras/era1.webp").write_bytes(b"ours")
    return root


@pytest.mark.parametrize("bad, why", [
    ("journeys/x/media/covers/AAAAAAAAAAA.webp", "personal data"),
    ("journeys/x/media/culture/Film.webp", "personal data"),
    ("lyrics/AAAAAAAAAAA.json", "personal data"),
    ("journeys/x/standins.json", "video ids"),
    ("clip.mp4", "personal media"),
    ("notes/retimed.json", "lyrics file"),
    ("README.md", "inline JPEG"),
])
def test_tree_guard(tmp_path, bad, why):
    root = tree(tmp_path / "repo")
    assert check.scan_tree(root) == []
    p = root / bad
    p.parent.mkdir(parents=True, exist_ok=True)
    if bad.endswith("retimed.json"):
        write_json(p, {"lines": [{"t": 1, "text": "a lyric"}]})
    elif bad == "README.md":
        p.write_text(f"![cover]({JPEG})")
    else:
        p.write_bytes(b"x")
    found = check.scan_tree(root)
    assert len(found) == 1 and why in found[0] and bad in found[0]


def test_tree_guard_uses_git_and_honours_ignores(tmp_path):
    root = tree(tmp_path / "repo")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("dist/\n")
    (root / "dist").mkdir()
    (root / "dist/page.html").write_text(JPEG)
    assert check.scan_tree(root) == []
    (root / "video.mp4").write_bytes(b"x")  # untracked but not ignored: git would pick it up
    assert len(check.scan_tree(root)) == 1


def test_page_guard(iso, tmp_path):
    make_bank(iso["home"])
    bank = Bank([iso["home"]])
    tpl = tmp_path / "t.html"
    tpl.write_text(TEMPLATE)
    out, _ = B.build("test-trip", public=True, bank=bank, template=tpl, log=lambda *a: None)
    j = bank.journeys["test-trip"]
    assert check.scan_page(out.read_text(), j, bank) == []
    personal, _ = B.build("test-trip", bank=bank, template=tpl, log=lambda *a: None)
    found = check.scan_page(personal.read_text(), j, bank)
    assert any("AAAAAAAAAAA" in f for f in found)
    assert check.scan_page(f'<img src="{JPEG}">', j, bank) == ["an inline JPEG picture"]
    assert "a video path" in check.scan_page('<video src="/v/AAAAAAAAAAA.mp4">')
    assert "lyric lines" in check.scan_page('{"lines": [{"t": 1, "text": "x"}]}')
