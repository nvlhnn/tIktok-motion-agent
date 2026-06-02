from pathlib import Path

from pipeline.video_processing import hf_upload_720p_target, prepare_hf_upload_video_720p


def test_hf_upload_720p_target_defaults(monkeypatch):
    monkeypatch.delenv("HF_UPLOAD_WIDTH", raising=False)
    monkeypatch.delenv("HF_UPLOAD_HEIGHT", raising=False)
    assert hf_upload_720p_target() == (720, 1280)


def test_prepare_hf_upload_video_720p_disabled(monkeypatch, tmp_path):
    source = tmp_path / "result.mp4"
    source.write_bytes(b"not a real video; disabled path should not inspect")
    monkeypatch.setenv("HF_UPLOAD_720P_ENABLED", "false")
    assert prepare_hf_upload_video_720p(source) == source.resolve()


def test_prepare_hf_upload_video_720p_invokes_ffmpeg(monkeypatch, tmp_path):
    source = tmp_path / "result.mp4"
    source.write_bytes(b"fake")
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"converted")

    monkeypatch.setenv("HF_UPLOAD_720P_ENABLED", "true")
    monkeypatch.setattr("pipeline.video_processing._run", fake_run)

    out = prepare_hf_upload_video_720p(source, output_dir=tmp_path)

    assert out.name == "result_720x1280.mp4"
    assert out.read_bytes() == b"converted"
    assert calls
    cmd = calls[0]
    assert "scale=720:1280:flags=lanczos,setsar=1" in cmd
    assert "veryfast" in cmd
    assert "20" in cmd
