import csv
import hashlib
import re
import sys

from .config import RUNS_CSV
from .state import state_load, state_save
from .locking import state_lock
from .storage import upsert_local_csv


CAPTION_STOPWORDS = {
    "ready", "stock", "import", "murah", "premium", "terbaru", "kekinian", "style", "gaya",
    "wanita", "cewek", "perempuan", "baju", "atasan", "outfit", "fashion", "casual", "korea", "korean",
    "by", "dan", "dengan", "untuk", "ukuran", "motif", "variasi", "model", "the", "a", "an",
    "ini", "baru", "best", "seller", "ld", "all", "size", "jumbo", "simple", "basic",
}


CAPTION_FIXED_TAGS = [
    "#fyp",
    "#muslimah",
    "#outfitideas",
    "#ootdhijab",
    "#outfittiktok",
]

DETAIL_WORDS = [
    "ruffle", "salur", "bordir", "plisket", "pleats", "pita", "ribbon", "rajut", "knit",
    "denim", "vneck", "v-neck", "kancing", "lace", "renda", "kerah", "balon", "flare",
    "stripe", "stripes", "polos", "linen", "rayon", "katun", "satin", "rib", "smock",
]

CATEGORY_WORDS = [
    "kemeja", "blouse", "blus", "cardigan", "cardi", "outer", "vest", "rompi", "kulot",
    "celana", "rok", "dress", "gamis", "set", "oneset", "sweater", "top", "tunik",
]

COLOR_WORDS = {
    "black": "hitam", "hitam": "hitam", "white": "putih", "putih": "putih", "ivory": "ivory",
    "cream": "cream", "krem": "cream", "beige": "beige", "coklat": "coklat", "brown": "coklat",
    "grey": "grey", "gray": "grey", "abu": "abu", "navy": "navy", "blue": "biru", "biru": "biru",
    "green": "hijau", "hijau": "hijau", "sage": "sage", "olive": "olive", "pink": "pink",
    "maroon": "maroon", "red": "merah", "merah": "merah", "yellow": "kuning", "kuning": "kuning",
    "taupe": "taupe", "milo": "milo", "khaki": "khaki",
}

OCCASIONS = ["ngantor", "daily", "hangout", "kuliah", "jalan", "layering", "foto mirror"]
MOODS = ["rapi", "kalem", "effortless", "soft", "clean", "flowy", "ringan", "jatuhnya enak"]


def clean_product_title(title: str) -> str:
    title = re.sub(r"\[[^\]]+\]", " ", title or "")
    title = re.sub(r"\([^)]*\)", " ", title)
    title = re.sub(r"[^\w\s\-/&]", " ", title, flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _words(title: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9]+", clean_product_title(title).lower())


def caption_keywords(title: str, limit: int = 2) -> list[str]:
    words = _words(title)
    picked = []
    for word in words:
        if len(word) < 4 or word in CAPTION_STOPWORDS:
            continue
        if word not in picked:
            picked.append(word)
        if len(picked) >= limit:
            break
    return picked


def _stable_pick(title: str, options: list[str], salt: str = "") -> str:
    if not options:
        return ""
    digest = hashlib.sha1(f"{title}|{salt}".encode("utf-8", "ignore")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def caption_parts(title: str) -> dict:
    words = _words(title)
    lower = " ".join(words)
    details = []
    categories = []
    colors = []
    for word in words:
        base = re.sub(r"nya$", "", word)
        if word in COLOR_WORDS and COLOR_WORDS[word] not in colors:
            colors.append(COLOR_WORDS[word])
        if base in COLOR_WORDS and COLOR_WORDS[base] not in colors:
            colors.append(COLOR_WORDS[base])
        if base in CATEGORY_WORDS and base not in categories:
            categories.append("cardi" if base == "cardigan" else base)
        if base in DETAIL_WORDS and base not in details:
            details.append("vneck" if base == "v-neck" else base)
    if "cutbray" in lower and "cutbray" not in details:
        details.append("cutbray")
    return {"details": details, "categories": categories, "colors": colors, "keywords": caption_keywords(title, limit=3)}


def caption_tags(title: str) -> list[str]:
    return CAPTION_FIXED_TAGS.copy()


def build_caption_phrase(product_title: str) -> str:
    title = clean_product_title(product_title)
    if not title:
        return _stable_pick(title, [
            "look daily anti ribet",
            "buat ootd santai",
            "potongannya gampang dipake",
            "vibes nya soft daily",
        ], "empty")

    parts = caption_parts(title)
    detail = parts["details"][0] if parts["details"] else ""
    category = parts["categories"][0] if parts["categories"] else ""
    color = parts["colors"][0] if parts["colors"] else ""
    keyword = parts["keywords"][0] if parts["keywords"] else ""
    occasion = _stable_pick(title, OCCASIONS, "occasion")
    mood = _stable_pick(title, MOODS, "mood")

    primary = []
    secondary = []
    if detail and category:
        primary.extend([
            f"{detail} {category} buat {occasion}",
            f"detail {detail} nya {mood}",
            f"{category} {detail} keliatan {mood}",
            f"aksen {detail} nya hidup",
        ])
    if color and category:
        primary.extend([
            f"{category} {color} buat {occasion}",
            f"tone {color} keliatan {mood}",
        ])
    if detail:
        primary.extend([
            f"detail {detail} nya niat",
            f"{detail} nya keliatan rapi",
            f"aksen {detail} bikin beda",
            f"{detail} nya bukan tempelan",
        ])
    if category:
        secondary.extend([
            f"{category} buat {occasion}",
            f"{category} nya {mood}",
            f"potongan {category} nya enak",
            f"{category} gampang di mix",
        ])
    if keyword:
        secondary.extend([
            f"{keyword} buat look daily",
            f"{keyword} keliatan wearable",
        ])
    fallback = [
        "look daily anti ribet",
        "buat ootd santai",
        "potongannya gampang dipake",
        "look nya kalem daily",
    ]

    candidates = primary or secondary or fallback
    phrase = _stable_pick(title, candidates, "phrase")
    phrase = re.sub(r"\bini\b", "", phrase).strip()
    phrase = re.sub(r"\b([a-z0-9]+) nya\b", r"\1nya", phrase)
    phrase = re.sub(r"\s+", " ", phrase)
    words = phrase.split()
    return " ".join(words[:5]).lower()


def build_tiktok_caption(product_title: str) -> str:
    phrase = build_caption_phrase(product_title)
    tags = caption_tags(product_title)
    return f"{phrase} {' '.join(tags)}".strip()


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
    row = None
    with state_lock():
        state = state_load()
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
