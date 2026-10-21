"""Files the bridge serves from the cache: disk video, stage pictures and their depth maps.

Every route takes its names from a fullmatch on the raw path ([a-z0-9-] slugs, [\\w.-] file names,
11-character YouTube ids), so nothing like ../ or %2f reaches the file system. With
LINER_NOTES_PILOT_CACHE set, video is also read from its video/ folder.
"""

import hashlib
import re
import threading
from collections import OrderedDict
from pathlib import Path

from .. import paths
from . import contract

VIDEO, PICTURE, COVER, DEPTH, TYPES = contract.VIDEO, contract.PICTURE, contract.COVER, contract.DEPTH, contract.TYPES
UNSATISFIABLE, parse_range = contract.UNSATISFIABLE, contract.parse_range


def video_dirs():
    pilot = paths.pilot_cache()
    return [paths.cache() / "video", *([pilot / "video"] if pilot else [])]


def find_video(yt, dirs=None):
    for d in dirs or video_dirs():
        f = Path(d) / f"{yt}.mp4"
        if f.is_file():
            return f
    return None


def video_ids(dirs=None):
    ids = set()
    for d in dirs or video_dirs():
        d = Path(d)
        if d.is_dir():
            ids.update(f.stem for f in d.glob("*.mp4") if re.fullmatch(r"[\w-]{11}", f.stem))
    return sorted(ids)


class Depth:
    """Depth maps are named by the sha256 of the picture they belong to: cache()/depth/<hash>.png."""

    def __init__(self, size=1024):
        self.lock, self.size, self.hashes = threading.Lock(), size, OrderedDict()

    def digest(self, pic):
        st = pic.stat()
        k = (str(pic), st.st_mtime_ns, st.st_size)
        with self.lock:
            if k in self.hashes:
                self.hashes.move_to_end(k)
                return self.hashes[k]
        h = hashlib.sha256(pic.read_bytes()).hexdigest()
        with self.lock:
            self.hashes[k] = h
            while len(self.hashes) > self.size:
                self.hashes.popitem(last=False)
        return h

    def find(self, slug, folder, name, journey_dir=None):
        """The depth map for /d/<slug>/<folder>/<name>, or None."""
        pics = [paths.cache() / folder / name]
        if journey_dir:
            pics.append(Path(journey_dir) / "media" / folder / name)  # era pictures ship with the journey
        for pic in pics:
            if pic.is_file():
                try:
                    h = self.digest(pic)
                except OSError:
                    continue
                for d in (paths.cache() / "depth", Path(journey_dir) / "media/depth" if journey_dir else None):
                    if d and (d / f"{h}.png").is_file():
                        return d / f"{h}.png"
        return None


def picture(folder, name):
    f = paths.cache() / folder / name
    return f if f.is_file() else None


def cover(key, matcher=None):
    """cache()/covers/<key>.webp, where key is a song id or a YouTube id. A YouTube id with no file of
    its own is looked up in the bank's aliases and served as that song's cover."""
    d = paths.cache() / "covers"
    f = d / f"{key}.webp"
    if f.is_file():
        return f
    if matcher is not None and contract.YT_ID.fullmatch(key):
        sid = matcher.by_alias.get(("youtube", key))
        if sid and (d / f"{sid}.webp").is_file():
            return d / f"{sid}.webp"
    return None
