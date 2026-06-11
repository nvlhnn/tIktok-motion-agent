import copy
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import requests


API_BASE = "https://api.weavy.ai/api/v1"
DEFAULT_RECIPE_ID = "nc4unilGEKlvZ6pcH612Mt"
DEFAULT_APP_VERSION = "4.1.394"


class FigmaWaveApiError(RuntimeError):
    def __init__(self, status_code: int | None, data: Any, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.data = data


def _strip_outer_quotes(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
        return raw[1:-1]
    return raw


def figmawave_auths() -> list[dict]:
    """Load FigmaWave/Weavy auth from env.

    Preferred env shape:
      FIGMAWAVE_AUTHS_JSON='[{"label":"figmawave-1","bearer_token":"..."}]'
      FIGMAWAVE_AUTHS_JSON='[{"label":"figmawave-1","firebase_api_key":"...","refresh_token":"...","email":"optional"}]'

    Fallback single-auth envs:
      FIGMAWAVE_BEARER_TOKEN, FIGMAWAVE_AUTH_LABEL

    Bearer tokens may include or omit the leading "Bearer ". Firebase refresh
    token auth is preferred because browser bearer tokens expire quickly. The
    optional email field is metadata only, useful for tracking account pools.
    """
    raw = _strip_outer_quotes(os.environ.get("FIGMAWAVE_AUTHS_JSON", ""))
    auths: list[dict] = []
    if raw:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("auths") or parsed.get("accounts") or []
        if not isinstance(parsed, list):
            raise RuntimeError("FIGMAWAVE_AUTHS_JSON must be a JSON list or object with auths/accounts")
        auths = [dict(a) for a in parsed]
    elif os.environ.get("FIGMAWAVE_BEARER_TOKEN"):
        auths.append({
            "label": os.environ.get("FIGMAWAVE_AUTH_LABEL") or "figmawave-1",
            "bearer_token": os.environ["FIGMAWAVE_BEARER_TOKEN"],
        })

    cleaned = []
    for idx, auth in enumerate(auths, start=1):
        auth.setdefault("label", f"figmawave-{idx}")
        if not auth.get("bearer_token") and not (auth.get("firebase_api_key") and auth.get("refresh_token")):
            raise RuntimeError(f"FigmaWave auth {auth.get('label')!r} missing bearer_token or firebase_api_key+refresh_token")
        cleaned.append(auth)
    if not cleaned:
        raise RuntimeError("No FigmaWave auth configured. Set FIGMAWAVE_AUTHS_JSON or FIGMAWAVE_BEARER_TOKEN.")
    return cleaned


def figmawave_auth_by_label(label: str | None) -> dict | None:
    if not label:
        return None
    for auth in figmawave_auths():
        if auth.get("label") == label:
            return auth
    return None


def figmawave_recipe_id(auth: dict | None = None) -> str:
    if auth and auth.get("recipe_id"):
        return str(auth["recipe_id"]).strip()
    return os.environ.get("FIGMAWAVE_RECIPE_ID", DEFAULT_RECIPE_ID).strip() or DEFAULT_RECIPE_ID


def figmawave_refresh_bearer_token(auth: dict) -> str:
    api_key = auth.get("firebase_api_key") or auth.get("api_key")
    refresh_token = auth.get("refresh_token") or auth.get("firebase_refresh_token")
    if not api_key or not refresh_token:
        token = str(auth.get("bearer_token") or "").strip()
        if not token:
            raise RuntimeError(f"FigmaWave auth {auth.get('label')!r} has no bearer or refresh credentials")
        return token

    now = time.time()
    cached = str(auth.get("bearer_token") or "").strip()
    expires_at = float(auth.get("bearer_expires_at") or 0)
    refresh_always = str(os.environ.get("FIGMAWAVE_REFRESH_ALWAYS", "true")).strip().lower() not in {"0", "false", "no"}
    if cached and not refresh_always and expires_at - now > 120:
        return cached

    r = requests.post(
        f"https://securetoken.googleapis.com/v1/token?key={api_key}",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if not (200 <= r.status_code < 300):
        raise FigmaWaveApiError(r.status_code, data, f"Firebase token refresh failed {r.status_code}: {data}")
    id_token = data.get("id_token")
    if not id_token:
        raise RuntimeError(f"Firebase token refresh returned no id_token: {data}")
    auth["bearer_token"] = f"Bearer {id_token}"
    if data.get("refresh_token"):
        auth["refresh_token"] = data["refresh_token"]
    try:
        auth["bearer_expires_at"] = now + int(data.get("expires_in") or 3600)
    except Exception:
        auth["bearer_expires_at"] = now + 3600
    return auth["bearer_token"]


def figmawave_user(auth: dict) -> dict:
    return figmawave_request("GET", "/users", auth, recipe_id=figmawave_recipe_id(auth))


def figmawave_remaining_credits(auth: dict) -> int:
    data = figmawave_user(auth)
    active = data.get("activeWorkspace") or {}
    credits = active.get("credits")
    if credits is None:
        credits = data.get("credits")
    if credits is None:
        credits_list = ((active.get("subscription") or {}).get("creditsList") or [])
        credits = sum(int(item.get("available") or 0) for item in credits_list if isinstance(item, dict))
    return int(credits or 0)


def select_figmawave_auth(preferred_label: str | None = None, min_credits: int | None = None) -> tuple[dict, dict]:
    """Pick a FigmaWave auth with enough credits.

    FigmaWave/Weavy currently costs ~10 credits per Kling motion generation, so
    the default selector skips accounts with fewer than 10 credits.
    """
    min_credits = min_credits if min_credits is not None else int(os.environ.get("FIGMAWAVE_MIN_CREDITS", "10"))
    auths = figmawave_auths()
    if preferred_label:
        preferred = figmawave_auth_by_label(preferred_label)
        if not preferred:
            raise RuntimeError(f"Configured FigmaWave auth not found for label: {preferred_label}")
        auths = [preferred]

    checked = []
    for auth in auths:
        try:
            credits = figmawave_remaining_credits(auth)
            generations = credits // 10
            quota = {"credits": credits, "estimated_generations": generations, "recipe_id": figmawave_recipe_id(auth)}
            checked.append({"label": auth.get("label"), **quota})
            if credits >= min_credits:
                return auth, quota
        except Exception as e:
            checked.append({"label": auth.get("label"), "error": str(e), "recipe_id": figmawave_recipe_id(auth)})
            if preferred_label:
                raise
            continue
    raise RuntimeError(f"No FigmaWave account has >= {min_credits} credits: {checked}")


def figmawave_headers(auth: dict, *, recipe_id: str | None = None, json_body: bool = True) -> dict:
    token = figmawave_refresh_bearer_token(auth).strip()
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "authorization": token,
        "origin": "https://app.weavy.ai",
        "user-agent": os.environ.get(
            "FIGMAWAVE_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        ),
        "x-app-version": os.environ.get("FIGMAWAVE_APP_VERSION", DEFAULT_APP_VERSION),
        "x-weavy-auth-provider": os.environ.get("FIGMAWAVE_AUTH_PROVIDER", "firebase"),
    }
    if recipe_id:
        headers["x-app-recipeid"] = recipe_id
    if json_body:
        headers["content-type"] = "application/json"
    return headers


def figmawave_request(method: str, path: str, auth: dict, *, json_body: dict | None = None, params: dict | None = None, recipe_id: str | None = None, timeout: int = 120) -> dict:
    url = f"{API_BASE}{path}"
    r = requests.request(
        method,
        url,
        headers=figmawave_headers(auth, recipe_id=recipe_id, json_body=json_body is not None),
        json=json_body,
        params=params,
        timeout=timeout,
    )
    text = r.text
    try:
        data = r.json()
    except Exception:
        data = {"raw": text}
    if not (200 <= r.status_code < 300):
        raise FigmaWaveApiError(r.status_code, data, f"FigmaWave {method} {path} failed {r.status_code}: {data}")
    return data


def figmawave_approve_models(auth: dict, model_ids: list[str], recipe_id: str | None = None) -> dict:
    """Approve workspace model access required by Weavy before first execute.

    Weavy returns internalErrorCode 1076 with the blocked model id when a
    workspace has not approved a model yet. Calling this endpoint once for the
    model allows the normal recipe execute request to proceed.
    """
    clean_ids = [str(model_id) for model_id in model_ids if str(model_id or "").strip()]
    if not clean_ids:
        return {"approved": []}
    return figmawave_request(
        "POST",
        "/workspaces/models/approve",
        auth,
        json_body={"modelIds": clean_ids},
        recipe_id=recipe_id or figmawave_recipe_id(auth),
        timeout=60,
    )


def figmawave_get_recipe(auth: dict, recipe_id: str | None = None) -> dict:
    recipe_id = recipe_id or figmawave_recipe_id(auth)
    return figmawave_request("GET", f"/recipes/{recipe_id}", auth, recipe_id=recipe_id)


def figmawave_upload_asset(auth: dict, file_path: str | os.PathLike, asset_type: str | None = None, recipe_id: str | None = None) -> dict:
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"FigmaWave asset upload file not found: {path}")
    guessed, _ = mimetypes.guess_type(str(path))
    mime = asset_type or guessed or "application/octet-stream"
    recipe_id = recipe_id or figmawave_recipe_id(auth)
    headers = figmawave_headers(auth, recipe_id=recipe_id, json_body=False)
    with path.open("rb") as fh:
        r = requests.post(
            f"{API_BASE}/assets/upload",
            headers=headers,
            files={"file": (path.name, fh, mime)},
            data={"type": mime},
            timeout=300,
        )
    text = r.text
    try:
        data = r.json()
    except Exception:
        data = {"raw": text}
    if not (200 <= r.status_code < 300):
        raise FigmaWaveApiError(r.status_code, data, f"FigmaWave upload failed {r.status_code}: {data}")
    if not data.get("url"):
        raise RuntimeError(f"FigmaWave upload returned no URL: {data}")
    return data


def _model_node(recipe: dict) -> dict:
    configured = os.environ.get("FIGMAWAVE_MODEL_NODE_ID", "").strip()
    nodes = recipe.get("nodes") or []
    if configured:
        for node in nodes:
            if node.get("id") == configured:
                return copy.deepcopy(node)
        raise RuntimeError(f"FigmaWave model node not found: {configured}")
    for node in nodes:
        if node.get("isModel") or node.get("type") == "custommodelV2":
            return copy.deepcopy(node)
    raise RuntimeError("FigmaWave recipe has no model node")


def _file_binding(asset: dict, node_id: str, output_id: str = "file") -> dict:
    file_obj = {k: v for k, v in asset.items() if k in {"id", "url", "name", "type", "width", "height", "duration", "fps", "hasAudio", "publicId", "thumbnailUrl", "viewUrl", "originalUrl"} and v is not None}
    return {"nodeId": node_id, "outputId": output_id, "file": file_obj}


def _set_kind_input(kind: dict, input_id: str, binding: dict | None):
    inputs = kind.setdefault("inputs", [])
    for item in inputs:
        if item and item[0].get("id") == input_id:
            item[1] = binding
            return
    inputs.append(([{"id": input_id}, binding]))


def _set_kind_parameter(kind: dict, param_id: str, value: Any, value_type: str):
    parameters = kind.setdefault("parameters", [])
    for item in parameters:
        if item and item[0].get("id") == param_id:
            item[1] = {"type": "value", "data": {"type": value_type, "value": value}}
            return
    parameters.append(([{"id": param_id}, {"type": "value", "data": {"type": value_type, "value": value}}]))


def figmawave_build_execute_payload(recipe: dict, image_asset: dict, video_asset: dict, *, prompt: str = "", mode: str = "pro", character_orientation: str = "video", keep_original_sound: bool = True) -> dict:
    recipe_nodes = recipe.get("nodes") or []
    image_import = next((n for n in recipe_nodes if n.get("id") != _model_node(recipe).get("id") and (n.get("data", {}).get("output", {}).get("file", {}).get("type") == "image" or n.get("data", {}).get("result", {}).get("type") == "image")), {})
    video_import = next((n for n in recipe_nodes if n.get("id") != _model_node(recipe).get("id") and (n.get("data", {}).get("output", {}).get("file", {}).get("type") == "video" or n.get("data", {}).get("result", {}).get("type") == "video")), {})
    image_node_id = image_import.get("id") or "figmawave-image-import"
    video_node_id = video_import.get("id") or "figmawave-video-import"

    node = _model_node(recipe)
    data = node.setdefault("data", {})
    kind = data.setdefault("kind", {})
    image_binding = _file_binding(image_asset, image_node_id)
    video_binding = _file_binding(video_asset, video_node_id)
    _set_kind_input(kind, "prompt", None if not prompt else {"type": "value", "data": {"type": "string", "value": prompt}})
    _set_kind_input(kind, "image", image_binding)
    _set_kind_input(kind, "video", video_binding)
    _set_kind_parameter(kind, "character_orientation", character_orientation, "string")
    _set_kind_parameter(kind, "mode", mode, "string")
    _set_kind_parameter(kind, "keep_original_sound", keep_original_sound, "boolean")
    data["params"] = {
        "prompt": prompt,
        "image": image_asset,
        "video": video_asset,
        "mode": mode,
        "character_orientation": character_orientation,
        "keep_original_sound": keep_original_sound,
    }

    edges = []
    model_id = node.get("id")
    for edge in recipe.get("edges") or []:
        target_handle = str(edge.get("targetHandle") or "")
        if edge.get("target") == model_id and ("input-image" in target_handle or "input-video" in target_handle):
            edge = copy.deepcopy(edge)
            if "input-image" in target_handle:
                edge["source"] = image_node_id
                edge["sourceHandle"] = f"{image_node_id}-output-file"
            if "input-video" in target_handle:
                edge["source"] = video_node_id
                edge["sourceHandle"] = f"{video_node_id}-output-file"
            edges.append(edge)
    return {"numberOfRuns": 1, "nodes": [node], "edges": edges}


def figmawave_execute(auth: dict, image_asset: dict, video_asset: dict, *, recipe_id: str | None = None, prompt: str = "", mode: str = "pro", character_orientation: str = "video", keep_original_sound: bool = True) -> dict:
    recipe_id = recipe_id or figmawave_recipe_id(auth)
    recipe = figmawave_get_recipe(auth, recipe_id)
    payload = figmawave_build_execute_payload(
        recipe,
        image_asset,
        video_asset,
        prompt=prompt,
        mode=mode,
        character_orientation=character_orientation,
        keep_original_sound=keep_original_sound,
    )
    try:
        return figmawave_request("POST", f"/batches/recipes/{recipe_id}/execute", auth, json_body=payload, recipe_id=recipe_id, timeout=180)
    except FigmaWaveApiError as e:
        data = e.data if isinstance(e.data, dict) else {}
        if e.status_code == 403 and data.get("internalErrorCode") == 1076:
            model_ids = []
            for node in payload.get("nodes") or []:
                model_id = (((node.get("data") or {}).get("kind") or {}).get("model") or {}).get("name")
                if model_id:
                    model_ids.append(model_id)
            if not model_ids and data.get("message"):
                model_ids.append(str(data["message"]))
            figmawave_approve_models(auth, model_ids, recipe_id=recipe_id)
            return figmawave_request("POST", f"/batches/recipes/{recipe_id}/execute", auth, json_body=payload, recipe_id=recipe_id, timeout=180)
        raise


def figmawave_batch_status(auth: dict, batch_id: str, recipe_id: str | None = None) -> dict:
    recipe_id = recipe_id or figmawave_recipe_id(auth)
    return figmawave_request("GET", f"/batches/recipes/{recipe_id}/batches/{batch_id}/status", auth, recipe_id=recipe_id)


def figmawave_submit(auth: dict, image_path: str | os.PathLike, video_path: str | os.PathLike, *, prompt: str = "", recipe_id: str | None = None) -> dict:
    recipe_id = recipe_id or figmawave_recipe_id(auth)
    image_asset = figmawave_upload_asset(auth, image_path, recipe_id=recipe_id)
    video_asset = figmawave_upload_asset(auth, video_path, recipe_id=recipe_id)
    started = figmawave_execute(auth, image_asset, video_asset, recipe_id=recipe_id, prompt=prompt)
    batch_id = started.get("batchId")
    if not batch_id:
        raise RuntimeError(f"No batchId from FigmaWave execute: {started}")
    run_id = ""
    runs = started.get("recipeRuns") or []
    if runs:
        run_id = runs[0].get("id") or ""
    return {"batch_id": batch_id, "run_id": run_id, "image_asset": image_asset, "video_asset": video_asset, "started": started}


def _extract_result_url(status: dict) -> str | None:
    for run in status.get("recipeRuns") or []:
        for node_run in run.get("nodeRuns") or []:
            containers = [node_run.get("result"), node_run.get("output")]
            for container in containers:
                if not container:
                    continue
                if isinstance(container, dict):
                    result = container.get("result") or container.get("file") or container
                    if isinstance(result, dict) and result.get("url"):
                        return result["url"]
                    if isinstance(result, list):
                        for item in result:
                            if isinstance(item, dict) and item.get("url"):
                                return item["url"]
                if isinstance(container, list):
                    for item in container:
                        if isinstance(item, dict) and item.get("url"):
                            return item["url"]
        # Some responses put complete output on the run itself.
        for key in ["result", "output"]:
            result = run.get(key)
            if isinstance(result, dict) and result.get("url"):
                return result["url"]
    return None


def figmawave_poll_result(auth: dict, batch_id: str, *, recipe_id: str | None = None, max_wait_seconds: int | None = None, poll_interval_seconds: int | None = None) -> tuple[str, dict]:
    recipe_id = recipe_id or figmawave_recipe_id(auth)
    max_wait = max_wait_seconds if max_wait_seconds is not None else int(os.environ.get("FIGMAWAVE_MAX_WAIT_SECONDS", "3600"))
    interval = poll_interval_seconds if poll_interval_seconds is not None else int(os.environ.get("FIGMAWAVE_POLL_INTERVAL_SECONDS", "45"))
    started_at = time.time()
    last_status: dict = {}
    while True:
        if time.time() - started_at > max_wait:
            raise TimeoutError(f"FigmaWave batch timed out: {batch_id}")
        time.sleep(min(interval, max(1, max_wait - int(time.time() - started_at))))
        last_status = figmawave_batch_status(auth, batch_id, recipe_id)
        url = _extract_result_url(last_status)
        if url:
            return url, last_status
        run_statuses = {str(r.get("status") or "").upper() for r in last_status.get("recipeRuns") or []}
        node_statuses = {str(n.get("status") or "").upper() for r in last_status.get("recipeRuns") or [] for n in (r.get("nodeRuns") or [])}
        failed = {"FAILED", "ERROR", "CANCELED", "CANCELLED"}
        if run_statuses & failed or node_statuses & failed:
            raise RuntimeError(f"FigmaWave batch failed: {last_status}")


def max_figmawave_wait_seconds() -> int:
    return int(os.environ.get("FIGMAWAVE_MAX_WAIT_SECONDS", "3600"))


def figmawave_poll_interval_seconds() -> int:
    return int(os.environ.get("FIGMAWAVE_POLL_INTERVAL_SECONDS", "45"))
