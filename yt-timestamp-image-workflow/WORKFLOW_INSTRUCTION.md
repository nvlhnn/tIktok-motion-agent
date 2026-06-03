# Instruction For Hana

When Naufal asks to use the YouTube timestamp image workflow:

1. Read this folder first:
   `/root/.openclaw/workspace/yt-timestamp-image-workflow/`
2. Use these files as the workflow rules:
   - `README.md`
   - `style-guide.md`
   - `scene-parser.md`
   - `master-prompt.md`
   - `negative-prompt.md`
   - `video-assembly.md`
   - `thumbnail-workflow.md`
   - `confirmation-gate.md`
3. Before main execution, follow `confirmation-gate.md`: confirm before generating/rendering, or ask concise blocking questions when necessary.
4. If Naufal sends a timestamped script, parse every timestamp.
5. Generate exactly one image per timestamp.
6. Use ChatGPT Image 2 / OpenAI image generation when available.
7. Use **medium quality** by default.
8. Use **horizontal 16:9** image format.
9. Use the beginner MS Paint stickman style.
10. Save images locally.
11. Name each image using index + exact narrator/scene start timestamp:
    - `00:00:00.000` -> `001__00-00-00-000.png`
    - `00:00:04.100` -> `002__00-00-04-100.png`
    - `00:00:41.860` -> `014__00-00-41-860.png`
    - Preferred format: `{INDEX}__{HH}-{MM}-{SS}-{MS}.png`
    - Also maintain `manifest.json` when possible, mapping index/start/image/text/prompt.
12. If Naufal also provides a WAV file, assemble the generated timestamp images into a video using `video-assembly.md`.
13. In the final video, each image starts exactly when the narrator starts that timestamp/subject and stays until the next narrator/subject start. If an SRT/VTT transcript exists, use exact segment start times with milliseconds; do not time images from segment end times or rounded filename timestamps. The final image stays until the WAV ends.
14. Export a 16:9 MP4 with the WAV as audio.
15. If Naufal provides a topic/title, create 4 thumbnail candidates using `thumbnail-workflow.md` and the Zenn channel design reference.
16. If thumbnail generation is requested, save them as `thumbnail_01.png`, `thumbnail_02.png`, `thumbnail_03.png`, and `thumbnail_04.png`.
17. Return the local file paths or attach the generated images/video/thumbnails when done.

Default assumption:
- “this workflow” = YouTube timestamp script → one local 16:9 medium-quality beginner MS Paint image per timestamp.
- If a WAV file is included, “this workflow” also includes editing those images into a finished MP4 video timed to the WAV.
- If a topic/title is included, also create 4 thumbnail candidates with Zenn-style design reference.

Ask only if missing:
- The script/timestamps.
- Whether he wants prompts only, image generation, full video assembly, or thumbnail generation, if genuinely unclear.

Always give a short count/summary confirmation before costly generation or final rendering, even if Naufal says to generate. After confirmation, proceed without extra interruptions unless blocked.
