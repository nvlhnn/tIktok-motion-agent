# TikTok Motion Agent

When Naufal says **go**, run the automation with minimal chat.

## Flow

1. Prepare:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py prepare
```

2. Parse only:
- `job_id`
- `product_title`
- `master_path`
- `product_image_path`

3. Generate an AI caption from `product_title`, then save it:
- Write a fresh Indonesian TikTok UGC-style caption from the product title.
- Use simple Indo Gen Z language, casual and not formal.
- Make it look like a human seller caption, not AI/copywriter text.
- Use lowercase, casual abbreviations when natural (`bgt`, `sih`, `gini`, `nya`).
- Do not use the word `ini`.
- Keep the caption phrase max 5 words before hashtags.
- Use max 5 hashtags total.
- Do not use fixed/repeated templates; make captions visibly different per video.
- No emoji.
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py set-caption <job_id> '<AI_GENERATED_CAPTION>'
```

4. Generate image with OpenClaw `image_generate` using quota-friendly settings:
- refs: `master_path`, `product_image_path`
- model: `openai/gpt-image-2`
- size: `2160x3840`
- quality: `high`
- outputFormat: `jpeg`
- openai.outputCompression: `92`
- set `aspectRatio: 9:16` too as an intent hint, but do not rely on it for OpenAI/gpt-image-2. Current runtime reports `aspectRatio=9:16` as ignored for OpenAI, so the actual control is `size: 2160x3840`.
- prompt: `Preserve master face/identity, lighting, wooden-door background, camera distance, and mid-thigh-up framing. Apply outfit style from product image only. Restyle top, bottom, hijab, and accessories to match; use product bottom if visible, otherwise modest matching bottom. Do not keep original jeans, cream hijab, or bag by default. No UI/text/watermark/product model/background. Realistic fit, true TikTok vertical 9:16 composition. Keep the subject close to camera like the master reference; do not zoom out, do not generate head-to-toe/full-body framing, do not show shoes or extra floor space.`

5. Validate the generated input image before complete:
- First run the local hard gate:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py validate-reference <generated_reference_path>
```
- It must be readable, non-empty, non-blank, exact 9:16, and at least 1080x1920. Preferred output remains `2160x3840` high-quality JPEG.
- If it comes back `1024x1536` / 2:3, too small, corrupt, blank, or wrong format, regenerate before continuing.
- Also run a semantic image review before video creation using the generated reference image plus the product reference image. Do not rely on dimensions only.
- The generated image must pass ALL of these before `complete`:
  - Product match: same garment category, color/tone, silhouette/cut, pattern/print, fabric/texture, buttons/count/placement, collar/neckline, sleeves, hem, pockets/seams/trims, ties/ribbons/lace/pleats, and set composition when applicable. Similar vibe is not enough.
  - Muslim modesty: hijab/head covering intact; no visible neck, collarbone, upper chest, cleavage, bare shoulders, upper arms, waist/back, thighs, bare legs, transparent/sheer revealing areas, or tight body-revealing fit. If the product is revealing, modest layering must cover skin while keeping the product design recognizable.
  - Identity/background/framing: master face/identity preserved, wooden-door background and close mid-thigh-up framing preserved, no head-to-toe zoom-out, no shoes/floor emphasis.
  - Visual quality: no UI/text/watermark, no deformed hands/body/face, no warped garment, no duplicate limbs, no obvious AI artifacts, sharp enough for TikTok.
- If product match is weak, modesty fails, identity/background/framing is wrong, or artifacts are obvious, regenerate the input image before continuing.
- Before calling `complete`, write a short pass/fail note in your own working notes/message: `REFERENCE_REVIEW: PASS` with product_match/modesty/quality reasons, or regenerate if not pass.
- `complete` also runs the local validation automatically and will refuse to submit a video if the generated reference fails objective checks.

6. Complete:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py complete <job_id> <generated_reference_path>
```

7. Review the generated video for TikTok readiness before upload status:
- Download/read the `result_link` locally and inspect metadata: resolution, ratio, fps, duration.
- Sample a few frames/contact sheet and review: framing/crop, sharpness, face/body consistency, hand/artifact issues, outfit/product visibility, modesty, and natural UGC feel.
- If good/uploadable, set status:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py set-status <job_id> READY_TO_UPLOAD --note "review passed"
```
- If not good, set status:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py set-status <job_id> REJECTED --note "short reason"
```

## Rules

- Product video and motion video must be different.
- Use product URL's first PDP image as outfit reference.
- Store links/status in Sheet.
- Generate the caption with AI reasoning from `product_title`; the pipeline's built-in caption is only a fallback, not the final style.
- Captions must not include emoji, including star/sparkle emoji.
- On successful completion and review, reply with `done` plus final status, `result_link`, caption, and a short review verdict. Do not reply with only `done`.
- Keep replies short: start, `done <result_link>`, or fail only.
- Never expose secrets.
