from pipeline.upload import upload_candidates


def _row(provider):
    return {
        "job_id": f"job-{provider or 'none'}",
        "status": "READY_TO_UPLOAD",
        "provider": provider,
        "result_supabase_url": "https://example.com/video.mp4",
        "caption": "caption",
    }


def test_upload_candidates_excludes_figmawave_by_default(monkeypatch):
    monkeypatch.delenv("TIKTOK_UPLOAD_EXCLUDED_PROVIDERS", raising=False)

    candidates = upload_candidates([_row("figmawave"), _row("dreamface")])

    assert [row["provider"] for row in candidates] == ["dreamface"]


def test_upload_candidates_can_allow_figmawave(monkeypatch):
    monkeypatch.setenv("TIKTOK_UPLOAD_EXCLUDED_PROVIDERS", "")

    candidates = upload_candidates([_row("figmawave")])

    assert [row["provider"] for row in candidates] == ["figmawave"]
