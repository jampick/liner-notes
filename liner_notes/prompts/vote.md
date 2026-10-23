+++
id = "vote"
version = 4
description = "The panel: five invented judges vote on every candidate against the heuristic, with a reason each."
+++
# Liner Notes: panel brief

A Liner Notes journey gets its songs from a panel, not from somebody else's list. Five judges, each with a different lens, vote on every candidate against the journey's heuristic. The listener sees who argued for each song and why, so the reasons have to be real arguments about the records.

The judges are invented, and the page says so. Don't name a real critic, writer or broadcaster as a judge.

## The panel

Seat five judges whose lenses fit this idea. A pattern that has worked: a purist who knows the canon, a chart historian (peaks and how long the radio kept playing it), a player or arranger (groove, chords, craft), a crate-digger (roots, samples, the afterlife in later records, and who gets the credit for where the sound came from) and a party host (would a normal room today move or sing along). Swap any lens that doesn't fit the idea for one that does.

Give every judge a short id, a full name and a title (a first and last name, then "the Purist" or whatever the lens is), plus one sentence on their lens. Names should sound like specific real people from the world the music came from, not default-sounding ones, and should be new for this panel. If the inputs already seat a panel, use it as given.

One judge holds the veto, usually the purist. The veto means "this isn't the thing the journey is about": outside the heuristic's era, sound or personnel, or the wrong recording of the song. It isn't for songs the judge doesn't like.

## The rule

{rule} You don't decide that: you vote, and `liner-notes` counts.

## What to write for every candidate

- Each judge's vote, yes or no, and a one-line reason (`why`) in that judge's voice. A judge who vetoes votes no, and the veto goes in the candidate's `veto`, with `by` (the judge's id) and `why` (one line on why the song isn't the thing the journey is about). A reason is specific: a player, a chart run, a chord change, a sample, a year. "A classic" isn't a reason.
- A `quote`: one or two sentences from one named judge. For a song that gets in, the material reason it belongs (a musical or historical fact, not praise). For one that doesn't, the dissenting judge on what sank it.

The era writers check the facts in every quote against what they read, and a wrong one comes back corrected, so only say what you know is true. No lyrics in quotes, not even a line.

A short list that passes the panel beats one padded out to a round number.

The candidates come from the inputs: the heuristic, plus the creator's seed list or playlist when there is one. If there are none, put 30 to 40 on the ballot first, one recording each: the ones a fan of the idea would expect and the ones they'd argue about. A ballot that wide usually leaves about ten close calls, the arguments a listener wants to read. Don't shade a vote to get there.

## Voice

{style_rules}

## The inputs

{inputs}

## Output

Reply with one JSON object in a ```json fenced block and nothing after it, matching this schema:

{output_schema}
