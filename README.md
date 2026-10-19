Liner Notes is a second screen for cliamp.

Put it on a monitor, a tablet or a phone next to your player. It shows what's playing at reading distance and plays the liner notes along with the song: what to listen for in the intro while the intro plays, how the record was made, what was going on that year, the lyrics as they're sung, and one record to chase next. It follows the player. Skip, shuffle or jump around the playlist, and the page keeps up.

It started as a page for one playlist, Downtempo Drift (49 songs, Bristol 1991 to Texas 2020), and turned into a small engine for any playlist. That journey ships here as the example.

An AI model wrote every note in the bank, working from the sources listed in each file. As of 0.2.0 all 49 songs are drafts: nobody has checked them against their sources yet, and the page labels them "Draft notes".

## What you need

- Python 3.11 or newer. The bridge and the page builder use only the standard library.
- [cliamp](https://github.com/bjarneo/cliamp) 2.0 or newer for the full experience (it speaks the V2 IPC this uses). Any MPRIS player on Linux works too, with less: no lyrics, no history, no jumping to a song.
- Optional: Pillow (`pip install 'liner-notes[media]'`) to fetch covers and pictures, and the `anthropic` SDK plus an API key (`pip install 'liner-notes[generate]'`) to write notes for your own playlists.

## Quick start

```sh
git clone https://github.com/jampick/liner-notes && cd liner-notes
pip install -e '.[media]'
liner-notes add downtempo-drift        # puts the playlist into cliamp
liner-notes media downtempo-drift      # covers and stage pictures into ~/.cache/liner-notes (slow on purpose)
liner-notes build downtempo-drift
liner-notes open downtempo-drift       # starts the bridge and opens the page
```

Then play the playlist in cliamp. The page notices and goes to the stage.

On a tablet or phone on the same network, run `liner-notes serve --lan` and scan the QR code it prints. If you use Tailscale, `liner-notes serve --tailscale` serves HTTPS on your machine's ts.net name instead, which phones need to keep the screen awake.

## Three layers

1. **Live, for any song.** What cliamp knows: title, artist, position, lyrics, the visualizer bands, tonight's history. Free and instant, even for songs nobody has written about.
2. **The song bank.** Notes per song in `songs/<artist>--<title>.json`, written once and reused by every journey that has the song. Every fact carries a source.
3. **Journeys.** A playlist with an order through the years, eras with an essay and a photograph, threads between songs, and optionally a panel: five invented judges with a lens each who argued the list into shape. The panel is fictional and the page says so.

## Your own playlists

```sh
liner-notes generate --from "Yacht Rock"                          # a saved cliamp playlist
liner-notes generate --provider navidrome --playlist 42           # any cliamp provider, read-only
liner-notes generate --from "Yacht Rock" --write --budget 5       # also write the songs the bank doesn't have
```

Without `--write` it costs nothing. It matches your tracks against the song bank, looks up release years on MusicBrainz, cuts eras at the widest gaps between years and builds a journey from what's already known. With `--write`, each missing song is one Claude API request with web search, using `liner_notes/prompts/SONG-BRIEF.md`. It must list its sources, and it stops before it can pass your budget. What you generate lives in `~/.local/share/liner-notes`, not in this repo.

## Players

The bridge reads now-playing through an adapter. Each one yields the same shape: title, artist, album, duration, a position and the time it was true, state, and whatever ids the player gives (YouTube id, Spotify id, file path, ISRC, MusicBrainz id).

| Adapter | How | Status |
|---|---|---|
| `cliamp` | V2 IPC on its Unix socket, with events, control, jump, lyrics, history and bands | works |
| `mpris` | D-Bus through `busctl`, any MPRIS player on Linux | works, tested against cliamp's MPRIS |
| `homeassistant` | a `media_player` over Home Assistant's WebSocket API (Sonos, Cast, AirPlay, Plex and more) | parser tested, not yet run against a live HA |
| `macos` | `media-control stream` from [ungive/media-control](https://github.com/ungive/media-control) | parser tested, not yet run on a Mac |
| `smtc` | Windows media controls through a small helper that prints JSON lines | contract and parser only |
| `lastfm` | a Last.fm user's now-playing scrobble, no position | parser tested |

`liner-notes serve` picks the first one it finds. `--player` picks one.

## Contributing a song

The bank grows by pull request. Copy an existing file in `songs/`, keep the shape in `schemas/song.schema.json`, and list a source for every fact. A new song starts with `"review": {"state": "draft"}`. Drafts show as "Draft notes" on the page. A maintainer reads it against its sources before it becomes `reviewed`. `liner-notes check` runs the same checks CI does.

The voice: a friend who has the records and has read the interviews. No em dashes, no "iconic", no "timeless". Specific names, studios, samples, chart positions, years. The full rules are in the song brief.

## What's in the repo and what isn't

Committed, because we wrote or made them: the code, the song notes and journey text, the year culture notes, and the era photographs (AI-generated, labelled as such).

Fetched into your cache, never committed or shipped in a shareable file: album covers (they belong to the labels), Wikipedia pictures (each with its licence recorded; `media --public` drops the ones that aren't freely licensed), lyrics (cliamp fetches them while you listen) and Apple's 30-second previews (streamed from Apple, with Apple's credit beside them).

`liner-notes build --public` makes a page you can send to someone: no covers, no video, the same notes. The muted music video on the stage is a personal-use option for local builds only (`--video`). It only plays when cliamp is playing that exact YouTube upload, because YouTube's developer policies don't allow separating a video from its audio.

Every page says in its credits, its `<head>` and its JSON-LD that an AI model wrote the notes, and how many songs a person has reviewed.

## Privacy

Everything runs on your machine. The bridge listens on 127.0.0.1 only, checks the Host header and refuses requests from other websites, so a page you visit can't read what you play. `--lan` and `--tailscale` are opt-in. They require a pairing token, which changes every time the bridge starts. `--tailscale` gets a Let's Encrypt certificate through Tailscale, which puts your machine's tailnet name in the public certificate logs. Liner Notes sends your music to nobody. MusicBrainz, Wikipedia and Apple see lookups for song titles, with a User-Agent that names this project.

## Licences

Code: MIT (`LICENSE`). Song notes, journey text and culture notes in `songs/`, `journeys/` and `culture/`: CC BY-SA 4.0 (`LICENSE-CONTENT`). The writers worked largely from Wikipedia, which is CC BY-SA, so share-alike is the honest choice.
