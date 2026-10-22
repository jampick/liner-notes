+++
id = "dates"
version = 1
description = "Each song's year, where it came from and how sure we are. Never a made-up year."
+++
# Liner Notes: dates brief

A Liner Notes journey plays its songs in the order the records came out, and cuts them into eras by year, so a wrong year puts a song in the wrong place and can move an era's bounds. You check the year of every song on the journey.

The inputs give each song's id, title and artist, and the year it has now with where that came from. Most start from MusicBrainz, which is only a first guess: it takes the first release it can find, and that can be a later compilation, a reissue or an album that shares the song's title. Some years were set by a person. Some songs have no year at all.

## The year

The year is the original release year of this recording: the single or the album, whichever came out first. Not the year it was written, recorded or charted, not a remaster, and not a different recording of the same song. If the version on the journey is a later recording, the year is that recording's.

Check each year against a page you actually read: label and discography pages, chart archives, Discogs, interviews, long-form music journalism. Use Wikipedia to find leads and confirm what matters elsewhere when you can.

## The order of trust

A person beats a writer, and a writer beats MusicBrainz.

- A year with `source` "person" stays as it is, even if you find another. Give the other year and your page in `note` and a person decides.
- A year from "musicbrainz" that you confirm: keep it, keep `source` "musicbrainz", and put the page you read in `url`.
- A year from "musicbrainz" that's wrong, or a missing year you find: set `year` to the right one, `source` to "writer", `url` to the page that shows it, and `corrected_from` to the year it had before (null if it had none). Say in `note`, in one sentence, why the old year was wrong.

## Never invent a year

If you can't find a year you'd stand behind, leave `year` as null (or as it was) and `confidence` "none", and say in `note` what you looked at. A song with no year is left out of the eras until someone finds one. A guess that looks right does more harm, because nobody looks at it again.

## What to write for every song

- `song`: the song's id, exactly as in the inputs.
- `year`: the year, or null.
- `source`: "musicbrainz", "writer", "person", or null when there's no year.
- `confidence`: "high" (two good sources agree, or the label's own page), "medium" (one good source), "low" (one weak source, or sources disagree and you picked one), "none" (no year).
- `url`: the page that settles it, when you read one.
- `note`: a short plain sentence when anything needs explaining: a correction, a disagreement between sources, a person's year you'd question. Leave it out otherwise.
- `corrected_from`: only on a song whose year you changed: the year it had before, or null.

## Voice

{style_rules}

## The inputs

{inputs}

## Output

Reply with one JSON object in a ```json fenced block and nothing after it, matching this schema:

{output_schema}
