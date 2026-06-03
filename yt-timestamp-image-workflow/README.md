# YouTube Timestamp Image Workflow

Purpose: reusable prompt pack for generating one image per timestamp/scene from a YouTube-style script.

When Naufal says something like “generate images from the timestamp workflow”, “check the yt timestamp image folder”, or “use this workflow for the script”, use this folder as the source of truth.

## Core Workflow

1. Naufal provides a YouTube script with timestamps.
2. Read the script carefully.
3. Create exactly **one separate image for every narrator/scene start timestamp** in the script or transcript.
4. If the script has timestamps like `0:00`, `0:03`, `0:07`, `0:10`, `0:12`, and `0:20`, generate one image for each of those start timestamps.
5. Each image must visually illustrate what the narrator starts saying at that exact moment.
6. Do not create random images. Every image should match the story, emotion, and idea being explained at that timestamp.
7. All images must feel like they belong in the same video and same drawing style.

## Generation Model / Quality

- Use **ChatGPT Image 2** / the configured OpenAI image generation path when available.
- Use **medium quality**, not high quality.
- Do not over-render or make the image look premium.

## Format Requirements

- Every image must be **horizontal 16:9** for YouTube video format.
- Generate each image as a wide YouTube frame.
- Not vertical.
- Not square.
- Clean, readable, centered composition.
- Do not crop important objects.
- Leave enough space around characters and objects.

## Default Visual Style

Extremely simple beginner MS Paint drawing style:

- White background.
- Thick, uneven black outlines.
- Wobbly hand-drawn lines.
- Stick figure humans with round heads and line bodies.
- Simple dot eyes or circle eyes.
- Very basic facial expressions.
- Flat colors only.
- Mostly white empty space.
- Occasional flat colors like green, brown, gray, red, yellow, orange, and blue.
- Red arrows or red question marks when useful.
- Handwritten text only when it helps explain the idea.
- If text appears, it must be spelled correctly, short, and easy to read.
- Never show the timestamp/timecode inside the image. Timestamps are metadata for filenames/timing only.

The drawings should feel amateur, funny, simple, and intentionally bad — like a beginner quickly drew them in Paint.

## Object Style

Objects should be drawn with basic shapes:

- Squares
- Circles
- Rectangles
- Arrows
- Simple tables
- Boxes
- Trees
- Rooms
- Signs
- Screens
- Stickmen
- Question marks
- Very simple symbols

## Avoid

- Glitches
- Broken anatomy
- Unreadable text
- Visible timestamps/timecodes like “0:41”, “00:41”, or “1:05”
- Subtitles, captions, corner labels, frame counters, UI overlays, watermarks, or metadata text
- Messy overlapping objects
- Weird extra details
- Realistic humans
- Realistic shading
- 3D
- Cinematic lighting
- Realistic cartoon style
- Disney style
- Anime style
- Polished illustration style
- Professional vector art
- Highly detailed backgrounds
- Complex textures
- Glossy or modern design




## Confirmation Gate

Before expensive main execution, give a short confirmation or ask needed questions.

Main execution means:
- Generating all timestamp images.
- Generating thumbnail images.
- Rendering the final MP4.

If inputs are incomplete or ambiguous, ask only the blocking question.

Even if everything is clear and Naufal explicitly says to generate/render, give a brief count/summary confirmation before the expensive step. After confirmation, proceed without extra interruptions unless blocked.

Detailed confirmation rules live in `confirmation-gate.md`.

## Thumbnail Candidate Workflow

If Naufal provides a topic/title, also create **4 thumbnail candidates** for the video.

Thumbnail candidates should use this reference channel for design direction:
`https://www.youtube.com/@Zenn0009/videos`

Default thumbnail behavior:

1. Use the topic/title for context.
2. Create 4 different thumbnail concepts.
3. Follow Zenn-style patterns: simple bold cartoon/doodle, thick black outlines, large central subject, short bold text, high contrast, curiosity/mystery angle.
4. If Naufal asks to generate thumbnails, save locally as:
   - `thumbnail_01.png`
   - `thumbnail_02.png`
   - `thumbnail_03.png`
   - `thumbnail_04.png`

Detailed thumbnail rules live in `thumbnail-workflow.md`.

## Full Video Editing Workflow

If Naufal also provides a `.wav` file, extend the workflow into video editing:

1. Generate one image for every timestamp.
2. Save images locally using timestamp filenames.
3. Use the WAV file as the audio track.
4. Place each image at its matching narrator/scene start timestamp.
5. If SRT/VTT is available, use exact start timestamps with milliseconds for cuts; do not rely only on rounded filenames.
6. Keep each image on screen until the next narrator/scene start timestamp.
7. Keep the final image on screen until the audio ends.
8. Export a horizontal 16:9 MP4 video.

Detailed assembly rules live in `video-assembly.md`.

## Local Saving / File Naming

Save images locally.

Preferred filename format is:

```text
{INDEX}__{HH}-{MM}-{SS}-{MS}.png
```

Examples:

```text
001__00-00-00-000.png
002__00-00-04-100.png
003__00-00-08-060.png
014__00-00-41-860.png
```

Why:
- `INDEX` keeps generation/video order obvious.
- `HH-MM-SS-MS` preserves exact SRT/VTT timing.
- The filename still remains filesystem-safe.
- It avoids ambiguity from rounded names like `0-41.png`.

Also create or update a `manifest.json` when possible:

```json
{
  "index": 14,
  "start": "00:00:41.860",
  "image": "images/014__00-00-41-860.png",
  "text": "When it became independent in 1965,"
}
```

Legacy filename format is still accepted by the assembler for older projects:


```text
0-00.png
0-03.png
0-07.png
0-10.png
0-12.png
0-20.png
```

Rules:
- Prefer exact millisecond filenames for new projects.
- Convert `:` and `.` to `-` for filesystem safety.
- Preserve the exact timestamp meaning.
- Do not use long descriptive titles by default.
- If duplicate timestamps exist, append a small suffix after the timestamp: `014__00-00-41-860_b.png`.

## Folder Contents

- `style-guide.md` — exact visual style rules.
- `scene-parser.md` — how to convert timestamped scripts into image prompts.
- `master-prompt.md` — reusable generation prompt template.
- `negative-prompt.md` — things to avoid.
- `example.md` — example input and output prompt format.
- `video-assembly.md` — how to merge generated images with WAV audio into MP4 video.
- `assemble_video.py` — helper script to assemble timestamp-named images and WAV audio into MP4.
- `thumbnail-workflow.md` — how to create 4 thumbnail candidates from topic/title.
- `confirmation-gate.md` — when to confirm or ask questions before generation/rendering.
- `WORKFLOW_INSTRUCTION.md` — operational instruction for hana.
