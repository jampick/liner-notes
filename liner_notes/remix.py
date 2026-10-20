"""Build the remix stage for a journey of remixes (Yacht House).

    liner-notes build yacht-house

A remix journey has no eras of its own. Each remix in its journey.json points at the original it
flips: a song in the bank (whose fact-checked notes, cover and artist photo it borrows), or a bare
lyrics file for an original no journey covers yet. What the remix did to the song comes from the
alignment events in the remix's lyrics file, so those notes can't be wrong about it.

All of the alignment data is personal and stays out of git:

  ~/.local/share/liner-notes/lyrics/<remix id>.json        the remix's lines and its `remix.events`
  ~/.local/share/liner-notes/lyrics/<original id>.json     the original's retimed lines
  ~/.local/share/liner-notes/remixes/originals/<file>      originals with no upload of their own
  ~/.cache/liner-notes/profiles/<id>.json                  tempo and loudness shape of each recording

A profile is measured from the audio cache with numpy and ffmpeg when it's missing (the [remix]
extra); `liner-notes migrate-personal` seeds them from the pilot's built page so nothing needs
measuring again. Covers and photos go into the page as data URIs, video comes from the bridge
(/v/<id>.mp4), so the page is meant to be opened through it. It follows cliamp through /now by
upload id, and by cliamp's title for SoundCloud tracks, which have no id.
"""

import base64
import json
import re
from pathlib import Path

from . import paths
from .bank import Bank, read_json
from .stage import safe, standins

TEMPLATE = paths.PACKAGE / "web" / "remix.html"
MARKS = ("/*__DATA__*/null", "/*DATA*/null")
FALLBACK = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="liner-token" content=""><title>__TITLE__</title></head>
<body><h1>__TITLE__</h1><p>The remix stage template (liner_notes/web/remix.html) isn't installed, so this page only carries its data.</p>
<script>const SONGS = /*__DATA__*/null;</script></body></html>
"""
SR = 11025


def data_uri(path):
    path = Path(path)
    mime = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}[path.suffix.lower()]
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def measure(path):
    """bpm (onset autocorrelation, 70 to 180, leaning to 120), length and loudness per second, 0..1."""
    import subprocess

    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"], capture_output=True, check=True
    ).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop, n = 110, 1024
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    mag = np.abs(np.fft.rfft(frames, axis=1))
    flux = np.maximum(np.diff(np.log1p(mag), axis=0), 0).sum(axis=1)
    flux -= flux.mean()
    ac = np.correlate(flux, flux, "full")[len(flux) - 1 :]
    lags = np.arange(len(ac))
    ok = (lags >= 100 * 60 / 180) & (lags <= 100 * 60 / 70)
    w = np.exp(-0.5 * (np.log2(np.where(lags > 0, 6000 / np.maximum(lags, 1), 1) / 120) / 0.6) ** 2)
    lag = lags[ok][np.argmax((ac * w)[ok])]
    sec = x[: len(x) // SR * SR].reshape(-1, SR)
    r = np.sqrt((sec**2).mean(axis=1))
    r = r / (r.max() or 1)
    return {"bpm": round(6000 / float(lag), 1), "seconds": round(len(x) / SR, 1), "energy": [round(float(v), 3) for v in r]}


def profile(rid, cache=None, log=print):
    """The cached profile for a recording, measured once from the audio cache when it's missing."""
    cache = Path(cache or paths.cache())
    f = cache / "profiles" / f"{rid}.json"
    if f.is_file():
        return read_json(f)
    audio = paths.audio_file(rid)
    if not audio:
        return None
    try:
        p = measure(audio)
    except ImportError:
        log(f"  {rid}: no profile cached, and measuring one needs numpy (pip install 'liner-notes[remix]')")
        return None
    except Exception as e:  # ffmpeg missing or a broken file
        log(f"  {rid}: couldn't measure {audio.name}: {e}")
        return None
    paths.atomic_write(f, json.dumps(p))
    return p


def pages(notes):
    """A note longer than the pane holds is read in two parts, split on a sentence."""
    out = []
    for n in notes:
        p = n["p"]
        if len(p) <= 300:
            out.append(n)
            continue
        cut = [m.end() for m in re.finditer(r"[.;] ", p)]
        k = min(cut, key=lambda c: abs(c - len(p) / 2)) if cut else len(p)
        out += [{"h": n["h"], "p": p[:k].strip()}, {"h": n["h"] + " (cont.)", "p": p[k:].strip()}]
    return out


