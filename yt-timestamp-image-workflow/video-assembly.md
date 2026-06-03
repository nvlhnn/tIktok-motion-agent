# Video Assembly Workflow — Timestamp Images + WAV Audio

Purpose: after generating timestamp images, combine them with Naufal's WAV narration/audio into a finished YouTube video.

## Inputs

Naufal provides:
1. A timestamped YouTube script.
2. A `.wav` audio file.

The workflow produces:
1. One image per timestamp.
2. A final video where each image appears at the correct timestamp.
3. The WAV audio merged as the video's audio track.

## Core Rule

Each image starts at the narrator/scene start timestamp and stays on screen until the next narrator/scene start timestamp. The visual should change when the narrator begins the new subject, not when that subject has already ended.

Example:

```text
00:00:00.000 -> show 001__00-00-00-000.png
00:00:04.100 -> switch to 002__00-00-04-100.png
00:00:08.060 -> switch to 003__00-00-08-060.png
00:00:41.860 -> switch to 014__00-00-41-860.png
```

Durations:

```text
001__00-00-00-000.png duration = 00:00:04.100 - 00:00:00.000 = 4.100 seconds
002__00-00-04-100.png duration = 00:00:08.060 - 00:00:04.100 = 3.960 seconds
003__00-00-08-060.png duration = next_start - 00:00:08.060
last image duration = audio_end - last_start
```

The last image remains until the WAV audio ends.

## File Naming

Generated images use index + exact timestamp filenames:

```text
00:00:00.000 -> 001__00-00-00-000.png
00:00:04.100 -> 002__00-00-04-100.png
00:00:41.860 -> 014__00-00-41-860.png
```

Legacy filenames like `0-41.png` are accepted by `assemble_video.py` for older projects, but new projects should use the exact filename format.

Final video default name:

```text
yt_timestamp_video.mp4
```

If there are multiple projects, create a project subfolder:

```text
yt-timestamp-image-workflow/projects/YYYYMMDD-HHMMSS/
```

Recommended project structure:

```text
project-folder/
  audio.wav
  script.txt
  transcript.srt
  manifest.json
  images/
    001__00-00-00-000.png
    002__00-00-04-100.png
    003__00-00-08-060.png
  prompts.md
  ffmpeg_concat.txt
  output.mp4
```

## Video Format

Default output:
- MP4
- 16:9 horizontal
- 1920x1080 if possible
- H.264 video
- AAC audio
- Pixel format: yuv420p
- Still images should fill the frame without cropping important content.

## Assembly Method

Use `ffmpeg`.

Preferred timing source:
1. If an `.srt` or `.vtt` transcript exists, use the exact segment **start** timestamps, including milliseconds.
2. Use those start timestamps as the image cut points.
3. Ignore transcript end timestamps for cut placement; they only describe when narration text ends.
4. New image filenames should preserve exact timing using `{INDEX}__{HH}-{MM}-{SS}-{MS}.png`.
5. Legacy rounded/floored filenames are only for older projects; the concat durations should always use the exact transcript start times when available.

Efficient default: use the included helper script when possible:

```bash
python3 assemble_video.py --audio project/audio.wav --images project/images --script project/script.txt --output project/output.mp4
```

The helper script:
- Reads exact start timestamps from the script/SRT/VTT when `--script` is provided.
- Supports milliseconds in transcript timestamps like `00:00:41,860`.
- Calculates image durations from consecutive narrator/scene start timestamps.
- Keeps the final image on screen until the WAV ends.
- Writes the ffmpeg concat file.
- Renders a 1920x1080 H.264/AAC MP4.

Preferred method:
1. Calculate duration for each image from timestamp differences.
2. Create an ffmpeg concat demuxer file.
3. Encode still-image slideshow with the WAV audio.
4. Stop video when audio ends.

Example concat file:

```text
file 'images/001__00-00-00-000.png'
duration 4.100
file 'images/002__00-00-04-100.png'
duration 3.960
file 'images/003__00-00-08-060.png'
duration 33.800
file 'images/014__00-00-41-860.png'
duration 10.000
file 'images/014__00-00-41-860.png'
```

Note: concat demuxer often needs the final file repeated once.

Example ffmpeg command:

```bash
ffmpeg -y \
  -f concat -safe 0 -i ffmpeg_concat.txt \
  -i audio.wav \
  -map 0:v:0 -map 1:a:0 \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1" \
  -c:v libx264 -pix_fmt yuv420p -r 30 \
  -c:a aac -b:a 192k \
  -shortest output.mp4
```

## Verification

After rendering, verify:

1. Output MP4 exists.
2. Duration is close to the WAV duration.
3. Resolution is 16:9.
4. Audio is present.
5. Image switches match timestamps.

Use:

```bash
ffprobe -hide_banner output.mp4
```

## If Timing Looks Wrong

Check:
- Timestamp parsing.
- Whether the script/SRT/VTT start timestamps, not end timestamps, are being used.
- Duration math.
- Whether the last image duration reaches audio end.
- Whether ffmpeg concat file repeated the final image.

## If WAV Has Silence

Do not trim silence unless Naufal asks. Use the audio exactly as provided.

## Default Behavior

When Naufal gives a WAV file and timestamped script, do the full pipeline:

1. Generate timestamp images.
2. Save images locally.
3. Assemble video with WAV audio.
4. Return final MP4 path and optionally attach it.
