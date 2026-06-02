import json
import mimetypes
import os
from pathlib import Path

import requests


def dreamface_auths() -> list[dict]:
    """Load DreamFace auth pool from env.

    Preferred env shape:
      DREAMFACE_AUTHS_JSON='[{"label":"df1","token":"...","account_id":"...","user_id":"...","client_id":"...","cookie":"..."}]'

    Fallback single-auth envs are also supported:
      DREAMFACE_TOKEN, DREAMFACE_ACCOUNT_ID, DREAMFACE_USER_ID,
      DREAMFACE_CLIENT_ID, DREAMFACE_COOKIE, DREAMFACE_AUTH_LABEL
    """
    raw = os.environ.get("DREAMFACE_AUTHS_JSON", "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        raw = raw[1:-1]
    auths: list[dict] = []
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("auths") or parsed.get("accounts") or []
        if not isinstance(parsed, list):
            raise RuntimeError("DREAMFACE_AUTHS_JSON must be a JSON list or object with auths/accounts")
        auths = [dict(a) for a in parsed]
    elif os.environ.get("DREAMFACE_TOKEN"):
        auths = [{
            "label": os.environ.get("DREAMFACE_AUTH_LABEL") or "dreamface-1",
            "token": os.environ.get("DREAMFACE_TOKEN"),
            "cookie": os.environ.get("DREAMFACE_COOKIE", ""),
            "client_id": os.environ.get("DREAMFACE_CLIENT_ID", "3fac5089bfb993119e743f657cb31b2b"),
            "account_id": os.environ.get("DREAMFACE_ACCOUNT_ID"),
            "user_id": os.environ.get("DREAMFACE_USER_ID"),
        }]

    cleaned = []
    for idx, auth in enumerate(auths, start=1):
        auth.setdefault("label", f"dreamface-{idx}")
        auth.setdefault("client_id", "3fac5089bfb993119e743f657cb31b2b")
        missing = [k for k in ["token", "account_id", "user_id"] if not auth.get(k)]
        if missing:
            raise RuntimeError(f"DreamFace auth {auth.get('label')!r} missing: {', '.join(missing)}")
        cleaned.append(auth)
    if not cleaned:
        raise RuntimeError("No DreamFace auth configured. Set DREAMFACE_AUTHS_JSON or DREAMFACE_TOKEN/ACCOUNT_ID/USER_ID.")
    return cleaned


def dreamface_headers(auth: dict, referer: str = "https://www.dreamfaceapp.com/creation", json_body: bool = True) -> dict:
    headers = {
        "accept": "application/json",
        "accept-language": "en-US,en;q=0.9",
        "client-id": str(auth.get("client_id") or "3fac5089bfb993119e743f657cb31b2b"),
        "dream-face-web": "dream-face-web",
        "origin": "https://www.dreamfaceapp.com",
        "referer": referer,
        "token": str(auth["token"]),
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }
    if json_body:
        headers["content-type"] = "application/json"
    if auth.get("cookie"):
        headers["Cookie"] = str(auth["cookie"])
    return headers


def dreamface_request(method: str, path: str, auth: dict, *, json_body: dict | None = None, params: dict | None = None, referer: str = "https://www.dreamfaceapp.com/creation") -> dict:
    url = f"https://www.dreamfaceapp.com{path}"
    r = requests.request(
        method,
        url,
        headers=dreamface_headers(auth, referer=referer, json_body=json_body is not None),
        json=json_body,
        params=params,
        timeout=120,
    )
    text = r.text
    try:
        data = r.json()
    except Exception:
        data = {"raw": text}
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"DreamFace {method} {path} failed {r.status_code}: {data}")
    if data.get("status_code") and data.get("status_code") != "THS12140000000":
        raise RuntimeError(f"DreamFace {method} {path} returned {data.get('status_code')}: {data.get('status_msg') or data}")
    return data


def dreamface_quota(auth: dict) -> dict:
    body = {"user_id": auth["user_id"], "account_id": auth["account_id"]}
    data = dreamface_request("POST", "/dw-server/rights/get_free_rights", auth, json_body=body)
    return data.get("data") or {}


def dreamface_remaining_credits(auth: dict) -> dict:
    """Return DreamFace credit balances from the credits endpoint."""
    body = {
        "user_id": auth["user_id"],
        "account_id": auth["account_id"],
        "time_zone": os.environ.get("DREAMFACE_TIME_ZONE", "Asia/Jakarta"),
    }
    data = dreamface_request(
        "POST",
        "/dw-server/credits/get_remaining_credits",
        auth,
        json_body=body,
        referer="https://www.dreamfaceapp.com/home",
    )
    return data.get("data") or {}


