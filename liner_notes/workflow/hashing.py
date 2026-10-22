"""Content hashes for staleness. A file is hashed as its bytes, so any edit counts, reformatting
included. A value with no file of its own (the seed, the computed song list) is hashed as canonical
JSON."""

import hashlib
import json
from pathlib import Path


def of_file(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def of_value(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