def rnd(x):
    """Round half up, the way Math.round on the page does, so the notes and the labels agree."""
    return int(x + 0.5)


def mmss(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def flip_notes(events, olines, rbpm, obpm, rlen, olen):
    """What the remix did to the song, said plainly from the alignment. Nothing here is guessed."""
    notes = []
    if obpm and abs(rbpm / obpm - 1) < 0.03:
        notes.append(
            {
                "h": f"Same tempo, about {rnd(rbpm)} BPM",
                "p": f"The remix keeps the original's speed and adds the kick. It runs {mmss(rlen)} against the original's {mmss(olen)}.",
            }
        )
    elif obpm:
        notes.append(
            {
                "h": f"About {rnd(obpm)} to {rnd(rbpm)} BPM",
                "p": f"The remix runs {rbpm / obpm:.2f} times the original's tempo, and {mmss(rlen)} against the original's {mmss(olen)}.",
            }
        )
    else:
        notes.append({"h": f"About {rnd(rbpm)} BPM", "p": f"The remix runs {mmss(rlen)} against the original's {mmss(olen)}."})
    sung = list(dict.fromkeys(ln["text"] for ln in olines if ln["text"]))
    used = {olines[e["line"]]["text"] for e in events if 0 <= e["line"] < len(olines)}
    cut = [t for t in sung if t not in used]
    if cut:
        notes.append(
            {
                "h": f"Keeps {len(sung) - len(cut)} of {len(sung)} lines",
                "p": f"Gone: “{cut[0]}”" + (f" and {len(cut) - 1} more." if len(cut) > 1 else "."),
            }
        )
    loops = sorted(events, key=lambda e: -len(e["times"]))
    if loops and len(loops[0]["times"]) > 2:
        e = loops[0]
        notes.append(
            {
                "h": f"“{e['text']}” ×{len(e['times'])}",
                "p": f"From {mmss(e['times'][0])} it goes round {len(e['times'])} times in {round(e['end'] - e['times'][0])} seconds"
                + (", a few words cut out of the line and stuttered." if e["chop"] else "."),
            }
        )
    back = sum(1 for a, b in zip(events, events[1:], strict=False) if b["orig"] < a["orig"] - 5)
    if back:
        notes.append(
            {
                "h": "Out of order",
                "p": f"The remix jumps back to an earlier part of the original {back} times. "
                "The map under the picture draws each jump as a ribbon from where the line was to where it is now.",
            }
        )
    vocal = sum(e["end"] - e["times"][0] for e in events)
    notes.append(
        {
            "h": f"Voice on {round(100 * vocal / rlen)}% of the time",
            "p": "The rest is the remixer's: the build, the drop and the breakdowns. The liner notes take those stretches.",
        }
    )
    return notes


def _song_by_upload(bank, yt):
    return next((s for s in bank.songs.values() if yt in ((s.get("aliases") or {}).get("youtube") or [])), None)


def _photo(bank, cache, artist):
    names = bank.artists()
    name = artist if artist in names else max((n for n in names if n in artist), key=len, default=None)
    f = cache / "artists" / f"{safe(names[name])}.webp" if name else None
    return data_uri(f) if f and f.is_file() else None


def _video(slug_of_original, yt, cache):
    """The original's own video when it's on disk, else its stand-in (video time = audio time x rate + offset)."""
    if paths.video_file(yt) or (cache / "video" / f"{yt}.mp4").is_file():
        return {"id": yt, "off": 0.0, "rate": 1.0, "kind": None}
    si = standins(slug_of_original).get(yt) if slug_of_original else None
    if si and (paths.video_file(si["standin"]) or (cache / "video" / f"{si['standin']}.mp4").is_file()):
        return {
            "id": si["standin"],
            "off": float(si.get("standin_offset") or 0),
            "rate": float(si.get("standin_rate") or 1.0),
            "kind": si.get("kind"),
        }
    return None


def compose(slug, bank=None, cache=None, log=print):
    """Return (songs, skipped): the list the remix template reads as SONGS, and the remixes left out."""
    bank = bank or Bank()
    cache = Path(cache or paths.cache())
    j = bank.journeys[slug]
    panel = (j.get("panel") or {}).get("quotes") or {}
    out, skipped = [], []
    for r in j["remixes"]:
        lf = paths.lyric_file(r["id"])
        rp = profile(r["id"], cache, log)
        if not lf or not rp:
            skipped.append(r["title"])
            continue
        lyr = read_json(lf)
        if not (lyr.get("remix") or {}).get("events"):
            skipped.append(r["title"])
            continue
        o = r["original"]
        if o.get("youtube"):
            of = paths.lyric_file(o["youtube"])
            if not of:
                skipped.append(r["title"])
                continue
            orig = read_json(of)
            song = bank.songs.get(o.get("song") or "") or _song_by_upload(bank, o["youtube"]) or {}
            home = next((s for s, jj in bank.journeys.items() if song.get("id") in jj.get("songs", [])), None)
            cover = cache / "covers" / f"{song['id']}.webp" if song else None
            year, video = song.get("year"), _video(home, o["youtube"], cache)
            op = profile(o["youtube"], cache, log)
        else:
            of = paths.user_data() / "remixes" / "originals" / o["file"]
            if not of.is_file():
                skipped.append(r["title"])
                continue
            orig, song, cover, year, video, op = read_json(of), {}, None, o.get("year"), None, None
        op = dict(op) if op else {"seconds": orig.get("upload") or 0, "bpm": None, "energy": None}
        olines = [ln for ln in orig["lines"] if ln["text"]]
        # The house kick makes the remix's tempo the reliable one. An original that comes out faster than
        # its own house remix was counted in double time (Biggest Part of Me read 150).
        if op.get("bpm") and op["bpm"] > rp["bpm"] * 1.15:
            op["bpm"] = round(op["bpm"] / 2, 1)
        rcover = cache / "covers" / f"{slug}-vol{r['volume']}.webp" if r.get("volume") else None
        ev = lyr["remix"]["events"]
        notes = song.get("notes") or {}
        orig_notes = [
            x for x in [{"h": "How it was made", "p": notes.get("made")}, {"h": "What was going on", "p": notes.get("context")}] if x["p"]
        ]
        orig_notes += [{"h": f"Listen for {x['where']}", "p": x["note"]} for x in (notes.get("listen_for") or [])[:1]]
        q = panel.get(r["song"])
        out.append(
            {
                "id": r["id"],
                "match": r["title"],
                "title": r["song"],
                "artist": r["artist"],
                "year": year,
                "remixer": r.get("remixer") or r.get("label"),
                "remixYear": r.get("remix_year"),
                "volume": r.get("volume"),
                "video": video,
                "remix": {
                    "length": rp["seconds"],
                    "bpm": rp["bpm"],
                    "energy": rp["energy"],
                    "cover": data_uri(rcover) if rcover and rcover.is_file() else None,
                },
                "original": {
                    "length": op["seconds"],
                    "bpm": op.get("bpm"),
                    "energy": op.get("energy"),
                    "lines": olines,
                    "cover": data_uri(cover) if cover and cover.is_file() else None,
                },
                "events": ev,
                "photo": _photo(bank, cache, r["artist"]),
                "notes": {
                    "orig": pages(orig_notes + r.get("notes", [])),
                    "flip": flip_notes(ev, olines, rp["bpm"], op.get("bpm"), rp["seconds"], op["seconds"]),
                    "panel": [{"h": q["who"], "p": q["quote"]}] if q else [],
                },
            }
        )
    return out, skipped


def template_text(log=print):
    if TEMPLATE.is_file():
        return TEMPLATE.read_text()
    log(f"  {TEMPLATE} isn't there yet; writing a bare page with the data")
    return FALLBACK


def render(data, title, template_html):
    import html as htmllib

    from .build import script_json

    mark = next((m for m in MARKS if m in template_html), None)
    if not mark:
        raise ValueError("the remix template has no data placeholder")
    head, _, tail = template_html.partition(mark)
    t = htmllib.escape(title)
    return head.replace("__TITLE__", t) + script_json(data) + tail.replace("__TITLE__", t)


def build(slug, out=None, public=False, bank=None, cache=None, force=False, log=print):
    from .build import Refused, write_page

    if public:
        raise Refused("a remix stage is built from personal alignments and lyrics, so it has no public build")
    bank = bank or Bank()
    data, skipped = compose(slug, bank, cache, log)
    problems = [] if data else ["no remix has its alignment, lyrics and profile on this machine (run liner-notes migrate-personal)"]
    html = render(data, bank.journeys[slug]["title"], template_text(log))
    out = Path(out) if out else paths.dist() / f"{slug}.html"
    write_page(out, html, problems, force, log)
    log(f"{out}  {len(html) // 1024} KB  {len(data)} remixes")
    if skipped:
        log(f"  not measured yet, left out: {', '.join(skipped)}")
    return out, problems
