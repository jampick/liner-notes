"""Song ids and the text normalisation that matching leans on.

A song id is "artist--title" in lowercase ASCII, with the title's parentheticals dropped:
"Massive Attack", "Unfinished Sympathy (2012 Mix)" -> "massive-attack--unfinished-sympathy".
When two different recordings collide, the second gets "--<year>".
"""

import re
import unicodedata

YT = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")
# An offline playlist plays the cached audio, named by its YouTube id: ~/.cache/journeys/audio/<id>.webm
# from the pilot, ~/.cache/liner-notes/audio/<id>.<ext> from here.
OFFLINE = re.compile(r"/(?:journeys|liner-notes)/audio/([\w-]{11})\.\w+$")

# Upload and release noise that says nothing about which song it is.
_NOISE = re.compile(
    r"""\s*[\(\[]\s*(?:
        official(?:\s+(?:music|lyric|hd|4k|audio|video))*(?:\s+video|\s+audio)?
      | (?:music|lyric|lyrics|audio|hd|4k)\s+video | visuali[sz]er | lyrics? | audio | hd | hq | 4k
      | (?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)? | remastered\s+version
      | (?:single|radio|album|original)\s+(?:version|edit|mix) | mono | stereo
      | (?:\d{4}\s+)?(?:mix|master|remix|version)(?:\s+\d{4})?
      | from\s+[^)\]]* | taken\s+from\s+[^)\]]* | feat\.?[^)\]]* | ft\.?[^)\]]* | featuring[^)\]]* | with\s+[^)\]]*
    )\s*[\)\]]""",
    re.I | re.X,
)
_DASH_NOISE = re.compile(r"\s+-\s+(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?.*$|\s+-\s+(?:single|radio|album)\s+(?:version|edit).*$", re.I)
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)


def ascii_fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return s.encode("ascii", "ignore").decode()


def slug(s):
    s = ascii_fold(s).lower().replace("&", " ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def clean_title(title):
    """The song's own title: no '(Official Video)', '- Remastered 2009', 'feat. X' or bracketed mixes."""
    t = title or ""
    prev = None
    while prev != t:
        prev = t
        t = _NOISE.sub("", t)
    t = _DASH_NOISE.sub("", t)
    t = _FEAT.sub("", t)
    return t.strip(" -")


def clean_artist(artist):
    a = re.sub(r"\s+-\s+topic$", "", artist or "", flags=re.I)
    a = re.sub(r"vevo$", "", a, flags=re.I)
    a = _FEAT.sub("", a)
    return a.strip()


def split_upload_title(title, artist):
    """YouTube titles often read 'Artist - Song'. When the artist field is a channel, take the pair from the title."""
    if " - " in (title or ""):
        left, right = title.split(" - ", 1)
        ka, kl = key(clean_artist(artist)), key(left)
        # The channel name is often the artist plus a suffix ("AIRfrench", "MassiveAttackVEVO").
        if not artist or ka == kl or (ka and ka in kl) or (kl and ka.startswith(kl)):
            return right, left
    return title, artist


def key(s):
    """Loose comparison key: ASCII, lowercase, alphanumerics only, leading 'the' dropped."""
    k = re.sub(r"[^a-z0-9]", "", ascii_fold(s).lower())
    return k[3:] if k.startswith("the") and len(k) > 6 else k


def song_id(artist, title):
    t = re.sub(r"\s*\([^)]*\)\s*", " ", title or "").strip()
    return f"{slug(artist)}--{slug(t)}"


def youtube_id(path):
    m = YT.search(path or "") or OFFLINE.search(path or "")
    return m.group(1) if m else None
