"""A Home Assistant media_player, over HA's WebSocket API.

One Home Assistant install already speaks to Sonos, Chromecast, Apple TV, AirPlay speakers, Plex,
Kodi, Music Assistant and dozens more, so this is the widest single input Liner Notes has.

Settings: LINER_NOTES_HA_URL (e.g. http://homeassistant.local:8123), LINER_NOTES_HA_TOKEN (a
long-lived access token from your HA profile) and LINER_NOTES_HA_ENTITY (media_player.living_room).

Protocol: connect to /api/websocket, answer auth_required with the token, then send
{"id": 1, "type": "subscribe_entities", "entity_ids": [entity]}. Events carry compressed state:
{"a": {entity: {"s": state, "a": attrs}}} for the first full state and {"c": {entity: {"+": {"s", "a"}}}}
for changes (attribute removals under "-"). Attributes used: media_title, media_artist,
media_album_name, media_duration, media_position, media_position_updated_at, media_content_id.

Status: the parser is tested; the small stdlib WebSocket client below hasn't met a live HA yet.
"""
import base64
import datetime as dt
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse

from . import Adapter, NowPlaying


def merge(entity_state, event, entity):
    """Apply one subscribe_entities event to our copy of the entity's state ({"s", "a"})."""
    if entity in (event.get("a") or {}):
        entity_state.clear()
        entity_state.update(event["a"][entity])
        entity_state.setdefault("a", {})
    ch = (event.get("c") or {}).get(entity)
    if ch:
        plus, minus = ch.get("+") or {}, ch.get("-") or {}
        if "s" in plus:
            entity_state["s"] = plus["s"]
        attrs = entity_state.setdefault("a", {})
        attrs.update(plus.get("a") or {})
        for k in minus.get("a") or []:
            attrs.pop(k, None)
    return to_now(entity_state)


def to_now(st):
    a = (st or {}).get("a") or {}
    if not a.get("media_title"):
        return None
    try:
        at = dt.datetime.fromisoformat(str(a.get("media_position_updated_at")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        at = time.time()
    ids = {"ha_content_id": a["media_content_id"]} if a.get("media_content_id") else {}
    cid = str(a.get("media_content_id") or "")
    if cid.startswith("spotify:track:"):
        ids["spotify_id"] = cid.rsplit(":", 1)[1]
    return NowPlaying(title=a["media_title"], artist=a.get("media_artist") or "", album=a.get("media_album_name") or "",
                      duration=float(a.get("media_duration") or 0), position=float(a.get("media_position") or 0), position_at=at,
                      state={"playing": "playing", "paused": "paused"}.get(st.get("s"), "stopped"), source="homeassistant", ids=ids)


class _WS:
    """Just enough RFC 6455 for a JSON client: text frames, masking, ping/pong."""

    def __init__(self, url, timeout=30):
        u = urllib.parse.urlparse(url)
        port = u.port or (443 if u.scheme in ("https", "wss") else 80)
        raw = socket.create_connection((u.hostname, port), timeout=timeout)
        self.s = ssl.create_default_context().wrap_socket(raw, server_hostname=u.hostname) if u.scheme in ("https", "wss") else raw
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /api/websocket HTTP/1.1\r\nHost: {u.hostname}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(1024)
            if not chunk:
                raise ConnectionError("closed during handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = b""
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            if op == 0x9:  # ping
                self.s.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if op == 0x8:
                raise ConnectionError("closed by server")
            parts += payload
            if b1 & 0x80:
                return json.loads(parts)


class HomeAssistantAdapter(Adapter):
    name = "homeassistant"
    capabilities = frozenset({"events"})

    def __init__(self, url=None, token=None, entity=None):
        self.url = url or os.environ.get("LINER_NOTES_HA_URL")
        self.token = token or os.environ.get("LINER_NOTES_HA_TOKEN")
        self.entity = entity or os.environ.get("LINER_NOTES_HA_ENTITY")
        if not (self.url and self.token and self.entity):
            raise TypeError("Home Assistant needs a URL, a token and an entity")
        self.state, self.last = {}, None

    def available(self):
        return True

    def now(self):
        return self.last

    def watch(self, interval=2.0):
        ws = _WS(self.url)
        if ws.recv().get("type") == "auth_required":
            ws.send({"type": "auth", "access_token": self.token})
            if ws.recv().get("type") != "auth_ok":
                raise PermissionError("Home Assistant refused the token")
        ws.send({"id": 1, "type": "subscribe_entities", "entity_ids": [self.entity]})
        while True:
            msg = ws.recv()
            if msg.get("type") == "event":
                self.last = merge(self.state, msg.get("event") or {}, self.entity)
                yield self.last
