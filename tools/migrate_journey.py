#!/usr/bin/env python3
"""Convert a journeys-pilot playlist folder into songs/, culture/ and journeys/<slug>/.

python3 tools/migrate_journey.py ~/Projects/Music/journeys/playlists/<slug> [--root DIR] [--cache DIR]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liner_notes.migrate import migrate  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--root", help="data root to write into (default: this checkout)")
    ap.add_argument("--cache", help="cache for pictures other people own (default: ~/.cache/liner-notes)")
    a = ap.parse_args()
    from liner_notes.migrate import migrate_remix  # noqa: E402

    if (Path(a.folder).expanduser() / "remixes.json").exists():
        migrate_remix(a.folder, a.root)
    else:
        migrate(a.folder, a.root, a.cache)
