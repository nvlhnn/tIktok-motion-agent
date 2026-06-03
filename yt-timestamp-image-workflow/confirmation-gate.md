# Confirmation Gate

Before executing the main expensive/irreversible parts of this workflow, confirm with Naufal or ask concise questions if needed.

## What Counts As Main Execution

Main execution includes:
- Generating all timestamp images.
- Generating 4 thumbnail images.
- Rendering/exporting the final MP4 video.

Planning, parsing, prompt drafting, file organization, and checking inputs do not require confirmation unless unclear.

## Default Confirmation Flow

After receiving the topic/title, timestamped script, and WAV file, first prepare a short execution plan:

```markdown
I’m ready to run the workflow.

I found:
- Topic/title: ...
- Timestamps: X images
- Audio: ...
- Output: timestamp images + MP4 video + 4 thumbnail candidates

Before I generate/render, confirm:
1. Generate the timestamp images now?
2. Generate thumbnail images too, or only thumbnail concepts/prompts?
3. Render the final MP4 after images are ready?
```

Even if the user says “generate everything”, “execute now”, “make the video”, or similar, give a brief confirmation summary before starting the expensive generation/render step. This workflow should avoid accidental image-generation spend or long renders.

Default rule: **confirm before generating or rendering**.

## Ask Questions When Necessary

Ask if any of these are missing or unclear:

- No timestamped script was provided.
- No WAV file was provided but video editing is requested.
- Topic/title is missing and thumbnails are requested.
- The user does not clarify whether they want thumbnail concepts only or generated thumbnail images.
- Timestamp format is ambiguous or duplicated in a confusing way.
- Audio duration and timestamps obviously conflict.
- The user gives multiple versions of the script/audio and it is unclear which to use.

## Keep Questions Minimal

Ask only the blocking questions. Do not ask unnecessary questions.

Good:
> I can do it. Quick confirm: generate thumbnail images too, or only 4 thumbnail concepts?

Bad:
> What color palette, font, style, mood, export bitrate, and exact output folder do you want?

## Confirmation Before Costly Generation

If generation will create many images, summarize the count first:

```text
I counted 37 timestamps, so this will generate 37 scene images plus 4 thumbnails. Confirm and I’ll run it.
```

## Confirmation Before Final Render

Before rendering the final MP4, if images were newly generated and the user has not approved them, offer a quick checkpoint:

```text
Images are ready. Want me to render the MP4 now with these, or review/adjust first?
```

If Naufal explicitly requested end-to-end execution, still provide the initial count/summary confirmation before starting. After he confirms, continue end-to-end unless an issue is found.
