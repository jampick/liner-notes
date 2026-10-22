+++
id = "picture"
version = 2
description = "One image prompt per era, in the house photographic style."
+++
# Liner Notes: era picture brief

Each era on a Liner Notes journey can open with a still of a room, a street or an object from the era, the kind of place the records were made or played, so the listener gets the feel before the first song. Nothing and nobody in it is real. You write the prompt an image model gets for each era. Nobody has to generate a picture; an era with no picture gets a type-led card instead.

## The style

Every prompt starts with this sentence, word for word, and the scene follows it:

"Shot on 35mm film, a candid lived-in film still, natural grain, soft halation around light sources, slightly faded colour, practical light only, nobody in frame, no faces, no text, no signage, no logos, no album covers."

## The scene

Two to four sentences, built from the era's essay and scene notes in the inputs:

- A place and a time: the city or region, the year or so, the season and the time of day ("a basement rehearsal room in Manchester, 1983, a wet November evening").
- Five to eight period objects that belong to this era's records: the instruments and gear the scene notes name, furniture, a drink, a tape format, the weather on the window. Name the instrument the way a player would (a Wurlitzer electric piano, a Minimoog), never its badge or lettering.
- Where the light comes from: lamps, a fire, a VU meter, a neon sign out of focus, low sun through blinds. Practical light only.
- No people, no faces, no hands, no text, no legible signs, no record sleeves. Nothing that would be a picture of a real person, a real logo or a real cover.

The scene should be specific enough that two eras of the same journey can't be swapped. Keep it wide, in a 3:2 frame.

## Voice

The style rules below apply to the scene text, since it's kept with the journey.

{style_rules}

## The inputs

{inputs}

## Output

Reply with one JSON object in a ```json fenced block and nothing after it, matching this schema:

{output_schema}
