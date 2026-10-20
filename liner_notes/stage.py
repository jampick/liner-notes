"""The stage data for a journey: what the bridge serves at /p/<slug>.stage.json.

    liner-notes build <slug>      writes it after the page (cache/stage/<slug>.stage.json)

Everything here comes from disk, with no network at all:

  tracks[<upload id>]   motion: a video for this upload is in the video cache, so the stage plays it
                        muted under the music. A stand-in (an official video of the same recording, for
                        an audio upload) comes from ~/.local/share/liner-notes/standins/<slug>.json with
                        its offset and rate: video time = position x rate + offset.
  years, songs          the culture notes, with a picture path when the cache has the article's picture
  artists               a photograph of each artist, when the cache has one
  credits               where each picture came from and under what licence (cache/credits.json)

Culture clips stay off until they can come from disk too, so no item carries a video id.
"""
import re
from pathlib import Path

from . import paths
from .bank import Bank, read_json


def safe(title):
    """The file name a Wikipedia article's picture is cached under (the pilot used the same rule)."""
    return re.sub(r"[^\w.-]", "_", title)[:90]


def standins(slug):
    """Stand-in videos for a journey: personal data, ours first, then the pilot's."""
    for f in (paths.user_data() / "standins" / f"{slug}.json", paths.pilot() / "playlists" / slug / "standins.json"):
        if f.is_file():
            return read_json(f)
    return {}


def _picture(cache, sub, title, credits, public):
    f = Path(cache) / sub / f"{safe(title)}.webp"
    if not f.is_file():
        return None
    rel = f"{sub}/{f.name}"
    if public and not free(credits.get(rel)):
        return None
    return rel


def free(credit):
    lic = (credit or {}).get("license", "").lower()
    return bool(lic) and lic not in ("non-free", "unknown") and "fair use" not in lic


def has_video(cache, yt):
    if not yt:
        return False
    if (Path(cache) / "video" / f"{yt}.mp4").is_file():
        return True
    return Path(cache) == paths.cache() and paths.video_file(yt) is not None


def compose(slug, bank=None, cache=None, public=False):
    bank = bank or Bank()
    cache = Path(cache or paths.cache())
    j = bank.journeys[slug]
    songs = [bank.songs[s] for s in j["songs"] if s in bank.songs]
    cf = cache / "credits.json"
    all_credits = read_json(cf) if cf.is_file() else {}
    si = {} if public else standins(slug)

    tracks = {}
    for s in songs:
        yt = ((j.get("pins") or {}).get(s["id"]) or {}).get("youtube") or ((s.get("aliases") or {}).get("youtube") or [None])[0]
        if not yt:
            continue
        t = {"embed": False, "motion": not public and has_video(cache, yt)}
        alt = si.get(yt) or {}
        if not t["motion"] and re.fullmatch(r"[\w-]{11}", str(alt.get("standin", ""))) and has_video(cache, alt["standin"]):
            t.update({"standin": alt["standin"], "standin_offset": float(alt.get("standin_offset") or 0)})
            if alt.get("standin_rate"):
                t["standin_rate"] = float(alt["standin_rate"])
            if alt.get("kind"):
                t["kind"] = alt["kind"]
        tracks[yt] = t

    credits = {}

    def item(it):
        out = {x: it[x] for x in ("kind", "title", "line", "source") if it.get(x)}
        if it.get("wiki"):
            rel = _picture(cache, "culture", it["wiki"], all_credits, public)
            if rel:
                out["img"] = rel
                if rel in all_credits:
                    credits[rel] = all_credits[rel]
        return out

    lo, hi = min(e["from"] for e in j["eras"]), max(e["to"] for e in j["eras"])
    years = {str(y): [item(it) for it in bank.years(y)] for y in range(lo, hi + 1) if bank.years(y)}
    per_song = {s["title"]: [item(it) for it in s.get("culture") or []] for s in songs if s.get("culture")}
    artists = {}
    for name, title in bank.artists().items():
        if not any(name == s["artist"] or name in s["artist"] for s in songs):
            continue
        rel = _picture(cache, "artists", title, all_credits, public)
        if rel:
            artists[name] = rel
            if rel in all_credits:
                credits[rel] = all_credits[rel]
    return {"tracks": tracks, "years": years, "songs": per_song, "artists": artists, "credits": credits, "public": public}


def path(slug, cache=None):
    return Path(cache or paths.cache()) / "stage" / f"{slug}.stage.json"


def write(slug, bank=None, cache=None, public=False, log=print):
    import json

    d = compose(slug, bank, cache, public)
    out = path(slug, cache)
    paths.atomic_write(out, json.dumps(d, ensure_ascii=False))
    n_img = sum(1 for v in list(d["years"].values()) + list(d["songs"].values()) for i in v if "img" in i)
    tr = d["tracks"].values()
    log(f"{out}  {sum(t['motion'] for t in tr)}/{len(tr)} videos on disk  {sum(1 for t in tr if t.get('standin'))} stand-ins  "
        f"{n_img} culture pictures  {len(d['artists'])} artists")
    return out
