from pipeline.affiliate_links import affiliate_state_for_row, normalize_product_url, product_key


def test_product_key_extracts_tiktok_product_id():
    assert product_key("https://www.tiktok.com/view/product/1734423297683523312?utm_source=x") == "tiktok_product:1734423297683523312"


def test_normalize_product_url_strips_tracking():
    assert normalize_product_url("https://www.tiktok.com/view/product/123/?utm_source=x&fbclid=y") == "https://www.tiktok.com/view/product/123"


def test_affiliate_state_missing_is_non_blocking():
    state = affiliate_state_for_row({"product_url": "https://www.tiktok.com/view/product/123"}, [])
    assert state["affiliate_status"] == "MISSING"
    assert state["product_key"] == "tiktok_product:123"
    assert "posting continues" in state["action_needed"]


def test_affiliate_state_found_reuses_link():
    row = {"product_url": "https://www.tiktok.com/view/product/123"}
    mapping = [{"product_key": "tiktok_product:123", "shopee_affiliate_url": "https://s.shopee.co.id/abc"}]
    state = affiliate_state_for_row(row, mapping)
    assert state["affiliate_status"] == "FOUND"
    assert state["shopee_affiliate_url"] == "https://s.shopee.co.id/abc"
