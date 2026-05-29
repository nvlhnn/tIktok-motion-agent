import csv
import re
import sys

from .config import RUNS_CSV
from .state import state_load, state_save
from .storage import upsert_local_csv


CAPTION_STOPWORDS = {
    "ready", "stock", "import", "murah", "premium", "terbaru", "kekinian", "style", "gaya",
    "wanita", "cewek", "perempuan", "baju", "atasan", "outfit", "fashion", "casual", "korea", "korean",
    "by", "dan", "dengan", "untuk", "ukuran", "motif", "variasi", "model", "the", "a", "an",
}


CAPTION_TAG_MAP = {
    "kemeja": ["#kemejawanita", "#atasanwanita", "#fashionwanita"],
    "blouse": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
    "blus": ["#blousewanita", "#atasanwanita", "#fashionwanita"],
    "sweater": ["#sweaterwanita", "#atasanwanita", "#outfitkekinian"],
    "rajut": ["#sweaterwanita", "#atasanwanita", "#ootd"],
    "knit": ["#sweaterwanita", "#atasanwanita", "#outfitinspiration"],
    "cardigan": ["#cardiganwanita", "#atasanwanita", "#outfitkekinian"],
    "kardigan": ["#cardiganwanita", "#atasanwanita", "#outfitkekinian"],
    "outer": ["#outerwanita", "#atasanwanita", "#ootd"],
    "vest": ["#vestwanita", "#atasanwanita", "#outfitinspiration"],
    "rompi": ["#rompiwanita", "#atasanwanita", "#ootd"],
    "kaos": ["#atasanwanita", "#fashionwanita", "#ootd"],
    "denim": ["#kemejawanita", "#atasanwanita", "#ootd"],
    "jeans": ["#kemejawanita", "#atasanwanita", "#ootd"],
    "crop": ["#atasanwanita", "#outfitkekinian"],
    "babydoll": ["#blousewanita", "#atasanwanita", "#ootd"],
    "bordir": ["#blousewanita", "#kemejawanita", "#atasanwanita"],
    "pita": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
    "ribbon": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
    "peplum": ["#blousewanita", "#atasanwanita", "#outfitkekinian"],
    "coquette": ["#blousewanita", "#atasanwanita", "#outfitinspiration"],
}

CAPTION_BASE_TAGS = [
    "#atasanwanita",
    "#blouse",
    "#kemejawanita",
    "#blousewanita",
    "#outfitinspiration",
    "#fashionwanita",
    "#outfitkekinian",
    "#ootd",
]


def clean_product_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]", " ", title or "")
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(r"[^\w\s\-/&]", " ", title, flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def caption_keywords(title: str, limit: int = 2) -> list[str]:
    title = clean_product_title(title)
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", title.lower())
    picked = []
    for word in words:
        if len(word) < 4 or word in CAPTION_STOPWORDS or word == "ini":
            continue
        if word not in picked:
            picked.append(word)
        if len(picked) >= limit:
            break
    return picked


def caption_tags(title: str) -> list[str]:
    lower = clean_product_title(title).lower()
    tags = []
    for key, mapped in CAPTION_TAG_MAP.items():
        if key in lower:
            tags.extend(mapped)
    # Competitor pattern: repetitive, broad modest-fashion discovery tags beat clever/random tags.
    tags.extend(CAPTION_BASE_TAGS)
    deduped = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return deduped[:6]


def build_tiktok_caption(product_title: str) -> str:
    title = clean_product_title(product_title)
    lower = title.lower()
    kws = caption_keywords(title)
    if "bordir" in lower:
        text = "bordirnya manis bgt"
    elif "denim" in lower or "jeans" in lower:
        text = "denim gini cakep"
    elif "rajut" in lower or "knit" in lower:
        text = "rajutnya cakep bgt"
    elif "pita" in lower or "ribbon" in lower:
        text = "pitanya gemes bgt"
    elif "outer" in lower or "cardigan" in lower or "kardigan" in lower:
        text = "outer kepake terus"
    elif "kemeja" in lower:
        text = "kemejanya clean bgt"
    elif "blouse" in lower or "blus" in lower:
        text = "blouse simple cakep"
    elif kws:
        text = " ".join(kws[:2] + ["cakep"])
    else:
        text = "simple tapi cakep"
    return f"{text.lower()} {' '.join(caption_tags(title))}".strip()


def caption_for_job(job_id: str | None = None, title: str | None = None) -> dict:
    if title:
        return {"product_title": title, "caption": build_tiktok_caption(title)}
    if not job_id:
        raise RuntimeError("caption needs either job_id or --title")
    state = state_load()
    row = None
    if job_id in (state.get("prepared_jobs") or {}):
        row = (state["prepared_jobs"][job_id] or {}).get("row")
    if row is None and RUNS_CSV.exists():
        with RUNS_CSV.open("r", newline="") as f:
            for existing in csv.DictReader(f):
                if existing.get("job_id") == job_id:
                    row = existing
    if not row:
        raise RuntimeError(f"Job not found: {job_id}")
    caption = build_tiktok_caption(row.get("product_title", ""))
    row = dict(row)
    row["caption"] = caption
    upsert_local_csv(row)
    try:
        from .sheets import upsert_sheet
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet caption update skipped: {e}", file=sys.stderr)
    return {"job_id": job_id, "product_title": row.get("product_title", ""), "caption": caption}


def set_caption_for_job(job_id: str, caption: str) -> dict:
    caption = (caption or "").strip()
    if not caption:
        raise RuntimeError("Caption cannot be empty")
    state = state_load()
    row = None
    if job_id in (state.get("prepared_jobs") or {}):
        info = state["prepared_jobs"][job_id]
        row = dict((info or {}).get("row") or {})
        row["caption"] = caption
        info["row"] = row
        state["prepared_jobs"][job_id] = info
        state_save(state)
    if row is None and RUNS_CSV.exists():
        with RUNS_CSV.open("r", newline="") as f:
            for existing in csv.DictReader(f):
                if existing.get("job_id") == job_id:
                    row = dict(existing)
                    row["caption"] = caption
                    break
    if row is None:
        raise RuntimeError(f"Job not found: {job_id}")
    upsert_local_csv(row)
    try:
        from .sheets import upsert_sheet
        upsert_sheet(row)
    except Exception as e:
        print(f"sheet caption update skipped: {e}", file=sys.stderr)
    return {"job_id": job_id, "product_title": row.get("product_title", ""), "caption": caption}
