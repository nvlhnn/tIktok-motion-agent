# Scene Parser Rules

Use these rules when converting a timestamped YouTube script into image prompts.

## Input Format Expected

Naufal may send timestamps like:

```text
0:00 Intro about why people fail on YouTube
0:03 The creator uploads but gets no views
0:07 The algorithm ignores him
0:20 He realizes the intro is boring
```

or:

```text
[0:00] Hook: Everyone thinks this is easy.
[0:10] Reality: You upload and nobody watches.
```

Accept loose formatting. Do not be picky.

## Timestamp Rule

For every narrator/scene start timestamp in the script or transcript, create exactly one image. The image represents the subject that begins at that timestamp.

If the script has:

```text
0:00
0:03
0:07
0:10
0:12
0:20
```

then generate exactly six images, one for each timestamp.

## Parsing Rules

For each timestamp:
1. Extract the narrator/scene start timestamp. If the source is SRT/VTT, use the segment start time, including milliseconds, as the true timing point.
2. Treat the timestamp as timing metadata only, not visual content.
3. Extract what the narrator starts saying at that exact moment.
4. Identify the story idea.
5. Identify the emotion.
6. Identify the visual subject.
7. Identify the action or situation.
8. Identify any useful simple symbol/object.
9. Create one image prompt that visually explains that timestamp and explicitly says not to display the timestamp/timecode.

## Scene Accuracy

Each image must match the current timestamp only.

Do not:
- Create random images.
- Skip timestamps.
- Merge multiple timestamps into one image unless Naufal asks.
- Generate a generic image that does not match the narration.
- Put the timestamp/timecode into the image itself.
- Add subtitles, captions, corner labels, frame counters, watermarks, or UI overlays unless Naufal explicitly asks.

## Scene Compression

If a timestamp has a long paragraph, compress it into one clear visual idea.

Example:

Script:
> 0:34 Most beginners think the algorithm hates them, but actually their intro is boring.

Visual idea:
> A sad stick figure points at a giant monster labeled “ALGORITHM”, while behind him his boring intro is putting viewers to sleep.

## Avoid Literal Walls of Text

Do not turn the full narration into text inside the image. Illustrate the idea visually.

## No Visible Timestamp Rule

The timestamp is for filename and video timing only. It must never appear as visible text in the generated image. Every generated image prompt should include a negative instruction like: “Do not draw, write, display, overlay, or watermark the timestamp/timecode anywhere in the frame.”

## Continuity

All images should feel like they belong in the same video and same drawing style.

If multiple timestamps follow the same character, keep a simple recurring main stick figure.

Default recurring character:
- Blue-shirt stickman = creator/narrator/main person.

## Local Filename Rule

Save images locally using index + exact timestamp names.

Examples:

```text
00:00:00.000 -> 001__00-00-00-000.png
00:00:04.100 -> 002__00-00-04-100.png
00:00:08.060 -> 003__00-00-08-060.png
00:00:41.860 -> 014__00-00-41-860.png
```

Rules:
- Preferred format: `{INDEX}__{HH}-{MM}-{SS}-{MS}.png`.
- Use the exact narrator/scene start time. If SRT/VTT has milliseconds, keep them.
- Replace `:`, `.`, and `,` with `-`.
- Do not add long titles by default.
- If duplicate timestamps exist, append suffix after the exact timestamp: `014__00-00-41-860_b.png`.
- Legacy filenames like `0-41.png` are accepted only for old projects; do not use them for new generation.

## Output Format Before Generation

When preparing prompts, use this format:

```markdown
### {TIMESTAMP}
Filename: {INDEX}__{HH}-{MM}-{SS}-{MS}.png
Prompt:
[full image prompt]
```

## If Script Has No Timestamps

Ask Naufal whether to:
1. Generate images by paragraph/beat, or
2. First create timestamps manually.

Recommendation: use paragraph/beat if he wants speed.
