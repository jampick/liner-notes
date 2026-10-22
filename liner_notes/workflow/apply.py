"""`liner-notes dates-apply`: the one way a journey's corrected years reach the song bank.

Accepting dates.json never writes a song: a song file is shared, and a silent write from one
journey's sign-off would change another journey's page. This command writes each year that
dates.json has and the bank doesn't, when the dates entry's source ranks at or above the bank's
(person, then writer, then MusicBrainz), into your copy of the song in user data, with
year_corrected_from and year_url. It names the other journeys that use each song.
"""

from .. import overlay
from ..bank import write_checked
from . import hashing, journeytoml
from .assemble import disagreements
from .journeytoml import Problem
from .stages import DATES, RANK, Ctx, stage_status


def apply(slug):
    with journeytoml.editing(slug) as doc:
        ctx = Ctx(doc)
        st = stage_status(ctx, DATES)
        if st["state"] != "accepted":
            raise Problem(f"dates is {st['state']}; accept it first: liner-notes accept {slug} dates")
        done = []
        for sid, r, s in disagreements(ctx):
            if RANK.get(r.get("source"), 0) < RANK.get(s.get("year_source"), 0):
                continue
            f, song = overlay.copy_for_writing(sid, ctx.bank)
            song["year_corrected_from"] = song.get("year")
            song["year"], song["year_source"] = r["year"], r.get("source")
            if r.get("url"):
                song["year_url"] = r["url"]
            write_checked(f, song, "song")
            others = [j for j in ctx.bank.journeys_with(sid)]
            done.append(
                {
                    "song": sid,
                    "title": s["title"],
                    "from": song["year_corrected_from"],
                    "year": r["year"],
                    "file": str(f),
                    "journeys": others,
                }
            )
        if done:
            # The bank now agrees with the dates you accepted, so that change doesn't stale them.
            ctx.__dict__.pop("bank", None)
            doc["stages"]["dates"]["inputs"]["bank-years"] = ctx.input_hash("bank-years")
            doc["stages"]["dates"]["output"] = hashing.of_file(ctx.dir / DATES.file)
    return done
