+++
id = "front"
version = 1
description = "The journey's front page: title, kicker, lede, the express route, the page's small copy and the threads between songs."
+++
# Liner Notes: front page brief

A Liner Notes journey is a page a listener walks one song at a time in release order. You write what's around the songs: the title, the line under it, the opening paragraph, the short express route for someone with ten minutes, the page's button and message copy, and the threads that tie one song to another. The person who made the journey shares it under their own name, and they'll edit what you write.

The journey's heuristic, its eras, its songs with their notes, and the panel (if it has one) are in the inputs. Use them; don't research new facts here. Anything you say about a song should already be in its notes or the era essays.

## What to write

- `title`, split into `titleA` and `titleB`: the journey's name and the two halves the page sets in two weights ("Sophisti" and "Pop"). Usually the idea's own name.
- `kicker`: one line under the title with the song count, a phrase for the idea, and the first and last records with their years ("42 records in good suits, Avalon 1982 to Driving 1990").
- `description`: one plain sentence for search results and link previews: how many songs, from what to what, and what the page gives you.
- `lede`: one paragraph, 60 to 120 words, that opens the page. Start with a person, a place or a fact from the story, not with the genre's definition. End by saying what the page is: these songs, one at a time, in the order the records came out.
- `express`: four or five songs, one per era where you can, for someone with ten minutes, and a one-line `note` on whose picks they are ("the Purist's one-per-era picks").
- `copy`: the small text on the page. `start` (the play button, two to four words with a little of the journey's flavour), `resume`, `express`, `panelNote` (if there's a panel, say plainly it's invented and the facts under each song are real and sourced), `endKept`, `endEmpty` and `credits` (who picked the songs, where the research came from, where previews and pictures came from).
- `threads`: connections between two songs on the journey: a shared sample, singer, player, producer, studio, label, writer, city, or an influence. Each has `from` and `to` (song ids from the inputs), a `kind` (sample, singer, producer, session, influence, soundtrack, city, label, writer) and a one-sentence `note`. Only threads the notes back up.

## Voice

{style_rules}

## The inputs

{inputs}

## Output

Reply with one JSON object in a ```json fenced block and nothing after it, matching this schema:

{output_schema}
