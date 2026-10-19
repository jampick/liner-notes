import tomllib

import pytest

from liner_notes import add
from liner_notes.bank import write_json

from .fake_cliamp import FakeCliamp


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    write_json(home / "songs/christopher-cross--sailing.json", {"id": "christopher-cross--sailing", "title": "Sailing", "artist": "Christopher Cross", "year": 1979, "aliases": {"youtube": ["AAAAAAAAAAA"]}})
    write_json(home / "songs/air--all-i-need.json", {"id": "air--all-i-need", "title": "All I Need", "artist": "Air", "year": 1998, "aliases": {}})
    write_json(home / "journeys/demo/journey.json", {"slug": "demo", "title": "Demo Mix", "songs": ["christopher-cross--sailing", "air--all-i-need"], "pins": {"air--all-i-need": {"youtube": "BBBBBBBBBBB"}}})
    monkeypatch.setenv("LINER_NOTES_HOME", str(home))
    monkeypatch.setenv("LINER_NOTES_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CLIAMP_CONFIG_DIR", str(tmp_path / "cliamp"))
    monkeypatch.setenv("LINER_NOTES_CLIAMP_SOCKET", str(tmp_path / "c.sock"))
    return tmp_path


def test_youtube_playlist(env):
    dest = add.add("demo", log=lambda *a: None)
    d = tomllib.loads(dest.read_text())
    assert [t["path"][-11:] for t in d["track"]] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert dest.name == "Demo Mix.toml"
    with pytest.raises(SystemExit):
        add.add("demo", log=lambda *a: None)


def test_other_provider_is_read_only(env):
    fake = FakeCliamp(env / "c.sock", [{"title": "x", "path": "x"}])
    fake.search = {"Christopher Cross Sailing": [{"title": "Sailing - 2019 Remaster", "artist": "Christopher Cross", "path": "spotify:track:1"}]}
    out = add.add("demo", provider="spotify", log=lambda *a: None)
    import json

    d = json.loads(out.read_text())
    assert d["found"][0]["song"] == "christopher-cross--sailing" and d["missing"] == ["air--all-i-need"]
    assert {op for op, _ in fake.ops} == {"provider.search"}
    fake.close()
