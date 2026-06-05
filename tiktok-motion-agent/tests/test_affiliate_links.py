from pipeline.affiliate_links import (
    affiliate_state_for_row,
    facebook_object_id_from_url,
    load_affiliate_links,
    normalize_product_url,
    product_key,
    _merge_affiliate_link_rows,
)


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


def test_facebook_object_id_from_page_post_permalink():
    assert facebook_object_id_from_url("https://facebook.com/1054147927792792_122098807347349724") == "1054147927792792_122098807347349724"


def test_load_affiliate_links_read_path_does_not_create_sheet(monkeypatch):
    calls = []

    monkeypatch.setattr("pipeline.affiliate_links._read_local_affiliate_links", lambda: [])

    def fake_get_affiliate_sheet(create=True):
        calls.append(create)
        raise RuntimeError("missing tab")

    monkeypatch.setattr("pipeline.affiliate_links.get_affiliate_sheet", fake_get_affiliate_sheet)

    assert load_affiliate_links(prefer_sheet=True) == []
    assert calls == [False]


def test_merge_affiliate_links_keeps_local_when_sheet_missing_link():
    sheet = [{"product_key": "tiktok_product:123", "tiktok_product_url": "https://www.tiktok.com/view/product/123", "product_name": "Name"}]
    local = [{"product_key": "tiktok_product:123", "shopee_affiliate_url": "https://s.shopee.co.id/abc"}]
    merged = _merge_affiliate_link_rows(sheet, local)
    assert merged[0]["product_name"] == "Name"
    assert merged[0]["shopee_affiliate_url"] == "https://s.shopee.co.id/abc"
