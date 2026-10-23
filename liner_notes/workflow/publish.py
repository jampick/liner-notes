"""`liner-notes build <slug>` on a journey in the making.

Without --out, build copies the assembled journey.json and its media from the work folder into your
bank in user data, and only keeps them there if the page is built: a refused or failed build takes
them away again, folder and all. That's the one step that puts a work journey where list, play, the
keys and check can see it. With --out, build makes a preview from the work folder and copies nothing.

journey.toml remembers the hash of what was copied, so a hand edit to the bank copy since (pins, say)
isn't overwritten without --replace.
"""

import shutil

from .. import paths
from ..bank import Bank, read_json, write_checked
from . import hashing, journeytoml
from .journeytoml import Problem


def work_journey(slug):
    f = journeytoml.folder(slug) / "journey.json"
    return read_json(f) if f.is_file() else None


def missing_pictures(slug):
    """Plain words for each era of an assembled work journey that has no picture on disk."""
    j = work_journey(slug)
    d = journeytoml.folder(slug)
    out = []
    for e in (j or {}).get("eras") or []:
        if not (e.get("image") and (d / e["image"]).is_file()):
            out.append(
                f"era {e['n']} ({e['name']}) has no picture. Until era cards without a picture arrive (phase 1 item 8), "
                f'build wants one: put it in media/ in {d}, add "image": "media/era-{e["n"]}.webp" to that era in '
                f"eras.json, then liner-notes accept {slug} eras and liner-notes assemble {slug}. Or build --force, "
                f"and the era shows an empty panel."
            )
    return out


def preview_bank(slug):
    """The bank, with the work journey standing in for any live one of the same slug."""
    bank = Bank()
    bank.journeys[slug] = work_journey(slug)
    bank.journey_dirs[slug] = journeytoml.folder(slug)
    return bank


class Published:
    """Where a work journey went, and how to take it back if its page isn't built."""

    def __init__(self, slug, dest, before, made_dir):
        self.slug, self.dir, self.before, self.made_dir = slug, dest, before, made_dir

    def undo(self):
        if self.made_dir:
            shutil.rmtree(self.dir, ignore_errors=True)
        elif self.before is None:
            (self.dir / "journey.json").unlink(missing_ok=True)
        else:
            paths.atomic_write(self.dir / "journey.json", self.before)

    def keep(self):
        with journeytoml.editing(self.slug) as doc:
            doc["published"] = {"hash": hashing.of_file(self.dir / "journey.json")}


def publish(slug, replace=False):
    """A Published for the journey's folder in your bank, or None when there's no assembled work
    journey. The caller keeps it once the page is built and undoes it otherwise."""
    src = journeytoml.folder(slug)
    if not (src / "journey.json").is_file():
        return None
    dest = paths.user_data() / "journeys" / slug
    bank = Bank()
    if slug in bank.journeys and bank.journey_dirs[slug].resolve() != dest.resolve():
        raise Problem(f"there's already a journey called {slug} at {bank.journey_dirs[slug]}; yours stays in {src}")
    f = dest / "journey.json"
    before = f.read_bytes() if f.is_file() else None
    if before is not None and not replace:
        last = (journeytoml.load(slug).get("published") or {}).get("hash")
        if hashing.of_file(f) != last:
            raise Problem(
                f"{f} was edited since the last build, and building again would overwrite it with the assembled "
                f"journey. Move your change into the work files (or keep a copy), then build --replace"
            )
    made_dir = not dest.exists()
    try:
        write_checked(f, read_json(src / "journey.json"), "journey")
        if (src / "media").is_dir():
            paths.guard(dest / "media")
            shutil.copytree(src / "media", dest / "media", dirs_exist_ok=True)
    except BaseException:
        Published(slug, dest, before, made_dir).undo()
        raise
    return Published(slug, dest, before, made_dir)
