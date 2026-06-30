"""Store-size estimate from GMaps proxies.

There's no public headcount/revenue, so we approximate packaging-volume potential
from observable signals. IMPORTANT CAVEAT (flagged in the build review): review_count
proxies *consumer popularity*, not B2B packaging throughput — a viral small cafe can
outscore a high-volume catering kitchen. Treat the score as a rough sort, not truth,
and tune WEIGHTS after seeing pilot output.

All weights live in one dict so Bro can adjust without touching logic.
"""

from __future__ import annotations

import math
import re

# Tunable. Each component contributes 0..1, multiplied by its weight; the
# weighted sum is rescaled to 0..100. Adjust freely after the pilot.
WEIGHTS = {
    "review_count": 0.40,   # primary volume proxy (log-scaled)
    "price_level": 0.20,    # $$$ tends to mean bigger ticket / more covers
    "rating": 0.10,         # mild quality signal
    "photo_count": 0.10,    # establishment richness proxy
    "has_website": 0.10,    # operational maturity
    "multi_branch": 0.10,   # same name across listings = chain/bigger op
}

# Bucket cutoffs on the 0..100 score.
BUCKET_BESAR = 66
BUCKET_SEDANG = 33


def _review_component(review_count) -> float:
    if not review_count or review_count <= 0:
        return 0.0
    # log scale: ~1000 reviews saturates to 1.0.
    return min(1.0, math.log10(review_count + 1) / 3.0)


_RP_NUM_RE = re.compile(r"(\d[\d.,]*)\s*(rb|ribu|k|jt|juta)?", re.IGNORECASE)


def _max_rupiah(text: str) -> int:
    """Largest rupiah amount in a price string, normalizing rb/ribu/k/jt/juta units."""
    best = 0
    for m in _RP_NUM_RE.finditer(text):
        raw = m.group(1).replace(".", "").replace(",", "")
        if not raw.isdigit():
            continue
        val = int(raw)
        unit = (m.group(2) or "").lower()
        if unit in ("rb", "ribu", "k"):
            val *= 1_000
        elif unit in ("jt", "juta"):
            val *= 1_000_000
        best = max(best, val)
    return best


def _price_component(price_level: str) -> float:
    """Price tier signal, 0..1. Handles both "$" symbols and Indonesian "Rp" ranges.

    GMaps in Indonesia usually shows a rupiah range (e.g. "Rp 25.000, 50.000" or
    "Rp 50, 100 rb") instead of "$$", so counting "$" alone scored most local
    listings at 0. We parse the rupiah upper bound and bucket it into tiers.
    """
    if not price_level:
        return 0.0
    dollars = price_level.count("$")
    if dollars:
        return min(1.0, dollars / 4.0)
    rupiah = _max_rupiah(price_level)
    if rupiah <= 0:
        return 0.0
    if rupiah < 25_000:
        return 0.25
    if rupiah < 50_000:
        return 0.50
    if rupiah < 100_000:
        return 0.75
    return 1.0


def _rating_component(rating) -> float:
    if not rating:
        return 0.0
    return max(0.0, min(1.0, (float(rating) - 3.0) / 2.0))  # 3.0->0, 5.0->1


def _photo_component(photo_count) -> float:
    if not photo_count or photo_count <= 0:
        return 0.0
    return min(1.0, math.log10(photo_count + 1) / 2.5)  # ~300 photos saturates


def score_row(row: dict, branch_count: int = 1) -> dict:
    """Return {store_size_score (0-100), store_size (Kecil/Sedang/Besar)}."""
    components = {
        "review_count": _review_component(row.get("review_count")),
        "price_level": _price_component(row.get("price_level", "")),
        "rating": _rating_component(row.get("rating")),
        "photo_count": _photo_component(row.get("photo_count")),
        "has_website": 1.0 if row.get("has_website") else 0.0,
        "multi_branch": min(1.0, (branch_count - 1) / 3.0),  # 4+ branches saturates
    }
    weighted = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)
    total_weight = sum(WEIGHTS.values()) or 1.0
    score = round(100 * weighted / total_weight, 1)

    if score >= BUCKET_BESAR:
        bucket = "Besar"
    elif score >= BUCKET_SEDANG:
        bucket = "Sedang"
    else:
        bucket = "Kecil"

    return {"store_size_score": score, "store_size": bucket}