def dreamface_free_task_times(auth: dict) -> dict:
    """Return the real daily free render counter for DreamAct/REPLACE_DANCE.

    DreamFace's older /rights/get_free_rights can report remain_count=5 even
    when /task/v2/submit immediately returns web_work_status -3
    (credits_not_enough). The web app checks get_free_task_times for the daily
    free render. Count this endpoint plus credits.free_count as usable quota.
    """
    body = {
        "biz_type": os.environ.get("DREAMFACE_TEMPLATE_ID", "REPLACE_DANCE"),
        "user_id": auth["user_id"],
    }
    data = dreamface_request(
        "POST",
        "/dw-server/rights/get_free_task_times",
        auth,
        json_body=body,
        referer="https://www.dreamfaceapp.com/apps/dreamact",
    )
    return data.get("data") or {}


def dreamface_combined_quota(auth: dict) -> dict:
    """Return usable DreamFace quota.

    Usable quota is daily free task times + credits.free_count (+ paid_count if
    present). Keep get_free_rights in the diagnostic payload only; do not count
    its remain_count because it has proven unusable for DreamAct renders.
    """
    legacy_rights = dreamface_quota(auth)
    daily = dreamface_free_task_times(auth)
    credits = dreamface_remaining_credits(auth)
    daily_free_count = int(daily.get("free_times") or 0)
    credits_free_count = int(credits.get("free_count") or 0)
    credits_paid_count = int(credits.get("paid_count") or 0)
    return {
        "quota_source": "rights/get_free_task_times + credits/get_remaining_credits (legacy get_free_rights diagnostic only)",
        "daily_free_count": daily_free_count,
        "daily_total_free_count": daily.get("total_free_times"),
        "daily_this_free": daily.get("this_free"),
        "credits_free_count": credits_free_count,
        "credits_paid_count": credits_paid_count,
        "credits_free_expires_time": credits.get("free_expires_time"),
        "legacy_free_remain_count": legacy_rights.get("remain_count"),
        "legacy_free_total_count": legacy_rights.get("total_count"),
        "available_count": daily_free_count + credits_free_count + credits_paid_count,
        "daily_quota": daily,
        "legacy_free_quota": legacy_rights,
        "credits_quota": credits,
    }


def dreamface_available_count(quota: dict) -> int:
    if "available_count" in quota:
        return int(quota.get("available_count") or 0)
    return int(quota.get("daily_free_count") or 0) + int(quota.get("credits_free_count") or 0) + int(quota.get("credits_paid_count") or 0)


def select_dreamface_auth() -> tuple[dict, dict]:
    def label_rank(auth: dict) -> int:
        label = str(auth.get("label") or "")
        try:
            return int(label.rsplit("-", 1)[-1])
        except Exception:
            return 0

    exhausted = []
    candidates = []
    for auth in dreamface_auths():
        quota = dreamface_combined_quota(auth)
        remain = dreamface_available_count(quota)
        if remain > 0:
            # Prefer daily free because it expires/reset fastest, then weekly
            # free credits, then paid credits if any are ever configured. For
            # exact quota ties, prefer the newest/largest-numbered account so
            # new daily-free accounts are used before older equal accounts.
            candidates.append((
                int(quota.get("daily_free_count") or 0),
                int(quota.get("credits_free_count") or 0),
                int(quota.get("credits_paid_count") or 0),
                label_rank(auth),
                auth,
                quota,
            ))
        else:
            exhausted.append({"label": auth.get("label"), "quota": quota})
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
        return candidates[0][4], candidates[0][5]
    raise RuntimeError(json.dumps({
        "ok": False,
        "code": "DREAMFACE_QUOTA_EXHAUSTED",
        "message": "No usable DreamFace quota left. Need daily free task time or credits_free/paid credits; legacy free_rights is ignored.",
        "auths": exhausted,
    }, ensure_ascii=False))


