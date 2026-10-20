"""Files the bridge serves from the cache: disk video, stage pictures and their depth maps.

Every route takes its names from a fullmatch on the raw path ([a-z0-9-] slugs, [\\w.-] file names,
11-character YouTube ids), so nothing like ../ or %2f reaches the file system. During the move
from the pilot, video is also read from ~/.cache/journeys/video.
"""
import hashlib
import re
import threading
from collections import OrderedDict
from pathlib import Path

from .. import paths

PILOT_VIDEO = Path.home() / ".cache/journeys/video"
VIDEO = re.compile(r"/v/([\w-]{11})\.mp4")
PICTURE = re.compile(r"/m/([a-z0-9-]+)/(culture|artists)/([\w-][\w.-]*\.(webp|jpg|png))")
DEPTH = re.compile(r"/d/([a-z0-9-]+)/(culture|artists|eras|covers)/([\w-][\w.-]*\.(?:webp|jpg|png))")
TYPES = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}
UNSATISFIABLE = "unsatisfiable"


def video_dirs():
    return [paths.cache() / "video", PILOT_VIDEO]


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


def parse_range(header, size):
    """RFC 7233 for one byte range: None means "send it all with 200" (no header, a malformed one,
    several ranges, or `bytes=-`), UNSATISFIABLE means 416, else (first, last) inclusive."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if not m or not (m.group(1) or m.group(2)):
        return None
    a, b = m.groups()
    if a:
        first = int(a)
        last = int(b) if b else size - 1
        if b and last < first:
            return None  # not a valid range, so the header is ignored
        if first >= size:
            return UNSATISFIABLE
        return first, min(last, size - 1)
    n = int(b)
    if n == 0 or size == 0:
        return UNSATISFIABLE
    return max(0, size - n), size - 1


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
