"""Fetch the pictures a journey shows but we don't own, into the cache, then write its stage data.

    liner-notes media <slug> [--public]

  - Covers from Apple (the song's Apple Music track id), into cache/covers/<song id>.webp.
  - The lead picture of each Wikipedia article the culture notes name, and of each artist, with its
    licence. Pictures on Wikimedia Commons carry a free licence and an author. Pictures uploaded only
    to English Wikipedia are usually non-free (album covers, film posters, used there as fair use):
    a --public stage leaves those out.

It never asks YouTube anything: whether an upload gets moving pictures on the stage depends only
on whether its video is in the disk cache (see stage.py). Network answers are cached in
cache/http.json, so a second run only fetches what's new. Requests are slow on purpose; Wikipedia
answers 429 when asked too fast.
"""
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT, paths
from .bank import Bank, read_json, write_json
from .stage import safe


class Fetcher:
    def __init__(self, cache=None, pause=0.4):
        self.cache = cache or paths.cache()
        self.http_file = self.cache / "http.json"
        self.http = read_json(self.http_file) if self.http_file.exists() else {}
        self.credits_file = self.cache / "credits.json"
        self.credits = read_json(self.credits_file) if self.credits_file.exists() else {}
        self.pause = pause

    def get(self, url, tries=5):
        for n in range(tries):
            try:
                time.sleep(self.pause)
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code != 429 or n == tries - 1:
                    raise
                wait = int(e.headers.get("Retry-After") or 0) or 15 * (n + 1)
                print(f"  429 from {url.split('/')[2]}, waiting {wait}s", flush=True)
                time.sleep(wait)

    def memo(self, k, fn):
        if k not in self.http:
            if len(self.http) % 25 == 0:
                self.save()  # so an interrupted run keeps what it fetched
                print(f"  ... {len(self.http)} lookups cached", flush=True)
            try:
                self.http[k] = fn()
            except Exception as e:  # a miss is remembered too, so a rerun doesn't hammer the same URL
                print("  miss:", k, e)
                self.http[k] = None
        return self.http[k]

    def save(self):
        write_json(self.http_file, self.http)
        write_json(self.credits_file, self.credits)

    def image(self, src, out, credit):
        if out.exists():
            return out
        from PIL import Image  # optional dependency: liner-notes[media]

        im = Image.open(io.BytesIO(self.get(src)))
        im = im.convert("RGBA" if im.mode in ("P", "LA", "RGBA") else "RGB")
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (12, 12, 12))
            bg.paste(im, mask=im.split()[3])
            im = bg
        im.thumbnail((1600, 1600))
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, "WEBP", quality=80)
        self.credits[str(out.relative_to(self.cache))] = credit
        return out


def wiki_picture(f, title, sub):
    """(relative path, credit) for an article's lead image, or (None, None)."""
    out = f.cache / sub / f"{safe(title)}.webp"
    rel = f"{sub}/{out.name}"
    if out.exists():
        return rel, f.credits.get(rel) or {"source": f"https://en.wikipedia.org/wiki/{title}", "license": "unknown"}
    d = f.memo("summary:" + title, lambda: json.loads(f.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title, safe=""))))
    src = ((d or {}).get("originalimage") or (d or {}).get("thumbnail") or {}).get("source")
    if not src:
        return None, None
    credit = {"source": f"https://en.wikipedia.org/wiki/{title}", "image": src, "license": "non-free", "author": ""}
    if "/wikipedia/commons/" in src:
        name = urllib.parse.unquote(src.split("/")[-1])
        if "/thumb/" in src:
            name = urllib.parse.unquote(src.split("/")[-2])
        meta = f.memo("commons:" + name, lambda: json.loads(f.get(
            "https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo&iiprop=extmetadata&titles=File:" + urllib.parse.quote(name))))
        pages = ((meta or {}).get("query") or {}).get("pages") or {}
        em = next(iter(pages.values()), {}).get("imageinfo", [{}])[0].get("extmetadata", {}) if pages else {}
        credit["license"] = (em.get("LicenseShortName") or {}).get("value") or "unknown"
        credit["author"] = re.sub(r"<[^>]+>", "", (em.get("Artist") or {}).get("value") or "").strip()
    try:
        f.image(src, out, credit)
    except Exception as e:
        print("  image failed:", title, e)
        return None, None
    f.credits[rel] = credit
    return rel, credit


def free(credit):
    lic = (credit or {}).get("license", "").lower()
    return bool(lic) and lic not in ("non-free", "unknown") and "fair use" not in lic


def cover(f, song):
    out = f.cache / "covers" / f"{song['id']}.webp"
    if out.exists():
        return out
    url = ((song.get("aliases") or {}).get("apple") or {}).get("url") or ""
    m = re.search(r"[?&]i=(\d+)", url) or re.search(r"/(\d+)(?:\?|$)", url)
    if not m:
        return None
    d = f.memo("itunes:" + m.group(1), lambda: json.loads(f.get(f"https://itunes.apple.com/lookup?id={m.group(1)}")))
    art = next((r.get("artworkUrl100") for r in (d or {}).get("results", []) if r.get("artworkUrl100")), None)
    if not art:
        return None
    try:
        return f.image(art.replace("100x100bb", "600x600bb"), out, {"source": url, "license": "cover art, (c) the label; local use only"})
    except Exception as e:
        print("  cover failed:", song["title"], e)
        return None


def stage(slug, public=False, bank=None, cache=None, log=print):
    from . import stage as stage_data

    bank = bank or Bank()
    f = Fetcher(cache)
    j = bank.journeys[slug]
    songs = [bank.songs[s] for s in j["songs"] if s in bank.songs]
    n_cover = sum(1 for s in songs if cover(f, s))
    for s in songs:
        for it in s.get("culture") or []:
            if it.get("wiki"):
                wiki_picture(f, it["wiki"], "culture")
    lo, hi = min(e["from"] for e in j["eras"]), max(e["to"] for e in j["eras"])
    for y in range(lo, hi + 1):
        for it in bank.years(y):
            if it.get("wiki"):
                wiki_picture(f, it["wiki"], "culture")
    for name, title in bank.artists().items():
        if any(name == s["artist"] or name in s["artist"] for s in songs):
            wiki_picture(f, title, "artists")
    f.save()
    log(f"{slug}: {n_cover}/{len(songs)} covers in {f.cache}")
    return stage_data.write(slug, bank=bank, cache=f.cache, public=public, log=log)
