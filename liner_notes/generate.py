"""Make a journey from your own playlist, and write the songs nobody has written yet.

    liner-notes generate --from "Yacht Rock"                     a saved cliamp playlist (the .toml)
    liner-notes generate --provider navidrome --playlist 42      any cliamp provider, read-only over IPC
    liner-notes generate --from "Yacht Rock" --write --budget 5  also write missing notes, up to $5

Without --write it costs nothing: it reads the tracks, matches them against the song bank, looks
up release years on MusicBrainz, cuts eras from the years and writes the journey. Songs already in
the bank keep their notes. The rest are listed, and the page shows them with what cliamp knows.

With --write, each missing song gets one Claude API request (Claude Opus 5.5 with web search and
web fetch) that follows prompts/SONG-BRIEF.md and must list its sources. The brief is the cached
system prompt, so every song after the first reads it at the cache price. Every written song is
marked a draft and AI-written; nothing becomes "reviewed" until a person says so.

The budget is checked before every song, the first one included: the next song is assumed to cost
the larger of ESTIMATE and the average so far, and the run stops if that could pass --budget. Every
request counts, including the ones that end in a refusal or a reply that won't parse.
"""

import datetime as dt
import json
import re
import time
import tomllib
from pathlib import Path

from . import paths
from .bank import Bank, read_json, write_json
from .ids import clean_artist, clean_title, slug, song_id, split_upload_title, youtube_id
from .match import Matcher

MODEL = "claude-sonnet-5-5"
# Claude Sonnet 5.5 list prices, USD per million tokens, and web search per request (as of 2026-10).
PRICE = {"input": 2.00, "output": 10.00, "cache_read": 0.10, "cache_write": 2.50, "search": 0.01}
# What one song costs before we've seen one: about 30k input and 6k output tokens and 8 searches.
ESTIMATE = (30_000 * PRICE["input"] + 6_000 * PRICE["output"]) / 1e6 + 8 * PRICE["search"]
BRIEF = paths.PACKAGE / "prompts" / "SONG-BRIEF.md"
DEFAULT_COPY = {
    "start": "Start listening",
    "resume": "Pick up at",
    "express": "Express: one per era",
    "companion": "Already playing this in your player? Companion mode keeps the previews quiet until you ask for one.",
    "panelNote": "",
    "endKept": "These are the ones you kept. The link below carries them.",
    "endEmpty": "You didn't keep anything this time. Press K on a stop to keep it.",
    "credits": "",
}
FONTS = "https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,300;0,400;0,500;1,300;1,400;1,500&family=Chivo+Mono:wght@400;500&display=swap"


def tracks_from_toml(name):
    f = paths.cliamp_dir() / "playlists" / f"{name}.toml"
    if not f.exists():
        raise SystemExit(f"liner-notes: no cliamp playlist {name!r} at {f}")
    return tomllib.loads(f.read_text()).get("track") or []


def tracks_from_provider(provider, playlist):
    from . import cliamp

    out, off = [], 0
    while True:
        res = cliamp.result("provider.tracks", {"provider": provider, "playlist": playlist, "offset": off, "limit": 500}, timeout=60)
        rows = (res or {}).get("tracks") or []
        out += rows
        if len(rows) < 500:
            return out
        off += len(rows)


def tidy(track):
    """Title and artist as the song is known, not as the upload is labelled."""
    t, a = split_upload_title(track.get("title") or "", track.get("artist") or "")
    return clean_title(t), clean_artist(a)


