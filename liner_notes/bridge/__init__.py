"""The local bridge between Liner Notes pages and cliamp (or another player).

    liner-notes serve            http://127.0.0.1:47391, this machine only
    liner-notes serve --lan      also on your network, for a tablet or phone, behind a pairing token

GET  /                         the journeys, as a small index page
GET  /now                      {ok, version, source, state, title, artist, album, yt, song, journey, playlist,
                               position, duration, epoch, moving, latency, can}; ok:false with reason
                               "no-player" or "stopped" when there's nothing (see clock.py for the position)
GET  /events                   server-sent events, one per now-playing change (from cliamp's IPC subscribe)
GET  /bands                    cliamp's visualizer bands as server-sent events (one shared `cliamp visstream`)
GET  /lyrics?yt=&song=         {ok, lines: [{t, start, text}], source: retimed | cliamp | lrclib}, only for the
                               song that is playing
GET  /history                  what cliamp played recently, newest first, with song ids
GET  /theme                    the current Omarchy theme's colours, mode and font, when there is one
GET  /song                     the playing song's liner notes, with its journey and era (read-only, for widgets)
GET  /journey/<slug>.json      a journey;  GET /song/<id>.json  one song from the bank
GET  /p/<slug>.html            a built page, with this start's token written in;  /p/<slug>.stage.json  its stage data
GET  /m/<slug>/<dir>/<file>    a stage picture from the cache;  /d/<slug>/<dir>/<file>  its depth map
GET  /v/  /v/<id>.mp4          the YouTube ids with a video on disk; one video, with Range
POST /play {yt, song}          jump cliamp to that song in the loaded playlist      (X-Liner-Token required)
POST /cmd {op}                 toggle, next or prev                                  (X-Liner-Token required)

server.py has the gate (Host, Origin, token, CSP) and the routes; now.py follows the player;
clock.py smooths the position; lyrics.py, bands.py, media.py and theme.py serve one route family
each. Nothing here writes to disk.
"""
from http.server import ThreadingHTTPServer

from .clock import Clock
from .now import Library, Now
from .server import PORT, Server, make_handler, make_server, playing_song, serve, setup_logging

__all__ = ["PORT", "Clock", "Library", "Now", "Server", "ThreadingHTTPServer", "make_handler", "make_server", "playing_song", "serve",
           "setup_logging"]
