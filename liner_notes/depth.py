"""Depth maps for the stage's pictures, so the page can move them in depth parallax.

    liner-notes depth            every picture in the cache and every era picture in the journeys

Depth Anything V2 Small (ONNX) on the CPU, the same model the Astro-Arc moving background uses:
one 8-bit grey PNG per picture, white is near, the near side of every edge grown and then blurred so
a figure moves whole instead of tearing at its rim. A map is keyed by the SHA-256 of the picture's
bytes and lives in ~/.cache/liner-notes/depth/<hash>.png; the bridge hashes the picture it's asked
about and serves the map that matches.

Optional: it needs the [depth] extra (numpy, pillow, onnxruntime) and the model file, set with
[depth] model in config.toml or LINER_NOTES_DEPTH_MODEL. Without them it says why and does nothing,
and the stage shows the pictures standing still.
"""

import hashlib
import os
from pathlib import Path

from . import config, paths

DIRS = ("artists", "culture", "covers")
PICTURES = (".webp", ".jpg", ".jpeg", ".png")
# The stage pane is about 1150 px wide and the shader reads the map blurred, so 768 holds everything.
MAX_WIDTH = 768


def model_path():
    p = os.environ.get("LINER_NOTES_DEPTH_MODEL") or config.load()["depth"]["model"]
    return Path(p).expanduser()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Depth:
    def __init__(self, model):
        import numpy as np
        import onnxruntime as ort
        from PIL import Image, ImageFilter

        self.np, self.Image, self.ImageFilter = np, Image, ImageFilter
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)  # leave cores for whatever else is running
        self.sess = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        self.name = self.sess.get_inputs()[0].name

    def make(self, src, out):
        np, Image, ImageFilter = self.np, self.Image, self.ImageFilter
        img = Image.open(src).convert("RGB")
        W, H = img.size
        # The ViT patch is 14 px; the short side at 518 like the reference preprocessing.
        if W >= H:
            h, w = 518, max(14, int(round(W / H * 518 / 14)) * 14)
        else:
            w, h = 518, max(14, int(round(H / W * 518 / 14)) * 14)
        x = np.asarray(img.resize((w, h), Image.BICUBIC), dtype=np.float32) / 255.0
        x = ((x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
        disp = self.sess.run(None, {self.name: x})[0].squeeze()  # relative inverse depth, larger is nearer
        lo, hi = np.percentile(disp, [2, 98])
        d = np.clip((disp - lo) / max(hi - lo, 1e-6), 0, 1)
        ow = min(W, MAX_WIDTH)
        oh = max(1, round(H * ow / W))
        dimg = Image.fromarray((d * 255).astype(np.uint8)).resize((ow, oh), Image.BICUBIC)
        r = max(1, round(ow / 256))
        dimg = dimg.filter(ImageFilter.MaxFilter(2 * r + 1)).filter(ImageFilter.GaussianBlur(radius=r))
        import io

        buf = io.BytesIO()
        dimg.save(buf, format="PNG", optimize=True)
        paths.atomic_write(out, buf.getvalue())


def pictures(bank=None, cache=None):
    from .bank import Bank

    cache = Path(cache or paths.cache())
    out = [p for d in DIRS for p in sorted((cache / d).glob("*")) if p.suffix.lower() in PICTURES]
    bank = bank or Bank()
    for slug, jdir in sorted(bank.journey_dirs.items()):
        for e in bank.journeys[slug].get("eras") or []:
            if e.get("image") and (jdir / e["image"]).is_file():
                out.append(jdir / e["image"])
    return out


def ensure(bank=None, cache=None, maker=None, log=print):
    """Make the missing maps. Returns (made, total), or None when the model or the extra is missing."""
    cache = Path(cache or paths.cache())
    pics = pictures(bank, cache)
    todo = [(p, cache / "depth" / f"{sha(p)}.png") for p in pics]
    todo = [(p, out) for p, out in todo if not out.exists()]
    if not todo:
        return 0, len(pics)
    if maker is None:
        model = model_path()
        if not model.is_file():
            log(f"depth: no model at {model}; pictures stay still")
            return None
        try:
            maker = Depth(model)
        except ImportError as e:
            log(f"depth: {e} (pip install 'liner-notes[depth]'); pictures stay still")
            return None
    made = 0
    for k, (p, out) in enumerate(todo, 1):
        try:
            maker.make(p, out)
            made += 1
            log(f"  depth {k}/{len(todo)} {p.parent.name}/{p.name}")
        except Exception as e:  # one bad picture shouldn't stop the rest
            log(f"  depth failed: {p.name}: {e}")
    return made, len(pics)
