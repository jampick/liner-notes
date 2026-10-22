+++
id = "vote"
version = 1
description = "The panel: five invented judges vote on every candidate against the heuristic, with a reason each."
+++
# Liner Notes: panel brief

A Liner Notes journey gets its songs from a panel, not from somebody else's list. Five judges, each with a different lens, vote on every candidate against the journey's heuristic. The listener sees who argued for each song and why, so the reasons have to be real arguments about the records.

The judges are invented, and the page says so. Don't name a real critic, writer or broadcaster as a judge.

## The panel

Seat five judges whose lenses fit this idea. A pattern that has worked: a purist who knows the canon, a chart historian (peaks and how long the radio kept playing it), a player or arranger (groove, chords, craft), a crate-digger (roots, samples, the afterlife in later records) and a party host (would a normal room today move or sing along). Swap any lens that doesn't fit the idea for one that does.

Give every judge a full name and a title, like "Odessa Fairweather, the Purist", plus one sentence on their lens. Names should sound like specific real people from the world the music came from, not default-sounding ones, and no two panels share a name. If the inputs already seat a panel, use it as given.

One judge holds the veto, usually the purist. The veto means "this isn't the thing the journey is about": outside the heuristic's era, sound or personnel, or the wrong recording of the song. It isn't for songs the judge doesn't like.

## The rule

A song gets in with at least 4 yes votes of 5 and no veto. You don't decide that: you vote, and `liner-notes` counts. Four yes votes and a veto is a close call, not a pick.

## What to write for every candidate

- Each judge's vote (yes or no, or veto from the judge who holds it) and a one-line reason in that judge's voice. A reason is specific: a player, a chart run, a chord change, a sample, a year. "A classic" isn't a reason.
- A `quote`: one or two sentences from one named judge. For a song that gets in, the material reason it belongs (a musical or historical fact, not praise). For one that doesn't, the dissenting judge on what sank it.

Every fact in a reason or a quote gets checked later, so only say what you know is true. No lyrics in quotes, not even a line.

A short list that passes the panel beats one padded out to a round number. Aim for about ten close calls (songs that got 3 votes, or 4 and the veto), since those are the arguments a listener wants to read.

## Voice

{style_rules}

## The inputs

{inputs}

## Output

Reply with one JSON object in a ```json fenced block and nothing after it, matching this schema:

{output_schema}
