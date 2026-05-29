"""Tests for pipeline.captions."""

from pipeline.captions import (
    clean_product_title, caption_keywords, caption_tags,
    build_tiktok_caption,
)


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
    # "wanita", "premium", "terbaru" are stopwords; "kemeja" is < 4 chars wait no it's 6
    assert "bordir" in kws
    assert "kemeja" in kws
    assert "wanita" not in kws  # stopword
    assert "premium" not in kws  # stopword


def test_caption_keywords_limit():
    kws = caption_keywords("Sweater Rajut Cardigan Bordir", limit=2)
    assert len(kws) <= 2


def test_caption_tags_maps_product_type():
    tags = caption_tags("Kemeja Bordir Wanita")
    assert "#kemejawanita" in tags
    assert len(tags) <= 6


def test_caption_tags_always_has_base_tags():
    tags = caption_tags("Something Random Product")
    # Base tags should always be included
    assert any("#atasanwanita" in t for t in tags)


def test_build_tiktok_caption_bordir():
    result = build_tiktok_caption("Kemeja Bordir Wanita Premium")
    assert "bordirnya manis bgt" in result
    assert "#" in result  # has hashtags


def test_build_tiktok_caption_denim():
    result = build_tiktok_caption("Kemeja Denim Wanita Import")
    assert "denim gini cakep" in result


def test_build_tiktok_caption_rajut():
    result = build_tiktok_caption("Sweater Rajut Wanita")
    assert "rajutnya cakep bgt" in result


def test_build_tiktok_caption_blouse():
    result = build_tiktok_caption("Blouse Simple Cewek Korea")
    assert "blouse simple cakep" in result


def test_build_tiktok_caption_outer():
    result = build_tiktok_caption("Cardigan Outer Wanita")
    assert "outer kepake terus" in result


def test_build_tiktok_caption_kemeja():
    result = build_tiktok_caption("Kemeja Putih Wanita")
    assert "kemejanya clean bgt" in result


def test_build_tiktok_caption_generic():
    result = build_tiktok_caption("Random Product Name Here")
    assert "cakep" in result  # keyword fallback or generic


def test_build_tiktok_caption_empty():
    result = build_tiktok_caption("")
    assert "simple tapi cakep" in result


def test_build_tiktok_caption_no_emoji():
    result = build_tiktok_caption("Kemeja Bordir Wanita")
    # Should not contain emoji
    for char in result:
        assert ord(char) < 0x1F600 or ord(char) > 0x1F64F, f"Found emoji: {char}"


def test_build_tiktok_caption_no_ini():
    result = build_tiktok_caption("Blouse ini bagus")
    words = result.split()
    # "ini" should not appear as a keyword
    assert "ini" not in words[:5]  # check the phrase part before hashtags