def dreamface_submit(auth: dict, image_url: str, video_url: str) -> str:
    body = {
        "media": {
            "images": [{"url": image_url}],
            "videos": [{"url": video_url}],
            "texts": [],
            "audios": [],
        },
        "user": {
            "account_id": auth["account_id"],
            "app_version": os.environ.get("DREAMFACE_APP_VERSION", "4.7.1"),
            "platform_type": "WEB",
            "user_id": auth["user_id"],
        },
        "template": {
            "template_id": os.environ.get("DREAMFACE_TEMPLATE_ID", "REPLACE_DANCE"),
            "play_types": [os.environ.get("DREAMFACE_TEMPLATE_ID", "REPLACE_DANCE")],
            "project_id": "",
        },
        "output": {
            "width": int(os.environ.get("DREAMFACE_OUTPUT_WIDTH", "1080")),
            "height": int(os.environ.get("DREAMFACE_OUTPUT_HEIGHT", "1080")),
            "ratio": os.environ.get("DREAMFACE_OUTPUT_RATIO", "1:1"),
            "duration": int(os.environ.get("DREAMFACE_DURATION", "5")),
            "resolution": os.environ.get("DREAMFACE_RESOLUTION", "480P"),
            "vertical": os.environ.get("DREAMFACE_VERTICAL", "true").lower() != "false",
            "replace_background": os.environ.get("DREAMFACE_REPLACE_BACKGROUND", "false").lower() == "true",
        },
        "ext_info": {
            "sing_title": os.environ.get("DREAMFACE_WORK_NAME", "ACT ANIMATE"),
            "is_sound_effect": os.environ.get("DREAMFACE_SOUND_EFFECT", "true").lower() != "false",
            "animate_channel": "",
            "route_url": "",
            "timbre_id": "",
            "cover": "",
            "video_id": "",
            "genders": [],
        },
        "work_type": os.environ.get("DREAMFACE_WORK_TYPE", "ACT_ANIMATE"),
        "create_work_session": False,
        "asset_info": {"asset_id": "", "original_video_url": "", "file_name": ""},
    }
    data = dreamface_request("POST", "/dw-server/task/v2/submit", auth, json_body=body, referer="https://www.dreamfaceapp.com/apps/dreamact")
    animate_id = (data.get("data") or {}).get("animate_image_id")
    if not animate_id:
        raise RuntimeError(f"No animate_image_id from DreamFace submit: {data}")
    return animate_id


def dreamface_upload_material(auth: dict, path: str | os.PathLike) -> str:
    """Upload an image/video material to DreamFace USS3 and return its public URL.

    DreamFace currently rejects Supabase public URLs during task submit because
    that domain is not on its OSS whitelist. The web app first uploads local
    materials through this endpoint and submits the returned uss3.dreamfaceapp.com
    URL, which is accepted by /task/v2/submit.
    """
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise RuntimeError(f"DreamFace material upload file not found: {file_path}")
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    headers = dreamface_headers(auth, referer="https://www.dreamfaceapp.com/apps/dreamact", json_body=False)
    url = f"https://www.dreamfaceapp.com/dw-server/phone_file/upload_uss3_server/WEB_ANIMATE_MATERIAL?user_id={auth['user_id']}"
    with file_path.open("rb") as fh:
        r = requests.post(
            url,
            headers=headers,
            files={"file": (file_path.name, fh, mime)},
            timeout=300,
        )
    text = r.text
    try:
        data = r.json()
    except Exception:
        data = {"raw": text}
    if not (200 <= r.status_code < 300):
        raise RuntimeError(f"DreamFace material upload failed {r.status_code}: {data}")
    if data.get("status_code") and data.get("status_code") != "THS12140000000":
        raise RuntimeError(f"DreamFace material upload returned {data.get('status_code')}: {data.get('status_msg') or data}")
    uploaded_url = (data.get("data") or {}).get("file_path") or (data.get("data") or {}).get("url")
    if not uploaded_url:
        raise RuntimeError(f"DreamFace material upload returned no URL: {data}")
    return uploaded_url


def dreamface_recent_creation(auth: dict, animate_id: str, size: int | None = None) -> dict | None:
    # Size 1 matches the normal single-worker account flow. If multiple systems
    # share one DreamFace account, bump DREAMFACE_RECENT_SIZE and match animate_id.
    size = size or int(os.environ.get("DREAMFACE_RECENT_SIZE", "1"))
    body = {
        "user_id": auth["user_id"],
        "account_id": auth["account_id"],
        "page": 1,
        "size": size,
        "is_web": True,
        "app_version": os.environ.get("DREAMFACE_APP_VERSION", "4.7.1"),
    }
    data = dreamface_request("POST", "/dw-server/work/v2/get_recent_creation_list", auth, json_body=body)
    items = ((data.get("data") or {}).get("list") or [])
    for item in items:
        if item.get("animate_id") == animate_id:
            return item
    return items[0] if size == 1 and items else None


def dreamface_work_detail(auth: dict, work_id: str) -> dict:
    data = dreamface_request("GET", "/dw-server/work/get_work_detail_web", auth, params={"work_id": work_id}, json_body=None)
    return data.get("data") or {}


def max_dreamface_wait_seconds() -> int:
    return int(os.environ.get("DREAMFACE_MAX_WAIT_SECONDS", "3600"))


def dreamface_poll_interval_seconds() -> int:
    return int(os.environ.get("DREAMFACE_POLL_INTERVAL_SECONDS", "45"))
