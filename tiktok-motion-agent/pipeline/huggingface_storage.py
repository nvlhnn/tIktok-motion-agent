"""HuggingFace Dataset storage for public result videos."""

import os
import urllib.parse
from pathlib import Path

from huggingface_hub import HfApi

from .config import require_env


def hf_repo_id() -> str:
    return os.environ.get("HF_REPO_ID", "nvlhnn/tiktok-motion").strip()


def hf_revision() -> str:
    return os.environ.get("HF_REVISION", "main").strip() or "main"


def hf_result_prefix() -> str:
    return os.environ.get("HF_RESULT_PREFIX", "results").strip().strip("/") or "results"


def hf_public_url(repo_id: str, repo_path: str, revision: str | None = None) -> str:
    revision = revision or hf_revision()
    return f"https://huggingface.co/datasets/{repo_id}/resolve/{urllib.parse.quote(revision, safe='')}/{urllib.parse.quote(repo_path.strip('/'), safe='/')}"


def hf_upload_file(local_path: Path, repo_path: str, summary: str | None = None) -> str:
    local_path = Path(local_path).expanduser().resolve()
    if not local_path.exists():
        raise RuntimeError(f"HuggingFace upload source not found: {local_path}")
    repo_id = hf_repo_id()
    repo_path = repo_path.strip("/")
    HfApi(token=require_env("HF_TOKEN")).upload_file(
        path_or_fileobj=str(local_path),
        path_in_repo=repo_path,
        repo_id=repo_id,
        repo_type="dataset",
        revision=hf_revision(),
        commit_message=summary or f"Upload {repo_path}",
    )
    return hf_public_url(repo_id, repo_path)


def upload_result_video(local_path: Path, job_id: str, provider: str = "") -> str:
    suffix = Path(local_path).suffix.lower() or ".mp4"
    repo_path = f"{hf_result_prefix()}/{job_id}{suffix}"
    return hf_upload_file(local_path, repo_path, summary=f"Upload result video {job_id}{(' '+provider) if provider else ''}")
