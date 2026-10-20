"""`liner-notes service install|uninstall|status`: the bridge as a systemd user service.

install writes ~/.config/systemd/user/liner-notes.service from the unit that ships in the package,
with ExecStart pointing at the Python that's running this (so a pipx or venv install keeps working),
then enables and starts it. It restarts the bridge when it dies and logs to the journal:

    journalctl --user -u liner-notes -f
"""

import sys

from . import paths

UNIT = "liner-notes.service"
TEMPLATE = paths.PACKAGE / "data" / UNIT


def unit_path():
    return paths.user_systemd_dir() / UNIT


def render(template=None, python=None):
    t = template or TEMPLATE
    if not t.is_file():
        raise SystemExit(f"liner-notes: the unit template {t} isn't in this install")
    text = t.read_text()
    if "@PYTHON@" not in text:
        raise SystemExit(f"liner-notes: {t} has no @PYTHON@ placeholder")
    py = python or sys.executable
    if any(c.isspace() for c in py):
        py = '"' + py.replace("\\", "\\\\").replace('"', '\\"') + '"'  # systemd's own quoting
    return text.replace("@PYTHON@", py)


def systemctl(system, *args):
    return system.run(["systemctl", "--user", *args], timeout=20)


def install(system, template=None, python=None, log=print):
    text = render(template, python)
    out = unit_path()
    paths.atomic_write(out, text)
    log(f"wrote {out}")
    for args in (("daemon-reload",), ("enable", "--now", UNIT)):
        r = systemctl(system, *args)
        if not r.ok:
            log(f"liner-notes: systemctl --user {' '.join(args)} failed")
            return 1
    log("the bridge now runs as a user service: systemctl --user status liner-notes")
    return 0


def uninstall(system, log=print):
    out = unit_path()
    if out.exists():
        systemctl(system, "disable", "--now", UNIT)
        out.unlink()
        systemctl(system, "daemon-reload")
        log(f"removed {out}")
    else:
        log("no liner-notes service installed")
    return 0


def status(system, log=print):
    out = unit_path()
    if not out.exists():
        log("not installed (liner-notes service install)")
        return 3
    active = systemctl(system, "is-active", UNIT)
    log(f"{out}: {(active.stdout or '').strip() or 'unknown'}")
    return 0 if active.ok else 3
