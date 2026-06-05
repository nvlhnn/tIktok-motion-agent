from pipeline.affiliate_links import comment_affiliate_for_row, instagram_shortcode_from_url


def test_instagram_shortcode_from_reel_url():
    assert instagram_shortcode_from_url("https://www.instagram.com/reel/DY-86LziXEo/") == "DY-86LziXEo"


def test_comment_affiliate_dry_run_does_not_log(monkeypatch):
    calls = []

    monkeypatch.setattr("pipeline.affiliate_links.load_affiliate_links", lambda prefer_sheet=True: [
        {"product_key": "tiktok_product:123", "shopee_affiliate_url": "https://s.shopee.co.id/abc"}
    ])
    monkeypatch.setattr("pipeline.storage.log_row", lambda row: calls.append(row))

    row = {
        "job_id": "job1",
        "product_url": "https://www.tiktok.com/view/product/123",
        "facebook_post_url": "https://www.facebook.com/reel/12345/",
        "instagram_post_url": "https://www.instagram.com/reel/DY-86LziXEo/",
    }
    updated, result = comment_affiliate_for_row(row, live=False, prefer_sheet=False)

    assert calls == []
    assert updated["fb_comment_status"] == "READY_TO_COMMENT"
    assert updated["ig_comment_status"] == "READY_TO_COMMENT"
    assert all(c.get("dry_run") for c in result["comments"])


def test_comment_affiliate_live_comment_keeps_affiliate_status_as_link_state(monkeypatch):
    monkeypatch.setenv("AFFILIATE_COMMENT_ENABLED", "true")
    monkeypatch.setenv("FACEBOOK_PAGE_ACCESS_TOKEN", "token")
    logged = []

    monkeypatch.setattr("pipeline.affiliate_links.load_affiliate_links", lambda prefer_sheet=True: [
        {"product_key": "tiktok_product:123", "shopee_affiliate_url": "https://s.shopee.co.id/abc"}
    ])
    monkeypatch.setattr("pipeline.affiliate_links._graph_post_comment", lambda object_id, message, token: {"id": "comment-1"})
    monkeypatch.setattr("pipeline.storage.log_row", lambda row: logged.append(row))

    updated, result = comment_affiliate_for_row(
        {"job_id": "job1", "product_url": "https://www.tiktok.com/view/product/123", "facebook_post_url": "https://www.facebook.com/reel/12345/"},
        live=True,
        prefer_sheet=False,
    )

    assert updated["affiliate_status"] == "FOUND"
    assert updated["fb_comment_status"] == "COMMENTED"
    assert result["comments"][0]["comment_id"] == "comment-1"
    assert logged[-1]["affiliate_status"] == "FOUND"


def test_comment_affiliate_missing_link_dry_run_does_not_log(monkeypatch):
    calls = []

    monkeypatch.setattr("pipeline.affiliate_links.load_affiliate_links", lambda prefer_sheet=True: [])
    monkeypatch.setattr("pipeline.storage.log_row", lambda row: calls.append(row))

    updated, result = comment_affiliate_for_row({"job_id": "job1", "product_url": "https://www.tiktok.com/view/product/123"}, live=False, prefer_sheet=False)

    assert calls == []
    assert updated["fb_comment_status"] == "PENDING_LINK"
    assert result["needs_affiliate_link"] is True
