"""The page <-> bridge contract as code, so the real bridge and tools/fake_bridge.py can't drift.

Everything here is pure: route patterns, the Range rules, the CSP and its script hashes, the token
placeholder, the bodies the page reacts to, and the Cache-Control each kind of answer carries. The
bridge imports these; a fake bridge should import them too instead of copying them.

Route patterns match the raw, undecoded path with fullmatch, so ../ and %2f never get through.
"""

import base64
import hashlib
import html
import re

PORT = 47391

# --- routes -------------------------------------------------------------------------------------
SLUG = r"[a-z0-9-]+"
YT_ID = re.compile(r"[\w-]{11}")
SONG_ID = re.compile(SLUG)
VIDEO = re.compile(r"/v/([\w-]{11})\.mp4")  # /v/<yt>.mp4
VIDEO_LIST = "/v/"
PICTURE = re.compile(rf"/m/({SLUG})/(culture|artists)/([\w-][\w.-]*\.(webp|jpg|png))")  # /m/<slug>/<dir>/<file>
COVER = re.compile(rf"/m/({SLUG})/covers/([\w-]{{1,200}})\.webp")  # /m/<slug or live>/covers/<song id or yt id>.webp
DEPTH = re.compile(rf"/d/({SLUG})/(culture|artists|eras|covers)/([\w-][\w.-]*\.(?:webp|jpg|png))")
PAGE = re.compile(rf"/p/({SLUG})\.html")
STAGE = re.compile(rf"/p/({SLUG})\.stage\.json")
JOURNEY = re.compile(rf"/journey/({SLUG})\.json")
SONG = re.compile(rf"/song/({SLUG})\.json")
TYPES = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}

NOW_KEYS = (
    "ok",
    "version",
    "source",
    "state",
    "title",
    "artist",
    "album",
    "yt",
    "song",
    "journey",
    "playlist",
    "position",
    "duration",
    "epoch",
    "moving",
    "latency",
    "can",
)
NULL_CORS = ("/now", "/theme")  # all a page opened from disk (Origin: null) may read
COMMANDS = ("toggle", "next", "prev")
MAX_BODY = 4096

# Cache-Control by kind of answer.
CACHE = {
    "api": "no-store",  # /now, /lyrics, /history, errors: always fresh
    "page": "no-cache",  # /p/*.html and stage data: revalidate with the ETag
    "picture": "private, max-age=3600",
    "video": "private, max-age=86400",
}

# --- the token ----------------------------------------------------------------------------------
TOKEN_META = b'<meta name="liner-token" content="">'


def token_meta(token):
    return f'<meta name="liner-token" content="{html.escape(token)}">'.encode()


def inject_token(page, token):
    """The page with this token in its placeholder (only the first; the rest of the bytes are untouched)."""
    return page.replace(TOKEN_META, token_meta(token), 1)


def token_refusal():
    """The 403 body for a POST with a missing or stale X-Liner-Token; the page reloads on reason "token"."""
    return {"ok": False, "reason": "token", "error": "reload the page for the current token"}


# --- answers the page reads ---------------------------------------------------------------------
def lyric_line(t, text):
    s = round(float(t), 3)
    return {"t": s, "start": s, "text": str(text or "")}


def lyrics_answer(lines, source, yt, song):
    return {"ok": bool(lines), "lines": lines or [], "source": source if lines else None, "yt": yt, "song": song}


def lyrics_not_playing(yt, song):
    """Asked about a song that isn't the one playing: never another song's lyrics."""
    return {"ok": False, "error": "not playing", "lines": [], "source": None, "yt": yt, "song": song}


def not_playing(reason, version, source, can):
    """/now with nothing to show: reason is "no-player" or "stopped"."""
    return {"ok": False, "reason": reason, "version": version, "source": source, "can": can}


# --- Range (RFC 7233, one range) ----------------------------------------------------------------
UNSATISFIABLE = "unsatisfiable"


def parse_range(header, size):
    """None means "send it all with 200" (no header, a malformed one, several ranges, or `bytes=-`),
    UNSATISFIABLE means 416, else (first, last) inclusive."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if not m or not (m.group(1) or m.group(2)):
        return None
    a, b = m.groups()
    if a:
        first = int(a)
        last = int(b) if b else size - 1
        if b and last < first:
            return None  # not a valid range, so the header is ignored
        if first >= size:
            return UNSATISFIABLE
        return first, min(last, size - 1)
    n = int(b)
    if n == 0 or size == 0:
        return UNSATISFIABLE
    return max(0, size - n), size - 1


# --- the Content-Security-Policy ----------------------------------------------------------------
APPLE_PREVIEWS = "https://audio-ssl.itunes.apple.com"  # the page plays and analyses Apple's song previews
SCRIPT = re.compile(rb"<script(\s[^>]*)?>(.*?)</script\s*>", re.S | re.I)


def script_hashes(page):
    """'sha256-...' for each inline <script> in the page bytes (ones with src= load from 'self')."""
    out = []
    for m in SCRIPT.finditer(page):
        attrs = (m.group(1) or b"").lower()
        if re.search(rb"\ssrc\s*=", b" " + attrs):
            continue
        text = m.group(2).replace(b"\r\n", b"\n").replace(b"\r", b"\n")  # what the HTML parser hands the browser
        h = "'sha256-" + base64.b64encode(hashlib.sha256(text).digest()).decode() + "'"
        if h not in out:
            out.append(h)
    return out


def csp(hashes=()):
    """The policy for a page whose inline scripts have these hashes. No 'unsafe-inline' for scripts;
    styles keep it while the page sets style attributes."""
    script = " ".join(["'self'", *hashes])
    return (
        "default-src 'self'; img-src 'self' data: blob:; "
        f"media-src 'self' blob: {APPLE_PREVIEWS}; style-src 'self' 'unsafe-inline'; "
        f"script-src {script}; connect-src 'self' {APPLE_PREVIEWS}; font-src 'self' data:; frame-src 'none'; "
        "object-src 'none'; base-uri 'none'"
    )


CSP = csp()  # for HTML with no inline script (the index page)
