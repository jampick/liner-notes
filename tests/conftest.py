"""Fixtures shared by the bridge tests live in bridge_kit.py."""

import pytest

pytest_plugins = ["tests.bridge_kit"]


@pytest.fixture(autouse=True)
def _own_style_file(tmp_path, monkeypatch):
    """No test reads the real ~/.config/liner-notes/style.toml: each gets its own, empty to start."""
    monkeypatch.setenv("LINER_NOTES_STYLE", str(tmp_path / "user-style.toml"))
