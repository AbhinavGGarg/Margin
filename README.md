# Margin

**Tap a line of your handwritten notes. Hear the moment it was explained.**

You study from a video and take notes on paper. A week later a line like
`light rxns in thylakoid membrane` makes no sense, and finding where the video
explained it means scrubbing around and guessing. Margin fixes that: paste the
video link, add a photo of your notes, and every handwritten line becomes a
button that plays the video from the second it was said.

Nothing about how you take notes changes. No special pen, no app running while
you watch.

## Run it

You need Python 3.9 or newer (macOS already has it).

```bash
./start.sh
```

The first run sets everything up, then opens Margin at http://localhost:8000.

Margin needs something to read handwriting. Either works:

- **Gemini** (best on messy handwriting). Get a free key at
  https://aistudio.google.com/apikey, put it in the `.env` file as
  `GEMINI_API_KEY=your-key`, and restart.
- **Apple Vision** (Mac only). Needs no key and works offline. `start.sh`
  installs it automatically on a Mac. It is good with neat handwriting.

To check your setup at any time:

```bash
./start.sh doctor
```

It tests the caption download, the handwriting reader and the matching model,
and tells you how to fix anything that fails.

## Use it

1. Paste a YouTube link. The video needs captions (auto-generated ones are fine).
2. Add a photo of one page of notes, taken from straight above in good light.
3. Press **Link my notes**.

Then:

- **Tap a line** to play the video from that moment.
- **Play the video** and watch your notes light up as each line is explained.
- **Also said at** shows other moments where the same thing came up.
- **Set to current moment** links a line by hand if the match is wrong.
- **The pencil** fixes a line that was misread; everything is re-matched.
- **How it matched** (or press `M`) shows a map of the whole alignment.
- Arrow keys move between lines; space plays and pauses.

If a video has no captions, choose **Paste a transcript instead** and paste the
text from YouTube's "Show transcript" panel (SRT and VTT files work too).

## How it works

```
photo of notes ──> handwriting reader ──> lines of text + where they sit on the page
video link ──────> timestamped captions ─> overlapping ~18-second "moments"
                                   │
                    evidence score for every (line, moment) pair
                                   │
               in-order alignment by dynamic programming
                                   │
              each line ──> one moment, a confidence, alternatives
```

**1. Evidence.** For every line and every moment Margin scores how likely it is
that the line is about that moment, from 0 to 1, using two signals:

- *Keywords:* the share of the line's informative words that are said in that
  moment, with rare words counting more. Note shorthand is expanded first
  (`w/`, `b/c`, `->`, `rxn`, `CO2`...).
- *Meaning:* similarity between sentence embeddings from a small model that
  runs on your laptop, for lines that paraphrase what was said.

The two are combined like independent witnesses: either one can make the case,
and agreement makes it stronger.

**2. Alignment.** Picking each line's best moment on its own goes wrong whenever
a term is said more than once. Notes are written in roughly the order things
were said, so Margin aligns all the lines together and finds the set of links
with the highest total evidence, where going backwards in time costs something
and a line with no clear match (a heading, "TEST FRIDAY!!") is left unlinked.
This is the same family of algorithm used to align DNA sequences. It runs in
time proportional to lines × moments. See `margin/align.py`.

**3. The exact second.** Inside the chosen moment, Margin finds the caption where
the line's words begin and starts one second before it.

## Project layout

```
margin/
  transcript.py   YouTube captions, pasted-transcript parsing, windowing
  scoring.py      keyword and meaning evidence
  align.py        the in-order alignment, alternatives, the match map
  readers/        handwriting readers: Gemini, Apple Vision
  pipeline.py     the three steps: captions, reading, linking (with caching)
  server.py       three stateless endpoints (FastAPI) and the interface
  doctor.py       setup check
  web/            the interface: plain HTML, CSS and JavaScript, no build step
pyproject.toml    tells a serverless host where the app is
tests/            35 tests, including a full lecture aligned end to end
```

The server stores nothing. Each session (the photo, the lines, the links) is
kept in your browser, and **Download** saves one as a file. Cached captions and
handwriting readings live in `data/`; delete that folder to start fresh.

## Hosting it

Margin runs on Vercel as it is (`pyproject.toml` and `vercel.json` point it at the app):

1. Import the repository at vercel.com and keep the default settings.
2. Add an environment variable `GEMINI_API_KEY`. Apple Vision only exists on a
   Mac, so the hosted copy needs Gemini to read handwriting.
3. Deploy.

Three things differ from running it on your own machine:

- Matching uses keywords only. The meaning model is too large for a serverless
  function.
- YouTube often refuses caption requests that come from cloud servers. When it
  does, Margin asks you to paste the transcript.
- Uploads are limited to about 4 MB, so photos are shrunk in the browser first.

To give visitors something to try without their own notes, make a session
locally, press **Download**, save the file as `margin/web/sample.json` and
commit it. A **Try a sample** button then appears on the home page.

## Tests

```bash
.venv/bin/python -m pip install pytest httpx
.venv/bin/python -m pytest
```

The tests never touch the network. They include a brute-force check that the
alignment really does find the best-scoring set of links.

## Limits

- One page per session.
- Sessions are saved in the browser you made them in. Use Download to move one.
- English works best; the keyword shorthand list is English.
- Handwriting that a person can't read, Margin can't either. Fix a misread line
  with the pencil.
- Diagrams and equations with no words in common with the speech usually stay
  unlinked. Link them by hand with **Set to current moment**.
- YouTube sometimes refuses caption requests. Pasting the transcript always works.
