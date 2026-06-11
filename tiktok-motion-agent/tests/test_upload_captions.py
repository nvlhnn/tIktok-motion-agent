from pipeline.upload import caption_for_buffer_channel, caption_with_affiliate_link, strip_affiliate_link


def test_strip_affiliate_link_removes_trailing_shopee_block():
    assert strip_affiliate_link("caption\n\nLink Shopee: https://s.shopee.co.id/abc") == "caption"


def test_caption_for_buffer_channel_never_adds_affiliate_to_tiktok(monkeypatch):
    monkeypatch.setenv("BUFFER_FACEBOOK_CHANNEL_ID", "fb")
    monkeypatch.setenv("BUFFER_INSTAGRAM_CHANNEL_ID", "ig")
    aff = {"shopee_affiliate_url": "https://s.shopee.co.id/abc"}

    assert caption_for_buffer_channel("caption", aff, "tiktok") == "caption"
    assert caption_for_buffer_channel("caption\n\nLink Shopee: https://s.shopee.co.id/old", aff, "tiktok") == "caption"


def test_caption_for_buffer_channel_adds_affiliate_only_to_fb_ig(monkeypatch):
    monkeypatch.setenv("BUFFER_FACEBOOK_CHANNEL_ID", "fb")
    monkeypatch.setenv("BUFFER_INSTAGRAM_CHANNEL_ID", "ig")
    aff = {"shopee_affiliate_url": "https://s.shopee.co.id/abc"}

    assert caption_for_buffer_channel("caption", aff, "fb") == "caption\n\nLink Shopee: https://s.shopee.co.id/abc"
    assert caption_for_buffer_channel("caption", aff, "ig") == "caption\n\nLink Shopee: https://s.shopee.co.id/abc"


def test_caption_with_affiliate_link_replaces_existing_link():
    aff = {"shopee_affiliate_url": "https://s.shopee.co.id/new"}
    assert caption_with_affiliate_link("caption\n\nLink Shopee: https://s.shopee.co.id/old", aff) == "caption\n\nLink Shopee: https://s.shopee.co.id/new"
