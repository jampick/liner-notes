"""`liner-notes build <slug>` on a journey in the making: copy its assembled journey.json and its
media from the work folder into your bank in user data, so it's live from then on. This is the only
step that puts a work journey where list, play, the keys and check can see it."""

import shutil

from .. import paths
from ..bank import Bank, read_json, write_checked
from . import journeytoml
from .journeytoml import Problem


class Published:
    """Where a work journey went, and how to put things back if its page is refused."""

    def __init__(self, dir, before):
        self.dir, self.before = dir, before

    def undo(self):
        f = self.dir / "journey.json"
        if self.before is None:
            f.unlink(missing_ok=True)
        else:
            paths.atomic_write(f, self.before)


def publish(slug):
    """A Published for the journey's folder in your bank, or None when there's no assembled work
    journey. The caller undoes it when the build is refused, so a journey whose page can't be made
    doesn't go live."""
    src = journeytoml.folder(slug)
    if not (src / "journey.json").is_file():
        return None
    dest = paths.user_data() / "journeys" / slug
    bank = Bank()
    if slug in bank.journeys and bank.journey_dirs[slug].resolve() != dest.resolve():
        raise Problem(f"there's already a journey called {slug} at {bank.journey_dirs[slug]}; yours stays in {src}")
    before = (dest / "journey.json").read_bytes() if (dest / "journey.json").is_file() else None
    write_checked(dest / "journey.json", read_json(src / "journey.json"), "journey")
    if (src / "media").is_dir():
        paths.guard(dest / "media")
        shutil.copytree(src / "media", dest / "media", dirs_exist_ok=True)
    return Published(dest, before)
