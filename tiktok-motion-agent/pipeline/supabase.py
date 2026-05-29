import mimetypes
import os
import subprocess
import urllib.parse
from pathlib import Path

import requests

from .config import require_env


def public_storage_url(object_path: str) -> str:
    project_ref = require_env("SUPABASE_PROJECT_REF")
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    return f"https://{project_ref}.supabase.co/storage/v1/object/public/{urllib.parse.quote(bucket, safe='')}/{urllib.parse.quote(object_path, safe='/')}"


def supabase_upload(local_path: Path, object_path: str) -> str:
    """Upload a file to the configured public Supabase bucket and return public URL."""
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    token = require_env("SUPABASE_ACCESS_TOKEN")
    local_path = Path(local_path)
    if not local_path.exists():
        raise RuntimeError(f"Upload source not found: {local_path}")
    target = f"ss:///{bucket}/{object_path.strip('/')}"
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    public_url = public_storage_url(object_path.strip('/'))
    content_type = mimetypes.guess_type(str(local_path))[0]
    if local_path.suffix.lower() == ".mp4":
        content_type = "video/mp4"
    cmd = [
        "npx", "supabase", "--experimental", "storage", "cp", str(local_path), target, "--linked",
    ]
    if content_type:
        cmd.extend(["--content-type", content_type])
    try:
        subprocess.run(cmd, cwd="/root/.openclaw/workspace", env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        output = e.stdout or ""
        # Supabase CLI sometimes uploads the object, then exits 1 with a
        # Duplicate/409 on retry. Treat it as success only if the public object
        # is actually reachable.
        if "Duplicate" in output or '"statusCode":"409"' in output or "already exists" in output:
            try:
                r = requests.head(public_url, timeout=30)
                if 200 <= r.status_code < 300:
                    return public_url
            except Exception:
                pass
        raise RuntimeError(f"Supabase upload failed for {target}: {output.strip() or e}") from e
    return public_url


def supabase_rm_prefix(prefix: str, dry_run: bool = False):
    """Delete a Supabase Storage prefix from the configured public bucket."""
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    token = require_env("SUPABASE_ACCESS_TOKEN")
    prefix = prefix.strip("/")
    if not prefix:
        raise RuntimeError("Refusing to delete empty Supabase prefix")
    target = f"ss:///{bucket}/{prefix}"
    if dry_run:
        return {"target": target, "dry_run": True}
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    subprocess.run([
        "npx", "supabase", "--experimental", "storage", "rm", "-r", target, "--linked",
    ], cwd="/root/.openclaw/workspace", env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return {"target": target, "dry_run": False}


def supabase_rm_object(object_path: str, dry_run: bool = False):
    """Delete one Supabase Storage object from the configured public bucket."""
    bucket = require_env("SUPABASE_PUBLIC_BUCKET")
    token = require_env("SUPABASE_ACCESS_TOKEN")
    object_path = object_path.strip("/")
    if not object_path:
        raise RuntimeError("Refusing to delete empty Supabase object path")
    if not object_path.startswith("magnific/automation/"):
        raise RuntimeError(f"Refusing to delete non-automation Supabase object: {object_path}")
    target = f"ss:///{bucket}/{object_path}"
    if dry_run:
        return {"target": target, "dry_run": True}
    env = os.environ.copy()
    env["SUPABASE_ACCESS_TOKEN"] = token
    try:
        subprocess.run([
            "npx", "supabase", "--experimental", "storage", "rm", target, "--linked",
        ], cwd="/root/.openclaw/workspace", env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except subprocess.CalledProcessError as e:
        output = e.stdout or ""
        if any(s in output.lower() for s in ["not found", "no such", "404"]):
            return {"target": target, "dry_run": False, "already_missing": True}
        raise RuntimeError(f"Supabase delete failed for {target}: {output.strip() or e}") from e
    return {"target": target, "dry_run": False}