def cut_eras(years, most=5, least_per=4):
    """Split the sorted years into up to `most` eras of similar size, breaking at the widest gaps."""
    ys = sorted(years)
    n = max(1, min(most, len(ys) // least_per or 1))
    if n == 1 or ys[0] == ys[-1]:
        return [(ys[0], ys[-1])]
    target = len(ys) / n
    cuts, start = [], 0
    for _k in range(n - 1):
        want = int(start + target)
        best, gap_best = None, -1
        for i in range(max(start + 1, want - int(target / 2)), min(len(ys) - 1, want + int(target / 2)) + 1):
            if ys[i] != ys[i - 1] and ys[i] - ys[i - 1] > gap_best - 0.001 * abs(i - want):
                best, gap_best = i, ys[i] - ys[i - 1]
        if best is None:
            continue
        cuts.append(best)
        start = best
    bounds, prev = [], 0
    for c in [*cuts, len(ys)]:
        bounds.append((ys[prev], ys[c - 1]))
        prev = c
    return bounds


def plan(tracks, name, bank=None, lookup=None, log=print):
    """Build the journey and stub songs. Returns (journey, new song records, ids missing notes)."""
    from .resolve import resolve

    bank = bank or Bank()
    lookup = lookup or resolve
    m = Matcher(bank.songs)
    order, pins, new = [], {}, {}
    for t in tracks:
        sid = m.match(t)
        yt = youtube_id(t.get("path"))
        if not sid:
            title, artist = tidy(t)
            sid = song_id(artist, title)
            if sid not in bank.songs and sid not in new:
                info = lookup(artist, title, yt) or {}
                new[sid] = {
                    "id": sid,
                    "title": title,
                    "artist": artist,
                    "year": info.get("year") or 0,
                    "year_note": "",
                    "album": "",
                    **({"mbid": info["mbid"]} if info.get("mbid") else {}),
                    "isrc": info.get("isrc") or [],
                    "aliases": {"youtube": [yt] if yt else [], **({"local": [t["path"]]} if t.get("path", "").startswith("/") else {})},
                    "notes": {},
                    "culture": [],
                    "review": {"state": "draft", "by": None, "date": None},
                    "provenance": {"ai_generated": False, "written_by": None, "written_on": None},
                }
                log(f"  {title}, {artist}: {info.get('year') or 'year unknown'}")
        if sid not in order:
            order.append(sid)
            if yt:
                pins[sid] = {"youtube": yt}
    every = {**bank.songs, **new}
    years = [every[s]["year"] for s in order if every[s].get("year")]
    if not years:
        raise SystemExit("liner-notes: no release years found; can't cut eras")
    bounds = cut_eras(years)
    bounds[0] = (min(bounds[0][0], min(years)), bounds[0][1])
    for s in order:  # songs with no year go in the last era rather than vanish
        if not every[s].get("year"):
            every[s]["year"] = bounds[-1][1]
    eras = [
        {
            "n": i,
            "name": f"{lo} to {hi}" if lo != hi else str(lo),
            "years": f"{lo}–{str(hi)[2:]}" if lo != hi else str(lo),
            "from": lo,
            "to": hi,
            "essay": "",
            "scene": {"cities": [], "labels": [], "gear": [], "artifact": ""},
            "sources": [],
            "image": "",
        }
        for i, (lo, hi) in enumerate(bounds, 1)
    ]
    words = name.split()
    journey = {
        "slug": slug(name),
        "title": name,
        "titleA": " ".join(words[: max(1, len(words) // 2)]),
        "titleB": " ".join(words[max(1, len(words) // 2) :]),
        "kicker": f"{len(order)} songs · {min(years)} to {max(years)}",
        "description": f"{name}: {len(order)} songs from {min(years)} to {max(years)}, with notes on each.",
        "lede": "",
        "fonts": FONTS,
        "copy": dict(DEFAULT_COPY),
        "panel": {},
        "eras": eras,
        "songs": order,
        "pins": pins,
        "threads": [],
        "review": {"state": "draft", "by": None, "date": None},
        "generated": True,
    }
    missing = [s for s in order if not (every[s].get("notes") or {}).get("made")]
    return journey, new, missing


def parse_reply(text):
    m = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    raw = m[-1] if m else text[text.find("{") : text.rfind("}") + 1]
    d = json.loads(raw)
    for k in ("title", "artist", "year", "made", "context", "sources"):
        if k not in d:
            raise ValueError(f"missing {k}")
    if not d["sources"]:
        raise ValueError("no sources")
    if "—" in json.dumps(d, ensure_ascii=False):
        d = json.loads(json.dumps(d, ensure_ascii=False).replace(" — ", ", ").replace("—", ", "))
    return d


def cost_of(usage):
    u = usage
    search = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    return (
        (u.input_tokens or 0) * PRICE["input"]
        + (u.output_tokens or 0) * PRICE["output"]
        + (getattr(u, "cache_read_input_tokens", 0) or 0) * PRICE["cache_read"]
        + (getattr(u, "cache_creation_input_tokens", 0) or 0) * PRICE["cache_write"]
    ) / 1e6 + search * PRICE["search"]


class Meter:
    """Money spent on requests, counted as each answer arrives, so a failure later still counts."""

    def __init__(self):
        self.spent = 0.0

    def add(self, usage):
        c = cost_of(usage)
        self.spent += c
        return c


def write_song(client, song, effort="medium", log=print, meter=None):
    """One song's notes from the Claude API. Returns (notes dict, cost in USD, model)."""
    import anthropic

    meter = meter or Meter()
    start = meter.spent
    system = [{"type": "text", "text": BRIEF.read_text(), "cache_control": {"type": "ephemeral"}}]
    ask = (
        f'Write the liner notes for "{song["title"]}" by {song["artist"]}'
        + (f" (released {song['year']}, per MusicBrainz; check it)" if song.get("year") else "")
        + "."
    )
    messages = [{"role": "user", "content": ask}]
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6},
    ]
    final = None
    for _turn in range(5):  # pause_turn: a long server-tool turn hands back control; send it back to continue
        try:
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=32000,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"effort": effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                final = stream.get_final_message()
        except anthropic.RateLimitError as e:
            wait = int(e.response.headers.get("retry-after", "30"))
            log(f"  rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        meter.add(final.usage)
        if final.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": final.content})
            continue
        break
    if final is None:
        raise RuntimeError("no answer")
    if final.stop_reason == "refusal":
        raise RuntimeError(f"declined ({getattr(final.stop_details, 'category', None)})")
    text = "".join(b.text for b in final.content if b.type == "text")
    try:
        return parse_reply(text), meter.spent - start, final.model
    except (ValueError, json.JSONDecodeError):
        messages += [
            {"role": "assistant", "content": final.content},
            {"role": "user", "content": "Reply again with only the JSON object in a ```json block, in the shape the brief gives."},
        ]
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            system=system,
            messages=messages,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            again = stream.get_final_message()
        meter.add(again.usage)
        return parse_reply("".join(b.text for b in again.content if b.type == "text")), meter.spent - start, again.model


def apply_notes(song, d, model, day):
    song.update(
        {
            "title": d["title"],
            "artist": d["artist"],
            "year": int(d.get("year") or song.get("year") or 0),
            "year_note": d.get("year_note") or "",
            "album": d.get("album") or "",
        }
    )
    song["notes"] = {
        "made": d["made"],
        "context": d["context"],
        "listen_for": d.get("listen_for") or [],
        "leads_to": d.get("leads_to") or {},
        "sources": d["sources"],
        "unverified": d.get("unverified") or "",
    }
    song["culture"] = d.get("culture") or []
    song["review"] = {"state": "draft", "by": None, "date": None}
    song["provenance"] = {
        "ai_generated": True,
        "model": f"{model} with web search, via liner-notes generate",
        "written_by": "liner-notes generate",
        "written_on": day,
    }
    return song


def generate(
    name=None,
    provider=None,
    playlist=None,
    write=False,
    budget=2.0,
    effort="medium",
    root=None,
    client=None,
    lookup=None,
    log=print,
    estimate=ESTIMATE,
):
    root = Path(root or paths.user_data())
    bank = Bank()
    if provider:
        tracks = tracks_from_provider(provider, playlist)
        name = name or f"{provider} {playlist}"
    else:
        tracks = tracks_from_toml(name)
    log(f"{name}: {len(tracks)} tracks")
    journey, new, missing = plan(tracks, name, bank, lookup, log)
    for s in new.values():
        write_json(root / "songs" / f"{s['id']}.json", s)
    write_json(root / "journeys" / journey["slug"] / "journey.json", journey)
    have = len(journey["songs"]) - len(missing)
    log(
        f"journey {journey['slug']}: {len(journey['songs'])} songs, {len(journey['eras'])} eras, {have} already have notes, {len(missing)} don't"
    )
    if not write or not missing:
        if missing:
            log("  add --write --budget <USD> to write the missing ones")
        return journey, 0.0
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    meter, day, done, tried = Meter(), dt.date.today().isoformat(), 0, 0
    log(f"  about ${estimate:.2f} a song before the first answer; budget ${budget:.2f}")
    for sid in missing:
        nxt = max(estimate, meter.spent / tried if tried else 0.0)
        if meter.spent + nxt > budget:
            log(f"  stopping: the next song could cost ${nxt:.2f} and pass the ${budget:.2f} budget (spent ${meter.spent:.2f})")
            break
        f = root / "songs" / f"{sid}.json"
        song = read_json(f) if f.exists() else dict(Bank().songs[sid])
        tried += 1
        try:
            d, cost, model = write_song(client, song, effort, log, meter)
        except Exception as e:
            log(f"  {song['title']}: {e} (spent ${meter.spent:.2f} so far)")
            continue
        done += 1
        write_json(f, apply_notes(song, d, model, day))
        log(f"  wrote {song['title']}, {song['artist']}  ${cost:.3f}  ({len(d['sources'])} sources)")
    log(f"wrote {done} of {tried} songs tried for ${meter.spent:.2f} (${(meter.spent / tried if tried else 0):.3f} a request)")
    return journey, meter.spent
