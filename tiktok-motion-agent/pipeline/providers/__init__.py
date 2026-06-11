import os


def selected_video_provider(provider: str | None = None) -> str:
    value = (provider or os.environ.get("VIDEO_PROVIDER") or "magnific").strip().lower()
    aliases = {
        "magnefic": "magnific",
        "dream_face": "dreamface",
        "dream-face": "dreamface",
        "figma_wave": "figmawave",
        "figma-wave": "figmawave",
        "weavy": "figmawave",
        "figmawave": "figmawave",
    }
    value = aliases.get(value, value)
    if value not in {"magnific", "dreamface", "figmawave"}:
        raise RuntimeError(f"Unsupported VIDEO_PROVIDER: {value!r}")
    return value
