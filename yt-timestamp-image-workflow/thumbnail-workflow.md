# Thumbnail Workflow — 4 Candidate Thumbnails

Purpose: when Naufal provides a topic/title for a YouTube video, create **4 thumbnail candidates** that fit the video context and use the Zenn channel thumbnail style as design reference.

Reference channel:
`https://www.youtube.com/@Zenn0009/videos`

Use the reference as visual inspiration, not a pixel-for-pixel copy.

## Inputs

Naufal may provide:
- Video topic
- Video title
- Script
- WAV audio
- Any specific hook/angle

Use the topic/title as the main context for thumbnail accuracy.

## Required Output

For every project, provide **4 thumbnail candidates**.

Each candidate should include:

```markdown
### Thumbnail Candidate 1 — [short concept name]
Title/Text on thumbnail: "..."
Visual concept: ...
Composition: ...
Colors: ...
Why it works: ...
Generation prompt: ...
Filename: thumbnail_01.png
```

If Naufal asks to generate thumbnails directly, generate 4 images and save them locally as:

```text
thumbnail_01.png
thumbnail_02.png
thumbnail_03.png
thumbnail_04.png
```

## Reference Style Summary — Zenn Channel

Observed reusable thumbnail patterns:

- Simple cartoon / doodle illustration style.
- Thick black outlines.
- High contrast and instantly readable.
- Minimal detail.
- Main idea is understandable in under one second.
- One large central subject or icon.
- Often uses humor, curiosity, mystery, or a question.
- Text is short and bold.
- Text often sits at the top.
- Yellow uppercase text with thick black outline is common.
- Some thumbnails use handwritten black/red marker-like text.
- Strong primary colors: red, blue, yellow, black, white.
- Backgrounds are flat, simple, and uncluttered.
- Characters have exaggerated simple expressions: shocked, confused, worried.
- Objects are oversized and iconic.
- Layout often uses:
  - Top headline + central illustration below.
  - One dominant icon on plain background.
  - Two contrasting objects/choices.
  - Big question mark or mystery element.

## Thumbnail Design Rules

Each thumbnail candidate should be:

- Horizontal 16:9.
- YouTube thumbnail format.
- Simple and readable on mobile.
- Based on the topic/title, not random.
- More polished than the timestamp scene images, but still simple cartoon/doodle.
- Bold, high-contrast, and curiosity-driven.
- Not cluttered.
- No long sentences.

## Text Rules

Thumbnail text should be:

- Short.
- Easy to read.
- Ideally 1–4 words.
- Question-style when useful.
- Correctly spelled.
- Big and bold.

Good examples:

```text
WHY LEFT?
2 HOURS?
ROTTEN MEAT?
NO VIEWS?
TOO LATE?
```

Avoid:
- Full video title pasted onto thumbnail.
- Tiny captions.
- Paragraphs.
- Too many labels.

## Candidate Variety

The 4 candidates should not be near duplicates.

Default candidate angles:

1. **Question / Mystery** — makes viewer curious.
2. **Contrast / Choice** — two opposing objects, sides, or outcomes.
3. **Problem / Pain** — shows the central problem visually.
4. **Surprise / Twist** — shows the unexpected answer or weirdest image from the topic.

## Generation Prompt Template

```text
Create a horizontal 16:9 YouTube thumbnail inspired by the Zenn channel thumbnail style.

Video topic/title: {TOPIC_OR_TITLE}
Thumbnail concept: {CONCEPT}
Text on thumbnail: "{SHORT_TEXT}"

Visual style:
Simple bold cartoon/doodle illustration, thick black outlines, high contrast, minimal detail, flat colors, clean uncluttered background, large central subject, readable on mobile, curiosity-driven.

Composition:
{COMPOSITION}

Color direction:
{COLORS}

Text treatment:
Large bold uppercase thumbnail text, preferably yellow with thick black outline unless another color fits better. Keep text short and correctly spelled.

Avoid:
Photorealism, 3D, anime, Disney style, glossy corporate vector art, complex detail, clutter, tiny text, long sentences, unreadable words.
```

## Relationship To Timestamp Images

Thumbnail style is related but not identical to timestamp images.

Timestamp images:
- Extremely bad MS Paint stickman drawings.
- Used inside the video.
- Medium quality.

Thumbnails:
- Still simple cartoon/doodle.
- More clickable and composed.
- Bold text and central hook.
- Must follow Zenn-style reference patterns.

## Default Behavior

When Naufal provides a topic/title:

1. Use it to understand the video context.
2. Create 4 thumbnail candidate concepts.
3. If he asks for generation, generate and save 4 thumbnail images.
4. Keep filenames simple: `thumbnail_01.png` through `thumbnail_04.png`.
