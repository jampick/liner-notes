"""Song ids and the text normalisation that matching leans on.

A song id is "artist--title" in lowercase ASCII, with the title's parentheticals dropped:
"Massive Attack", "Unfinished Sympathy (2012 Mix)" -> "massive-attack--unfinished-sympathy".
When two different recordings collide, the second gets "--<year>".
Cyrillic, Greek, Hangul and kana are romanised by table ("BTS", "봄날" -> "bts--bomnal"); a name in
any other script, kanji included, becomes "x" and 8 hex digits of a hash ("竹内まりや" -> "xb8852d99").
The title and artist themselves are kept as they are; only the id is ASCII.
"""

import hashlib
import re
import unicodedata

YT = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")
# An offline playlist plays the cached audio, named by its YouTube id: .../journeys/audio/<id>.webm from a
# pilot cache, ~/.cache/liner-notes/audio/<id>.<ext> from here.
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


# Letters that NFKD doesn't take apart, and scripts with a one-letter-at-a-time romanisation.
_LATIN = dict(zip("øæœßđðþłıħŋ", "o ae oe ss d d th l i h ng".split(), strict=True))
_CYRILLIC = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґўђјљњћџ",
        "a b v g d e yo zh z i y k l m n o p r s t u f kh ts ch sh shch - y - e yu ya i yi ye g u dj j lj nj c dz".split(),
        strict=True,
    )
)
_GREEK = dict(zip("αβγδεζηθικλμνξοπρσςτυφχψω", "a v g d e z i th i k l m n x o p r s s t y f ch ps o".split(), strict=True))
_TABLE = {k: v.strip("-") for t in (_LATIN, _CYRILLIC, _GREEK) for k, v in t.items()}
# Hiragana, in Unicode order from ぁ (U+3041) to ゖ (U+3096). Katakana is the same table 0x60 higher.
_KANA = (
    "a a i i u u e e o o ka ga ki gi ku gu ke ge ko go sa za shi ji su zu se ze so zo ta da chi ji - tsu zu te de to do "
    "na ni nu ne no ha ba pa hi bi pi fu bu pu he be pe ho bo po ma mi mu me mo ya ya yu yu yo yo ra ri ru re ro wa wa "
    "i e o n vu ka ke"
).split()
_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o"}
# Revised Romanisation of Korean, by jamo: initial, medial, final.
_HL = "g kk n d tt r m b pp s ss - j jj ch k t p h".split()
_HV = "a ae ya yae eo e yeo ye o wa wae oe yo u wo we wi yu eu ui i".split()
_HT = "- k k k n n n t l k m l l l p l m p p t t ng t t k t p t".split()
_SEPARATORS = "・･、。「」『』【】〜～　"


def _hiragana(c):
    return chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c


def _translit(s):
    """s with Cyrillic, Greek, Hangul and kana written out in Latin letters, or None when some letter
    has no simple rule (Chinese characters and kanji, Arabic, Hebrew, Thai and the rest)."""
    out, sokuon = [], False
    for c in unicodedata.normalize("NFC", s).lower():
        h = _hiragana(c)
        if c in _SEPARATORS:
            out.append(" ")
        elif h == "っ":
            sokuon = True
            continue
        elif h == "ー":
            pass
        elif h in _SMALL_Y and out and out[-1].endswith("i") and len(out[-1]) > 1:
            stem = out[-1][:-1]
            out[-1] = stem + (_SMALL_Y[h] if stem in ("sh", "ch", "j") else "y" + _SMALL_Y[h])
        elif h in _SMALL_V and out and len(out[-1]) > 1 and out[-1][-1] in "aiueo":
            out[-1] = out[-1][:-1] + _SMALL_V[h]
        elif "ぁ" <= h <= "ゖ":
            r = _KANA[ord(h) - ord("ぁ")].strip("-")
            out.append(("t" if r.startswith("ch") else r[0]) + r if sokuon and r and r[0] not in "aiueon" else r)
        elif "가" <= c <= "힣":
            n = ord(c) - ord("가")
            out.append((_HL[n // 588] + _HV[n % 588 // 28] + _HT[n % 28]).replace("-", ""))
        elif (base := c if c in _TABLE else unicodedata.normalize("NFD", c)[0]) in _TABLE:
            out.append(_TABLE[base])
        elif c.isalpha() and not ascii_fold(c):
            return None
        else:
            out.append(c)
        sokuon = False
    return "".join(out)


def slug(s):
    """Lowercase ASCII words joined by hyphens. A name in a script with no simple romanisation here,
    or one that leaves nothing behind, becomes "x" and 8 hex digits of its hash, so it's never empty
    and two different names don't share a slug. Only an empty name gives an empty slug."""
    s = unicodedata.normalize("NFC", s or "").strip()
    t = _translit(s)
    out = re.sub(r"[^a-z0-9]+", "-", ascii_fold(t).lower().replace("&", " ").replace("'", "")).strip("-") if t is not None else ""
    if out or not s:
        return out
    return "x" + hashlib.sha256(s.casefold().encode()).hexdigest()[:8]


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
    return f"{slug(artist) or 'unknown'}--{slug(t) or 'untitled'}"


def youtube_id(path):
    m = YT.search(path or "") or OFFLINE.search(path or "")
    return m.group(1) if m else None
