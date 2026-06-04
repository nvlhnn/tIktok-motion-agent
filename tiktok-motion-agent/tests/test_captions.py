"""Tests for pipeline.captions."""

from pipeline.captions import (
    clean_product_title, caption_keywords, caption_tags,
    build_tiktok_caption, build_caption_phrase, caption_parts,
)


GENERIC_PHRASES = ["cakep bgt", "manis bgt", "adem bgt", "simple cakep"]


def phrase_part(caption: str) -> str:
    return " ".join([w for w in caption.split() if not w.startswith("#")])


def test_clean_product_title_strips_brackets():
    assert clean_product_title("[PROMO] Kemeja Wanita") == "Kemeja Wanita"
    assert clean_product_title("Blouse (import)") == "Blouse"


def test_clean_product_title_strips_special_chars():
    result = clean_product_title("Kemeja★Premium!!!")
    assert "★" not in result
    assert "!" not in result


def test_clean_product_title_normalizes_whitespace():
    assert clean_product_title("  Kemeja   Wanita  ") == "Kemeja Wanita"


def test_caption_keywords_picks_meaningful_words():
    kws = caption_keywords("Kemeja Bordir Wanita Premium Terbaru")
    assert "bordir" in kws
    assert "kemeja" in kws
    assert "wanita" not in kws
    assert "premium" not in kws


def test_caption_keywords_limit():
    kws = caption_keywords("Sweater Rajut Cardigan Bordir", limit=2)
    assert len(kws) <= 2


def test_caption_parts_extracts_details_color_category():
    parts = caption_parts("Cardigan Beige Plisket Rajut")
    assert "cardi" in parts["categories"]
    assert "beige" in parts["colors"]
    assert "plisket" in parts["details"]


def test_caption_tags_are_fixed():
    tags = caption_tags("Kemeja Bordir Wanita")
    assert tags == ["#fyp", "#muslimah", "#outfitideas", "#ootdhijab", "#outfittiktok"]


def test_caption_tags_do_not_vary_by_product_type():
    tags = caption_tags("Something Random Product")
    assert tags == ["#fyp", "#muslimah", "#outfitideas", "#ootdhijab", "#outfittiktok"]


def test_build_caption_phrase_is_short():
    phrase = build_caption_phrase("Ruffle Salur Blouse")
    assert len(phrase.split()) <= 5


def test_build_tiktok_caption_avoids_repeated_generic_phrases():
    examples = [
        "Ruffle Salur Blouse Wanita",
        "Sasmita Salur Blouse Cheongsam",
        "Kemeja Korea Wanita",
        "Bordir Bunga Dress",
        "Cardigan Plisket Beige",
    ]
    captions = [build_tiktok_caption(x) for x in examples]
    assert not any(any(g in c for g in GENERIC_PHRASES) for c in captions)


def test_build_tiktok_caption_hashtags_max_five():
    result = build_tiktok_caption("Ruffle Rajut Shandira Blouse")
    hashtags = [w for w in result.split() if w.startswith("#")]
    assert 1 <= len(hashtags) <= 5


def test_build_tiktok_caption_is_deterministic():
    title = "Cardigan Hits Moonlife Cardi Oriza Knit Pleats"
    assert build_tiktok_caption(title) == build_tiktok_caption(title)


def test_build_tiktok_caption_varies_by_title():
    a = phrase_part(build_tiktok_caption("Cardigan Plisket Beige"))
    b = phrase_part(build_tiktok_caption("Kulot Grey Cutbray"))
    assert a != b


def test_build_tiktok_caption_empty():
    result = build_tiktok_caption("")
    assert result
    assert len(phrase_part(result).split()) <= 5


def test_build_tiktok_caption_no_emoji():
    result = build_tiktok_caption("Kemeja Bordir Wanita")
    for char in result:
        assert ord(char) < 0x1F600 or ord(char) > 0x1F64F, f"Found emoji: {char}"


def test_build_tiktok_caption_no_ini():
    result = build_tiktok_caption("Blouse ini bagus")
    words = phrase_part(result).split()
    assert "ini" not in words
