"""The two secrets the bridge keeps apart.

The page token goes into every page the bridge serves and has to come back in X-Liner-Token on
each POST. It lives in state()/token (mode 0600) and survives restarts, so a page on WS2 keeps
its controls when systemd restarts the bridge. It is made again only when the file is missing or
unreadable.

The pairing token lets another machine in (--lan, --tailscale). It is new each start unless one is
given, and never appears in a response body.
"""

import os
import re
import secrets
from pathlib import Path

from .. import paths

VALID = re.compile(r"[A-Za-z0-9_-]{16,128}")


def _read(p):
    try:
        t = p.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return t if VALID.fullmatch(t) else None


def write_private(p, text):
    """Write text to p, readable by this user only, replacing p in one step."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, text.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, p)


def page_token(path=None):
    """The page token from state()/token, made (atomically, 0600) when it's missing or unreadable."""
    p = Path(path or paths.state() / "token")
    t = _read(p)
    if t:
        try:
            if p.stat().st_mode & 0o077:
                p.chmod(0o600)
        except OSError:
            pass
        return t
    new = secrets.token_urlsafe(24)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(f".token.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, new.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if p.exists():
            os.replace(tmp, p)  # there, but unreadable or not a token
        else:
            os.link(tmp, p)  # fails if another start made one first; then that one wins
            os.unlink(tmp)
    except FileExistsError:
        os.unlink(tmp)
        return _read(p) or new
    return new


def pairing_token():
    return secrets.token_urlsafe(18)
