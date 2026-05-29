import json
import os
import sys
import time
from pathlib import Path

import requests

from ..config import ROOT, require_env


def magnific_function_url() -> str:
    return f"https://{require_env('SUPABASE_PROJECT_REF')}.supabase.co/functions/v1/magnific-motion"


def retryable_magnific_error(status_code: int, text: str) -> bool:
    lowered = text.lower()
    return status_code in {403, 500, 502, 503, 504} or "blocked" in lowered or "rate" in lowered


class MagnificApiError(RuntimeError):
    def __init__(self, status_code: int | None, data: dict, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


def magnific_auths() -> list[dict]:
    """Load Magnific auth pool from env.

    Preferred env shape:
      MAGNIFIC_AUTHS_JSON='[{"label":"magnific-1","api_key":"FPSX..."}]'

    Also supported:
      MAGNIFIC_API_KEYS_JSON='["FPSX...", {"label":"m2","api_key":"FPSX..."}]'
      MAGNIFIC_API_KEYS='FPSX...,FPSX...'
      MAGNIFIC_API_KEY='FPSX...'
    """
    auths: list[dict] = []
    raw = os.environ.get("MAGNIFIC_AUTHS_JSON", "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("auths") or parsed.get("accounts") or []
        if not isinstance(parsed, list):
            raise RuntimeError("MAGNIFIC_AUTHS_JSON must be a JSON list or object with auths/accounts")
        auths = [dict(a) for a in parsed]
    else:
        keys_raw = os.environ.get("MAGNIFIC_API_KEYS_JSON", "").strip()
        if len(keys_raw) >= 2 and keys_raw[0] == keys_raw[-1] and keys_raw[0] in {"'", '"'}:
            keys_raw = keys_raw[1:-1]
        if keys_raw:
            parsed = json.loads(keys_raw)
            if not isinstance(parsed, list):
                raise RuntimeError("MAGNIFIC_API_KEYS_JSON must be a JSON list")
            for idx, item in enumerate(parsed, start=1):
                if isinstance(item, str):
                    auths.append({"label": f"magnific-{idx}", "api_key": item})
                elif isinstance(item, dict):
                    auths.append(dict(item))
                else:
                    raise RuntimeError("MAGNIFIC_API_KEYS_JSON entries must be strings or objects")
        elif os.environ.get("MAGNIFIC_API_KEYS"):
            for idx, key in enumerate([k.strip() for k in os.environ["MAGNIFIC_API_KEYS"].split(",") if k.strip()], start=1):
                auths.append({"label": f"magnific-{idx}", "api_key": key})
        elif os.environ.get("MAGNIFIC_API_KEY"):
            auths.append({"label": os.environ.get("MAGNIFIC_AUTH_LABEL") or "magnific-1", "api_key": os.environ["MAGNIFIC_API_KEY"]})

    cleaned = []
    for idx, auth in enumerate(auths, start=1):
        auth.setdefault("label", f"magnific-{idx}")
        if not auth.get("api_key"):
            raise RuntimeError(f"Magnific auth {auth.get('label')!r} missing api_key")
        cleaned.append(auth)
    if not cleaned:
        raise RuntimeError("No Magnific auth configured. Set MAGNIFIC_AUTHS_JSON, MAGNIFIC_API_KEYS, or MAGNIFIC_API_KEY.")
    return cleaned


def magnific_auth_by_label(label: str | None) -> dict | None:
    if not label:
        return None
    for auth in magnific_auths():
        if auth.get("label") == label:
            return auth
    return None


def is_magnific_quota_error(error: Exception) -> bool:
    if not isinstance(error, MagnificApiError):
        return False
    text = json.dumps(error.data, ensure_ascii=False).lower()
    return error.status_code == 429 or "limit" in text or "quota" in text or "free trial" in text


def remove_magnific_auth_from_env(limited_auth: dict):
    """Remove a quota-limited Magnific key from ROOT/.env for future runs.

    Supports every auth-pool env shape accepted by magnific_auths(). The current
    process keeps its already-loaded in-memory auth list; this persists the
    removal so the next generation/retry will not reuse a dead key.
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    key = limited_auth.get("api_key")
    label = limited_auth.get("label")
    if not key:
        return

    changed = False
    output = []
    for line in env_path.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        raw = value.strip()
        unquoted = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'} else raw

        if name == "MAGNIFIC_AUTHS_JSON":
            try:
                parsed = json.loads(unquoted)
                wrapper_key = None
                if isinstance(parsed, dict):
                    wrapper_key = "auths" if "auths" in parsed else "accounts" if "accounts" in parsed else None
                    auths = parsed.get(wrapper_key) if wrapper_key else []
                else:
                    auths = parsed
                if isinstance(auths, list):
                    kept = [a for a in auths if not (isinstance(a, dict) and (a.get("api_key") == key or (label and a.get("label") == label)))]
                    changed = changed or len(kept) != len(auths)
                    if wrapper_key:
                        parsed[wrapper_key] = kept
                        output.append(f"{name}={json.dumps(parsed, separators=(',', ':'))}")
                    else:
                        output.append(f"{name}={json.dumps(kept, separators=(',', ':'))}")
                    continue
            except Exception:
                pass

        if name == "MAGNIFIC_API_KEYS_JSON":
            try:
                parsed = json.loads(unquoted)
                if isinstance(parsed, list):
                    kept = []
                    for item in parsed:
                        if isinstance(item, str) and item == key:
                            changed = True
                            continue
                        if isinstance(item, dict) and (item.get("api_key") == key or (label and item.get("label") == label)):
                            changed = True
                            continue
                        kept.append(item)
                    output.append(f"{name}={json.dumps(kept, separators=(',', ':'))}")
                    continue
            except Exception:
                pass

        if name == "MAGNIFIC_API_KEYS":
            keys = [k.strip() for k in raw.split(",") if k.strip()]
            kept = [k for k in keys if k != key]
            changed = changed or len(kept) != len(keys)
            output.append(f"{name}={','.join(kept)}")
            continue

        if name == "MAGNIFIC_API_KEY" and raw == key:
            changed = True
            continue

        output.append(line)

    if changed:
        env_path.write_text("\n".join(output).rstrip() + "\n")
        print(f"Removed quota-limited Magnific auth from .env: {label or 'unlabeled'}", file=sys.stderr)


def magnific_post(payload: dict, auth: dict | None = None) -> dict:
    auth = auth or magnific_auths()[0]
    api_key = auth["api_key"]
    max_retries = int(os.environ.get("MAGNIFIC_MAX_RETRIES", "5"))
    retry_delay = int(os.environ.get("MAGNIFIC_RETRY_DELAY_SECONDS", "30"))
    last_detail = None
    for attempt in range(max_retries + 1):
        r = requests.post(
            magnific_function_url(),
            headers={"Content-Type": "application/json", "x-magnific-api-key": api_key},
            json=payload,
            timeout=120,
        )
        text = r.text
        try:
            data = r.json()
        except Exception:
            data = {"raw": text}
        if 200 <= r.status_code < 300:
            return data
        last_detail = f"Magnific function failed {r.status_code}: {data}"
        if attempt < max_retries and retryable_magnific_error(r.status_code, text):
            time.sleep(retry_delay)
            continue
        raise MagnificApiError(r.status_code, data, last_detail)
    raise MagnificApiError(None, {}, last_detail or "Magnific function failed")


def magnific_generate_with_rotation(payload: dict, preferred_label: str | None = None) -> tuple[dict, dict]:
    auths = magnific_auths()
    if preferred_label:
        preferred = magnific_auth_by_label(preferred_label)
        if not preferred:
            raise RuntimeError(f"Configured Magnific auth not found for label: {preferred_label}")
        auths = [preferred]

    quota_errors = []
    for auth in auths:
        try:
            return magnific_post(payload, auth=auth), auth
        except Exception as e:
            if is_magnific_quota_error(e):
                quota_errors.append({"label": auth.get("label"), "error": str(e)})
                remove_magnific_auth_from_env(auth)
                continue
            raise
    raise RuntimeError(json.dumps({
        "ok": False,
        "code": "MAGNIFIC_QUOTA_EXHAUSTED",
        "message": "All configured Magnific API keys are quota-limited.",
        "auths": quota_errors,
    }, ensure_ascii=False))


def max_magnific_wait_seconds() -> int:
    return int(os.environ.get("MAGNIFIC_MAX_WAIT_SECONDS", "3600"))


def check_magnific_timeout(started_at: float, task_id: str):
    if time.time() - started_at > max_magnific_wait_seconds():
        raise TimeoutError(f"Magnific task timed out: {task_id}")
