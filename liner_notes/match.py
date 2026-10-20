"""Which song in the bank is cliamp playing?

cliamp describes a track with a title, an artist, a path (a URL, a file or a provider id) and,
for provider tracks, provider_meta. We try exact aliases first (a YouTube id, a file path, a
provider id), then a normalised artist and title, so "Protection (Remastered 2006)" by
"Massive Attack - Topic" still lands on massive-attack--protection.
"""

from .ids import clean_artist, clean_title, key, split_upload_title, youtube_id

PROVIDER_KEYS = ("spotify", "tidal", "qobuz", "navidrome", "jellyfin", "plex", "emby", "ytmusic", "soundcloud", "local")


class Matcher:
    def __init__(self, songs):
        self.by_alias, self.by_name, self.by_title = {}, {}, {}
        for s in songs.values() if isinstance(songs, dict) else songs:
            self.add(s)

    def add(self, s):
        sid = s["id"]
        al = s.get("aliases") or {}
        for y in al.get("youtube") or []:
            self.by_alias[("youtube", y)] = sid
        for p in PROVIDER_KEYS:
            for v in al.get(p) or []:
                self.by_alias[(p, str(v))] = sid
        names = [s["title"], *(al.get("titles") or [])]
        artists = [s["artist"], *(al.get("artists") or [])]
        for t in names:
            for a in artists:
                self.by_name.setdefault((key(clean_artist(a)), key(clean_title(t))), sid)
            self.by_title.setdefault(key(clean_title(t)), []).append((key(clean_artist(s["artist"])), sid))

    def match(self, track):
        """Return a song id or None. `track` is a cliamp track dict."""
        if not track:
            return None
        path = track.get("path") or ""
        yt = youtube_id(path)
        if yt and ("youtube", yt) in self.by_alias:
            return self.by_alias[("youtube", yt)]
        meta = track.get("provider_meta") or {}
        for p in PROVIDER_KEYS:
            for v in (meta.get(f"{p}_id"), meta.get("id") if meta.get("provider") == p else None):
                if v and (p, str(v)) in self.by_alias:
                    return self.by_alias[(p, str(v))]
        if path and ("local", path) in self.by_alias:
            return self.by_alias[("local", path)]

        title, artist = track.get("title") or "", track.get("artist") or ""
        candidates = [(title, artist), split_upload_title(title, artist)]
        for t, a in candidates:
            k = (key(clean_artist(a)), key(clean_title(t)))
            if k in self.by_name:
                return self.by_name[k]
        # Same title, and one artist credit contains the other ("Khruangbin" vs "Khruangbin & Leon Bridges").
        for t, a in candidates:
            ka = key(clean_artist(a))
            for art, sid in self.by_title.get(key(clean_title(t)), []):
                if ka and art and (ka in art or art in ka):
                    return sid
        return None
