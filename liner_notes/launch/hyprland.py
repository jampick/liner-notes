"""Hyprland, through hyprctl and its Lua dispatchers.

    hl.dsp.exec_cmd("<command>", { workspace = "2 silent", fullscreen = true })
    hl.dsp.window.close({ window = "address:0x..." })
    hl.dsp.focus({ window = "address:0x..." })

Every string that goes into Lua is quoted by lua_str, so a slug, a path with a space or a quote
in a playlist name can't end the string early.
"""

import os
import socket
from pathlib import Path

PAGE_CLASS = "chrome-127.0.0.1__p_"


def lua_str(s):
    """A Lua string literal for any text: backslash, quote and control characters escaped."""
    out = []
    for ch in str(s):
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def window(addr):
    return "{ window = " + lua_str(f"address:{addr}") + " }"


class Hypr:
    def __init__(self, system):
        self.sys = system

    @property
    def available(self):
        return bool(self.sys.env.get("HYPRLAND_INSTANCE_SIGNATURE")) and bool(self.sys.which("hyprctl"))

    def query(self, what):
        return self.sys.run(["hyprctl", what, "-j"], timeout=5).json()

    def clients(self):
        return self.query("clients") or []

    def active(self):
        return self.query("activewindow") or {}

    def dispatch(self, lua):
        return self.sys.run(["hyprctl", "dispatch", lua], timeout=5)

    def exec_cmd(self, command, workspace, fullscreen=False):
        opts = f"workspace = {lua_str(workspace)}" + (", fullscreen = true" if fullscreen else "")
        return self.dispatch(f"hl.dsp.exec_cmd({lua_str(command)}, {{ {opts} }})")

    def close(self, addr):
        return self.dispatch(f"hl.dsp.window.close({window(addr)})")

    def focus(self, addr):
        return self.dispatch(f"hl.dsp.focus({window(addr)})")

    def move_silent(self, addr, workspace):
        return self.dispatch(
            f"hl.dsp.window.move({{ window = {lua_str('address:' + addr)}, workspace = {lua_str(workspace)}, follow = false }})"
        )

    def events_socket(self):
        env = self.sys.env
        return (
            Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
            / "hypr"
            / env.get("HYPRLAND_INSTANCE_SIGNATURE", "")
            / ".socket2.sock"
        )


def watch(addr, sock_path, remember, connect=None):
    """Follow one window and call remember(workspace) each time it's moved, until it closes."""
    addr = addr.removeprefix("0x")
    s = connect(sock_path) if connect else socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if not connect:
        s.connect(str(sock_path))
    with s, s.makefile("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith(f"movewindowv2>>{addr},"):
                parts = line.split(">>", 1)[1].split(",", 2)
                if len(parts) == 3:
                    remember(parts[2])
            elif line == f"closewindow>>{addr}":
                return
