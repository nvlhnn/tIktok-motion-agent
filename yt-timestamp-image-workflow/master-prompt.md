# Master Image Prompt Template

Use this template for each timestamp scene.

```text
Create one horizontal 16:9 YouTube frame using ChatGPT Image 2.

Timestamp metadata, not visual text: {TIMESTAMP}
Script line / scene idea: {SCENE_IDEA}

Illustrate exactly what the narrator is saying at this timestamp. The image must match the story, emotion, and idea of this exact moment. Do not create a random or generic image. IMPORTANT: the timestamp is only metadata for timing; never draw, write, show, display, label, overlay, or watermark the timestamp/timecode anywhere in the image.

Style:
Extremely simple beginner Microsoft Paint drawing. It should look like someone who is not good at drawing quickly drew it by hand.

Visual requirements:
- White background.
- Thick, uneven black outlines.
- Wobbly hand-drawn lines.
- Stick figure humans with round heads and line bodies.
- Simple dot eyes or circle eyes.
- Very basic facial expressions.
- Flat colors only.
- Mostly empty white space.
- Occasional flat colors like green, brown, gray, red, yellow, orange, and blue.
- Use red arrows or red question marks if helpful.
- Objects made from basic shapes: squares, circles, rectangles, arrows, tables, boxes, trees, rooms, signs, screens, stickmen, question marks, simple symbols.

Main visual:
{MAIN_VISUAL}

Composition:
- Clean, readable, and centered.
- Keep the main subject large enough for a YouTube video.
- Do not crop important objects.
- Leave space around characters and objects.
- Use short readable handwritten text only if it helps explain the idea.
- If text appears, spell it correctly.
- Never include timestamps, timecodes, frame numbers, subtitles, captions, UI overlays, watermarks, or corner labels in the image.

Avoid:
- Glitches.
- Broken anatomy.
- Unreadable text.
- Any visible timestamp/timecode such as “0:41”, “00:41”, “1:05”, or clock-style numbers in the corners.
- Subtitles, captions, UI overlays, frame counters, watermarks, or metadata labels.
- Messy overlapping objects.
- Weird extra details.
- Realistic humans.
- Realistic shading.
- 3D.
- Cinematic lighting.
- Realistic cartoon style.
- Disney style.
- Anime style.
- Polished illustration style.
- Professional vector art.
- Highly detailed backgrounds.
- Complex textures.
- Glossy or modern design.

Do not make the drawing look too good. It should be amateur, funny, simple, and intentionally bad, like a beginner MS Paint drawing.
```

## Quick Prompt Variant

```text
Create one horizontal 16:9 YouTube frame using ChatGPT Image 2. Timing metadata only: {TIMESTAMP}. Do NOT draw or display the timestamp/timecode anywhere in the image. {SCENE_IDEA}. Show exactly this moment from the script: {MAIN_VISUAL}. Extremely simple beginner MS Paint stickman drawing, white background, thick uneven black outlines, wobbly hand-drawn lines, flat colors only, basic shapes, simple readable composition, intentionally bad and funny, not realistic, not 3D, not cinematic, not anime, not Disney, not polished, not professional vector art. Use short correctly spelled text only if helpful, but never include timestamps, subtitles, captions, watermarks, or UI overlays.
```

## Strong Anti-Polish Add-on

Append this if the image model keeps making things too nice:

```text
Make it deliberately amateur and badly drawn. Uneven mouse-drawn lines, clumsy shapes, awkward spacing, simple bucket-fill colors, mostly white empty background, like a rushed meme drawing made in old Microsoft Paint. Do not make it beautiful.
```
