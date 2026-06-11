# TikTok Motion Agent

When Naufal says **go**, run the automation with minimal chat.

## Flow

1. Prepare:

If the cron/request assigns a worker, prefix every `motion_pipeline.py` command with `MOTION_WORKER_ID=<worker> MAX_ACTIVE_GENERATIONS=2`.

```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py prepare
```

2. Parse only:
- `job_id`
- `product_title`
- `master_path`
- `product_image_paths` if present; otherwise fallback to `product_image_path`

3. Generate an AI caption from `product_title`, then save it:
- Write a fresh Indonesian TikTok UGC-style caption from the product title.
- Use simple Indo Gen Z language, casual and not formal.
- Make it look like a human seller caption, not AI/copywriter text.
- Make the phrase specific to the product detail or use-case, not just generic praise. Mention a concrete detail when possible: ruffle, salur, bordir, plisket, rajut, vneck, warna/tone, cut, layering, ngantor/daily/kuliah/hangout.
- Avoid repetitive/template phrases like `cakep bgt`, `manis bgt`, `adem bgt`, `simple cakep`, `kalem cakep`, or `buat daily` on every video.
- Use lowercase, casual abbreviations when natural (`bgt`, `sih`, `gini`, `nya`), but do not overuse `bgt`.
- Do not use the word `ini`.
- Keep the caption phrase max 5 words before hashtags.
- Always use exactly these fixed hashtags: `#fyp #muslimah #outfitideas #ootdhijab #outfittiktok`.
- Do not add product-specific or extra hashtags.
- Do not use fixed/repeated phrase templates; make caption phrases visibly different per video.
- No emoji.
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py set-caption <job_id> '<AI_GENERATED_CAPTION>'
```

4. Generate image with OpenClaw `image_generate` using quota-friendly settings:
- refs MUST be built from the prepared job data in this exact order: `master_path`, then every path in `product_image_paths` up to 2 product refs.
- If `product_image_paths` has two entries, the `image_generate.images` array MUST contain 3 images total: `[master_path, product_image_paths[0], product_image_paths[1]]`.
- If `product_image_paths` has one entry, use `[master_path, product_image_paths[0]]`.
- Only if `product_image_paths` is absent/empty, fall back to `[master_path, product_image_path]`.
- Do not silently drop `product_reference_2.jpg` when it exists.
- The generated image MUST show exactly one person only. Product reference images are garment-detail references only; ignore their human models as subjects. Never generate two people, side-by-side duplicates, before/after comparison, collage, mirror duplicate, ghost duplicate, extra model, extra face/body, mannequin, or poster person.
- model: `openai/gpt-image-2`
- size: `2160x3840`
- quality: `high`
- outputFormat: `jpeg`
- openai.outputCompression: `92`
- set `aspectRatio: 9:16` too as an intent hint, but do not rely on it for OpenAI/gpt-image-2. Current runtime reports `aspectRatio=9:16` as ignored for OpenAI, so the actual control is `size: 2160x3840`.
- prompt: `Preserve master face/identity, lighting, wooden-door background, and the exact body-to-image ratio from the selected master reference. Dynamically match the selected master image (`master.png`, `master2.png`, or any explicitly provided master): same virtual camera distance, same subject scale in frame, same head-to-frame ratio, same torso/body width relative to canvas, and same crop position. Do not hardcode a fixed crop style; if the master is wider/looser, keep it wider/looser, and if the master is closer/tighter, keep it closer/tighter. Do not zoom in or zoom out compared with the selected master. Do not add extra safety margin by making the subject smaller. Never switch to full-body, near full-body, head-to-toe, or floor-visible framing unless the selected master itself is full-body. Keep the master reference's exact relative positions for head, shoulders, torso, hands, hips/lower crop, top margin, side margins, and background amount; preserving subject scale is more important than adding extra empty space or showing the full garment. Product visibility must adapt to the master crop, not the other way around: if the product is a dress, skirt, set, long tunic, pants, or has a lower hem, it is acceptable for lower hem/ankles/feet to be outside the frame exactly like the master crop. Maintain the same margin style as the master around elbows, wrists, hands, and garment edges; do not shrink the person just to avoid edge proximity. Use a simple natural standing pose with relaxed arms near the torso; hands should be cleanly formed, anatomically natural, visible when included, with five normal fingers per hand, not fused, duplicated, cropped, twisted, interlaced, or hidden behind fabric. Avoid poses that create motion-control artifacts: no crossed arms, no hands near frame edges, no hands covering product details, no awkward bent wrists, no partial fingers, no floating extra fingers. The outfit must VERY closely match the product references. Preserve the exact garment construction, not just the general style: garment category, color/tone, pattern/print, fabric texture, neckline/collar, sleeve shape and cuffs, front/back closures, seams, waist construction, hem shape, trims, buttons, lace, ruffles, pleats, ties, pockets, panels, layering, and visible set composition. Distinctive product details must be clearly visible and structurally accurate; do not simplify, smooth out, hide, replace, or reinterpret them. Avoid generic fashion interpretation; do not convert the product into a similar-looking but different item. Do not hide important neckline, closure, waist, sleeve, hem, print, or trim details under hijab, pose, arm placement, crop, bag, or accessories. Maintain strict modest chest coverage: no visible cleavage, bust outline emphasis, exposed upper chest, deep/open neckline, sheer chest fabric, tight chest fit, or skin showing below the collarbone. If the product has a v-neck, open collar, loose neckline, sheer panel, or low front opening, style it with a modest inner layer/undershirt or hijab drape that covers the chest while still showing the product neckline shape and key details clearly. The chest area must look covered, natural, and TikTok Muslimah appropriate. The person must not wear, carry, hold, sling, or pose with any bag, purse, handbag, tote, backpack, clutch, crossbody bag, shoulder bag, or strap; no bag-like accessory anywhere in the image. Restyle top, bottom, hijab, and accessories to match the product while keeping modest coverage; use product bottom if visible, otherwise modest matching bottom. Do not keep original jeans, cream hijab, or bag by default. No UI/text/watermark/product model/background. Realistic fit, true TikTok vertical 9:16 composition. Do not zoom out into head-to-toe/full-body framing, do not show shoes, do not show feet, do not show floor space, and do not make the person smaller to show the full outfit.`

