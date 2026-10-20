import hashlib

import pytest

from liner_notes.bridge.media import UNSATISFIABLE, parse_range

YT = "AbCdEfGhIjK"
BODY = bytes(range(256)) * 4  # 1024 bytes


@pytest.fixture
def media(env):
    v = env["tmp"] / "cache/video"
    v.mkdir(parents=True)
    (v / f"{YT}.mp4").write_bytes(BODY)
    (env["tmp"] / "pilot-video").mkdir()
    (env["tmp"] / "pilot-video/PiLoTvIdEo1.mp4").write_bytes(b"old cache")
    (env["tmp"] / "pilot-video/AbCdEfGhIjK.mp4").write_bytes(b"shadowed")
    c = env["tmp"] / "cache/culture"
    c.mkdir()
    (c / "poster.webp").write_bytes(b"RIFFposter")
    d = env["tmp"] / "cache/depth"
    d.mkdir()
    (d / f"{hashlib.sha256(b'RIFFposter').hexdigest()}.png").write_bytes(b"\x89PNGdepth")
    era = env["root"] / "journeys/demo/media/eras"
    era.mkdir(parents=True)
    (era / "era1.webp").write_bytes(b"era picture")
    (d / f"{hashlib.sha256(b'era picture').hexdigest()}.png").write_bytes(b"\x89PNGera")
    secret = env["tmp"] / "secret.txt"
    secret.write_text("private")
    return env


@pytest.mark.parametrize(
    "header,want",
    [
        (None, None),
        ("", None),
        ("bytes=0-99", (0, 99)),
        ("bytes=1000-", (1000, 1023)),
        ("bytes=1000-5000", (1000, 1023)),
        ("bytes=-100", (924, 1023)),
        ("bytes=-5000", (0, 1023)),
        ("bytes=1024-", UNSATISFIABLE),
        ("bytes=-0", UNSATISFIABLE),
        ("bytes=-", None),
        ("bytes=5-1", None),
        ("bytes=0-1,5-9", None),
        ("items=0-1", None),
        ("bytes=abc", None),
    ],
)
def test_parse_range(header, want):
    assert parse_range(header, 1024) == want


def test_video_ranges(start, media):
    b = start()
    st, body, h = b.req("GET", f"/v/{YT}.mp4")
    assert st == 200 and body == BODY and h["Accept-Ranges"] == "bytes" and h["Content-Length"] == "1024"
    assert "no-store" not in h["Cache-Control"] and h["ETag"] and h["Last-Modified"]
    st, body, h = b.req("GET", f"/v/{YT}.mp4", headers={"Range": "bytes=100-199"})
    assert st == 206 and body == BODY[100:200] and h["Content-Range"] == "bytes 100-199/1024"
    st, body, h = b.req("GET", f"/v/{YT}.mp4", headers={"Range": "bytes=-24"})
    assert st == 206 and body == BODY[-24:]
    st, body, h = b.req("GET", f"/v/{YT}.mp4", headers={"Range": "bytes=2000-"})
    assert st == 416 and h["Content-Range"] == "bytes */1024" and body == b""
    st, body, _ = b.req("GET", f"/v/{YT}.mp4", headers={"Range": "bytes=-"})
    assert st == 200 and body == BODY  # E20: this used to be a 206
    st, _, _ = b.req("GET", f"/v/{YT}.mp4", headers={"If-None-Match": h["ETag"]})
    assert st == 304


def test_video_falls_back_to_the_pilot_cache(start, media):
    b = start()
    assert b.req("GET", "/v/PiLoTvIdEo1.mp4")[1] == b"old cache"
    assert b.req("GET", f"/v/{YT}.mp4")[1] == BODY  # the new cache wins
    assert b.get("/v/") == {"ok": True, "ids": [YT, "PiLoTvIdEo1"]}


def test_pictures_and_depth_maps(start, media):
    b = start()
    st, body, h = b.req("GET", "/m/demo/culture/poster.webp")
    assert st == 200 and body == b"RIFFposter" and h["Content-Type"] == "image/webp"
    assert b.req("GET", "/d/demo/culture/poster.webp")[1] == b"\x89PNGdepth"
    assert b.req("GET", "/d/demo/eras/era1.webp")[1] == b"\x89PNGera"  # an era picture that ships with the journey
    assert b.req("GET", "/d/demo/culture/missing.webp")[0] == 404


TRAVERSALS = [
    "/v/../../secret.txt",
    "/v/..%2f..%2fsecret.mp4",
    "/v/%2e%2e%2fsecret12.mp4",
    "/v/AbCdEfGhIjK.mp4/../x",
    "/v/AbCdEfGhIjK.mp4%00",
    "/m/demo/culture/../../secret.txt",
    "/m/demo/culture/..%2f..%2fsecret.webp",
    "/m/demo/../culture/poster.webp",
    "/m/demo/culture/.poster.webp",
    "/m/demo/covers/poster.webp",
    "/m/../culture/poster.webp",
    "/m/demo/culture/%2e%2e/poster.webp",
    "/d/demo/culture/../../secret.txt",
    "/d/demo/eras/..%2f..%2fera1.webp",
    "/d/demo/depth/x.png",
    "/d/Demo/culture/poster.webp",
    "/p/../secret.html",
    "/p/..%2fsecret.html",
    "/p/demo.html/../x",
    "/p/.%2e/secret.stage.json",
    "/journey/../songs/x.json",
    "/journey/..%2fx.json",
    "/song/../../etc.json",
    "/song/%2e%2e%2fpasswd.json",
]


@pytest.mark.parametrize("path", TRAVERSALS)
def test_no_way_out_of_the_cache(start, media, path):
    b = start()
    st, body, _ = b.req("GET", path)
    assert st in (400, 404), (path, st)
    assert b"private" not in body
