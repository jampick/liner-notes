"""Starting things on the desktop: cliamp, the bridge and the page window.

Every external program goes through a System object, so tests swap in a fake one and nothing
real is started, focused or played. The real one runs argv lists (never a shell), and starts
long-lived programs detached in their own session so the caller never waits on them.
"""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    returncode: int
    stdout: str = ""

    @property
    def ok(self):
        return self.returncode == 0

    def json(self):
        try:
            return json.loads(self.stdout or "null")
        except ValueError:
            return None


class System:
    """The real world. Tests subclass it and override what they need."""

    def __init__(self, env=None):
        self.env = env if env is not None else desktop_env()

    def run(self, argv, timeout=10):
        try:
            r = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, env=self.env)
            return Result(r.returncode, r.stdout)
        except (OSError, subprocess.SubprocessError):
            return Result(127)

    def spawn(self, argv):
        """Start a program detached: its own session, no pipes, and we don't wait for it."""
        p = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=self.env,
            close_fds=True,
        )
        return p.pid

    def which(self, name):
        return shutil.which(name, path=self.env.get("PATH"))

    def sleep(self, s):
        time.sleep(s)

    def now(self):
        return time.monotonic()

    def http_ok(self, url, timeout=1):
        try:
            with urllib.request.urlopen(url, timeout=timeout):
                return True
        except OSError:
            return False

    def cmdline(self, pid):
        """A process's argv, or [] when it's gone."""
        try:
            return Path(f"/proc/{int(pid)}/cmdline").read_bytes().decode(errors="replace").split("\0")
        except (OSError, ValueError):
            return []

    def pids(self):
        return [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]


def desktop_env(base=None):
    """cliamp and the Stream Deck run programs with only PATH, HOME and LANG, so find the session's runtime
    dir, Wayland socket, D-Bus and Hyprland instance ourselves when they're missing."""
    env = dict(os.environ if base is None else base)
    run = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", run)
    if "WAYLAND_DISPLAY" not in env:
        socks = sorted(p.name for p in Path(run).glob("wayland-[0-9]") if p.is_socket())
        if socks:
            env["WAYLAND_DISPLAY"] = socks[0]
    if "DBUS_SESSION_BUS_ADDRESS" not in env and os.path.exists(f"{run}/bus"):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={run}/bus"
    if "HYPRLAND_INSTANCE_SIGNATURE" not in env:
        hy = [p for p in Path(run, "hypr").glob("*") if (p / ".socket.sock").exists()]
        if hy:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = max(hy, key=lambda p: p.stat().st_mtime).name
    return env