5. Validate the generated input image before complete:
- Run only the local hard gate:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py validate-reference <generated_reference_path>
```
- It must be readable, non-empty, non-blank, exact 9:16, and at least 1080x1920. Preferred output remains `2160x3840` high-quality JPEG.
- If it comes back `1024x1536` / 2:3, too small, corrupt, blank, or wrong format, regenerate before continuing.
- Do NOT run or require a semantic product-match/reference-image checker before `complete`, and do not regenerate solely because of semantic product-match concerns. The prompt must still ask the image model to match the product closely; this rule only disables the checker/regeneration loop as a blocker.
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
- Use up to two product PDP images as outfit references when available; do not use only the first PDP image if `product_image_paths` includes a second reference.
- Store links/status in Sheet.
- Generate the caption with AI reasoning from `product_title`; the pipeline's built-in caption is only a fallback, not the final style.
- Captions must not include emoji, including star/sparkle emoji.
- On successful completion and review, reply with `done` plus `job_id`, final status, `result_link`, product image, caption, and a short review verdict. Do not reply with only `done`.
- Telegram/video-done notifications must always include the `job_id` so Naufal can trace the row/job later.
- Telegram/video-done notifications must also include the product image URL only; do not attach/upload the image. Use `product_image_url` when available, otherwise the first URL from `product_image_urls`; if two product refs exist, include only the first product image URL unless Naufal asks for all.
- Keep replies short: start, `done <job_id> <result_link>`, product image URL, or fail only.
- Never expose secrets.
