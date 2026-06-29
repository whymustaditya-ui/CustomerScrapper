"""Search-query templates per industry bucket.

Each bucket holds a list of synonym search terms to widen the net on GMaps.
The industry label attached to a lead is the bucket whose synonym matched the
query, refined later by GMaps' own category text where available.
"""

CATEGORIES = {
    "Catering": ["catering", "katering", "nasi box", "nasi kotak", "catering pernikahan"],
    "Horeca": ["hotel", "cafe", "kafe", "restoran", "kedai", "bistro"],
    "Resto": ["restoran", "rumah makan", "warung makan", "depot makan"],
    "Cloud Kitchen": ["cloud kitchen", "dapur bersama", "ghost kitchen"],
    "Frozen Food": ["frozen food", "makanan beku", "frozen food rumahan"],
    "Bakery": ["bakery", "toko kue", "kue", "pastry", "roti"],
    "Warung Makan": ["warung makan", "warteg", "warung nasi"],
}

# Default industry label fallback when nothing else resolves.
DEFAULT_INDUSTRY = "F&B"


def buckets():
    """Return the list of selectable industry buckets for the UI."""
    return list(CATEGORIES.keys())


def synonyms(bucket: str):
    """Return synonym search terms for a bucket, or [bucket] if unknown."""
    return CATEGORIES.get(bucket, [bucket])


def industry_for_term(term: str) -> str:
    """Map a matched search term back to its industry bucket label."""
    t = (term or "").lower().strip()
    for bucket, syns in CATEGORIES.items():
        if any(t == s.lower() for s in syns):
            return bucket
    return DEFAULT_INDUSTRY
