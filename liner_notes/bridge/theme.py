"""The current Omarchy theme's colours, mode and font, for pages that match the desktop.

Pages ask every few seconds, so the answer is kept until the theme's files change (their mtimes
and where the `current` link points). The font comes from `omarchy-font-current`, run by its full
path at most once every `font_every` seconds and outside the lock, so other requests get the last
answer meanwhile; a slow or missing one just means no font.
"""
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

THEME = Path.home() / ".local/state/omarchy/current"
FONT_BIN = "/usr/share/omarchy/bin/omarchy-font-current"


def font_binary():
    return FONT_BIN if os.access(FONT_BIN, os.X_OK) else shutil.which("omarchy-font-current")


class Theme:
    def __init__(self, root=None, font_bin=None, font_every=30.0):
        self.root, self.font_bin, self.font_every = Path(root or THEME), font_bin, font_every
        self.lock = threading.Lock()
        self.stamp, self.value, self.font, self.font_at, self.font_busy = None, None, "", None, False

    def _stamp(self):
        out = [os.path.realpath(self.root)]
        for f in ("theme/colors.toml", "theme.name", "theme/light.mode"):
            try:
                st = (self.root / f).stat()
                out.append((f, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((f, None))
        return tuple(out)

    def _font(self):
        """The cached font, refreshed by at most one caller at a time and never under the lock."""
        with self.lock:
            now = time.monotonic()
            if self.font_busy or (self.font_at is not None and now - self.font_at < self.font_every):
                return self.font
            self.font_busy, self.font_at = True, now
        exe = self.font_bin or font_binary()
        font = ""
        if exe:
            try:
                font = subprocess.run([exe], capture_output=True, text=True, timeout=3).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                font = ""
        with self.lock:
            self.font, self.font_busy = font, False
        return font

    def _read(self):
        colors = {}
        try:
            for line in (self.root / "theme/colors.toml").read_text().splitlines():
                m = re.fullmatch(r'\s*(\w+)\s*=\s*"([^"]*)"\s*', line)
                if m:
                    colors[m.group(1)] = m.group(2)
            name = (self.root / "theme.name").read_text().strip()
        except OSError:
            return None
        mode = colors.pop("mode", None) or ("light" if (self.root / "theme/light.mode").exists() else "dark")
        return {"ok": True, "name": name, "mode": mode, "colors": colors}

    def get(self):
        with self.lock:
            stamp = self._stamp()
            if stamp != self.stamp:
                self.stamp, self.value = stamp, self._read()
            value = self.value
        if value is None:
            return {"ok": False}
        return {**value, "font": self._font()}
