import json
import subprocess

import pytest

from liner_notes import build as B
from liner_notes import check
from liner_notes.bank import Bank, write_json
from tests.helpers_build import TEMPLATE, iso, make_bank  # noqa: F401

# Built by concatenation so this file doesn't trip the guard it tests.
JPEG = "data:image/" + "jpeg;base64,/9j/4AAQ"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 50
WEBP_BYTES = b"RIFF\x0c\x00\x00\x00WEBPVP8 ours"
LRC = "[00:12.34] first line\n[00:15.00] second line\n"


def tree(root):
    """A clean little checkout: a song, an era picture, a README."""
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write_json(root / "songs/a--b.json", {"id": "a--b", "notes": {"made": "Cut in 1981 [see the sleeve]."}})
    (root / "journeys/x/media/eras").mkdir(parents=True)
    (root / "journeys/x/media/eras/era1.webp").write_bytes(WEBP_BYTES)
    (root / "README.md").write_text("Notes at 3:45 and a [12:00] meeting are fine.\n")
    return root


# One case per probe from the engineering review, plus the original controls.
PROBES = [
    ("songs/x.json", json.dumps({"id": "x", "lyrics": "First line of the song\nSecond line"}), "lyrics"),
    ("notes/x.lrc", LRC, "lyrics"),
    ("journeys/x/NOTES.md", LRC, "timed lyric lines"),
    ("songs/y.json", json.dumps({"syncedLyrics": "[00:01.00] la"}), "lyrics"),
    ("songs/p.json", json.dumps({"plainLyrics": "la"}), "lyrics"),
    ("songs/z.json", json.dumps({"lines": [{"start": 1, "words": "la la"}]}), "lyric lines"),
    ("notes/retimed.json", json.dumps({"lines": [{"t": 1, "text": "a lyric"}]}), "lyric lines"),
    ("journeys/x/media/eras/era9.jpg", JPEG_BYTES, "only pictures"),
    ("journeys/x/media/eras/era8.webp", JPEG_BYTES, "only pictures"),
    ("culture/pics/foo.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "only pictures"),
    ("journeys/x/cover.webp", WEBP_BYTES, "only pictures"),
    ("docs/screenshots/stage.gif", JPEG_BYTES, "only pictures"),
    ("docs/screenshots/stop.webp", JPEG_BYTES, "only pictures"),
    ("docs/screenshots/stop.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 20, "only pictures"),
    ("docs/screenshots/sub/stop.webp", WEBP_BYTES, "only pictures"),
    ("README.md", "![c](data:image/" + "jpg;base64,/9j/4AAQ)", "inline JPEG"),
    ("docs/a.md", "![c](data:image/" + "JPEG;base64,/9j/4AAQ)", "inline JPEG"),
    ("docs/c.md", f"![cover]({JPEG})", "inline JPEG"),
    ("docs/b.html", "<img src='data:image/" + "webp;base64,UklGR'>", "inline WebP"),
    ("journeys/x/videos.json", json.dumps({"BBBBBBBBBBB": {"standin": "SSSSSSSSSSS", "standin_offset": 1}}), "stand-in"),
    ("journeys/x/standins.json", "{}", "video ids"),
    ("journeys/x/media/covers/AAAAAAAAAAA.webp", WEBP_BYTES, "personal data"),
    ("journeys/x/media/culture/Film.webp", WEBP_BYTES, "personal data"),
    ("lyrics/AAAAAAAAAAA.json", "{}", "personal data"),
    ("clip.mp4", b"x", "personal media"),
    ("x.ogg", b"OggS", "personal media"),
    ("x.wav", b"RIFF", "personal media"),
    ("x.opus", b"x", "personal media"),
    ("x.m4a", b"x", "personal media"),
    ("x.webm", b"x", "personal media"),
    ("x.mp3", b"x", "personal media"),
    ("x.flac", b"x", "personal media"),
    ("x.mkv", b"x", "personal media"),
    ("x.mov", b"x", "personal media"),
    ("blob.bin", b"\x00\x01", "doesn't know"),
]


@pytest.mark.parametrize("rel, body, why", PROBES, ids=[p[0] + ":" + p[2] for p in PROBES])
def test_tree_guard(tmp_path, rel, body, why):
    root = tree(tmp_path / "repo")
    assert check.scan_tree(root) == []
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    (p.write_bytes if isinstance(body, bytes) else p.write_text)(body)
    found = check.scan_tree(root)
    assert found and all(rel in f for f in found) and any(why in f for f in found), found


def test_fixture_webp_allowed(tmp_path):
    root = tree(tmp_path / "repo")
    (root / "tests/fixtures/x/media/eras").mkdir(parents=True)
    (root / "tests/fixtures/x/media/eras/era1.webp").write_bytes(b"stub")
    assert check.scan_tree(root) == []


def test_readme_screenshots_allowed(tmp_path):
    root = tree(tmp_path / "repo")
    (root / "docs/screenshots").mkdir(parents=True)
    (root / "docs/screenshots/stop.webp").write_bytes(WEBP_BYTES)
    (root / "docs/screenshots/stage.gif").write_bytes(b"GIF89a" + b"\0" * 20)
    assert check.scan_tree(root) == []


def test_tree_guard_honours_git_ignores(tmp_path):
    root = tree(tmp_path / "repo")
    (root / ".gitignore").write_text("dist/\n")
    (root / "dist").mkdir()
    (root / "dist/page.html").write_text(JPEG)
    assert check.scan_tree(root) == []
    (root / "video.mp4").write_bytes(b"x")  # untracked but not ignored: git would pick it up
    assert len(check.scan_tree(root)) == 1


def test_guard_needs_a_checkout(tmp_path, monkeypatch):
    (tmp_path / "site-packages").mkdir()
    with pytest.raises(SystemExit):
        check.scan_tree(tmp_path / "site-packages")
    monkeypatch.setenv("LINER_NOTES_HOME", str(tmp_path / "site-packages"))
    out = []
    assert check.publish(log=out.append) == 2 and "isn't one" in out[0]


def test_page_guard(iso, tmp_path):
    make_bank(iso["home"])
    bank = Bank([iso["home"]])
    tpl = tmp_path / "t.html"
    tpl.write_text(TEMPLATE)
    out, _ = B.build("test-trip", public=True, bank=bank, template=tpl, log=lambda *a: None)
    j = bank.journeys["test-trip"]
    assert check.scan_page(out.read_text(), j, bank) == []  # its era picture is allowed
    personal, _ = B.build("test-trip", bank=bank, template=tpl, log=lambda *a: None)
    assert any("AAAAAAAAAAA" in f for f in check.scan_page(personal.read_text(), j, bank))
    assert check.scan_page(f'<img src="{JPEG}">') == ["an inline JPEG, PNG or GIF picture"]
    assert "a video path" in check.scan_page('<video src="/v/AAAAAAAAAAA.mp4">')
    assert "lyric lines" in check.scan_page('{"lines": [{"t": 1, "text": "x"}]}')


@pytest.mark.parametrize(
    "html, why",
    [
        ('{"lyrics":["a line","another"]}', "lyric lines"),
        ("[00:12.34] first line", "timed lyric lines"),
        ('window.JOURNEY = {"stops":[]};</script><img src="data:image/' + 'png;base64,iVBOR">', "JPEG, PNG or GIF"),
        ('<img src="data:image/' + 'webp;base64,UklGR">', "isn't an era picture"),
        ('{"syncedLyrics": "x"}', "lyric lines"),
        ('{"standin": "SSSSSSSSSSS"}', "stand-in"),
    ],
)
def test_page_probes(html, why):
    found = check.scan_page(html)
    assert any(why in f for f in found), found


def test_template_code_isnt_a_video_path():
    code = '<script>window.JOURNEY = {"stops": []};</script><script>const src = `/v/${id}.mp4`;</script>'
    assert not any("video path" in f for f in check.scan_page(code))
    data = '<script>window.JOURNEY = {"stops": [{"video": "clip.mp4"}]};</script>'
    assert any("video path" in f for f in check.scan_page(data))
