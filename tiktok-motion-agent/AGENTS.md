# TikTok Motion Agent

When Naufal says **go**, run the automation with minimal chat.

## Flow

1. Prepare:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py prepare
```

2. Parse only:
- `job_id`
- `master_path`
- `product_image_path`

3. Generate image with OpenClaw `image_generate` using quota-friendly settings:
- refs: `master_path`, `product_image_path`
- model: `openai/gpt-image-2`
- size: `2160x3840`
- quality: `medium`
- do not use `aspectRatio` with OpenAI; OpenAI does not receive it directly. Use this supported 9:16 size instead.
- prompt: `Preserve master face/identity, pose, lighting, and wooden-door background. Apply outfit style from product image only. Restyle top, bottom, hijab, and accessories to match; use product bottom if visible, otherwise modest matching bottom. Do not keep original jeans, cream hijab, or bag by default. No UI/text/watermark/product model/background. Realistic fit, true TikTok vertical 9:16 full-body framing, no crop/cut-off.`

4. Complete:
```bash
/root/.openclaw/workspace/tiktok-motion-agent/.venv/bin/python /root/.openclaw/workspace/tiktok-motion-agent/motion_pipeline.py complete <job_id> <generated_reference_path>
```

## Rules

- Product video and motion video must be different.
- Use product URL's first PDP image as outfit reference.
- Store links/status in Sheet; do not send final video unless asked.
- Keep replies short: start, done, or fail only.
- Never expose secrets.
