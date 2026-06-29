"""Address parsing + field normalization for Indonesian GMaps listings.

Indonesian addresses typically look like:
    Jl. Kemang Raya No.5, RT.1/RW.2, Bangka, Mampang Prapatan,
    Kota Jakarta Selatan, DKI Jakarta 12730

Strategy: split on commas, find the kecamatan token by matching against the
known kecamatan reference (areas.KECAMATAN_TO_KOTA). Kelurahan is the token
immediately before the kecamatan; kota is resolved from the kecamatan (and
cross-checked against any "Kota/Kab." token). Low-confidence parses are flagged.
"""

from __future__ import annotations

import re

from scraper.areas import KECAMATAN_TO_KOTA

_KOTA_RE = re.compile(r"\b(kota|kabupaten|kab\.?)\s+([a-z ]+)", re.IGNORECASE)
_POSTCODE_RE = re.compile(r"\b\d{5}\b")
_RTRW_RE = re.compile(r"\brt\.?\s*\d+", re.IGNORECASE)


def _clean_token(t: str) -> str:
    return t.strip(" .,").strip()


def parse_address(address: str) -> dict:
    """Return {kelurahan, kota, parse_confidence} for one address string."""
    if not address:
        return {"kelurahan": "", "kota": "", "parse_confidence": "none"}

    raw_tokens = [_clean_token(t) for t in address.split(",")]
    tokens = [t for t in raw_tokens if t]

    kecamatan_idx = None
    matched_kota = ""
    for idx, tok in enumerate(tokens):
        key = tok.lower()
        if key in KECAMATAN_TO_KOTA:
            kecamatan_idx = idx
            matched_kota = KECAMATAN_TO_KOTA[key]
            break

    kelurahan = ""
    kota = ""
    confidence = "low"

    if kecamatan_idx is not None:
        # Kelurahan is usually the token before kecamatan, but skip RT/RW noise.
        for back in range(kecamatan_idx - 1, -1, -1):
            cand = tokens[back]
            if _RTRW_RE.search(cand) or cand.lower().startswith("jl"):
                continue
            kelurahan = cand
            break
        kota = matched_kota
        confidence = "high"
    else:
        # Fallback: regex for explicit "Kota/Kab. X".
        m = _KOTA_RE.search(address)
        if m:
            kota = "Kota " + _clean_token(m.group(2)).title()
            confidence = "medium"

    # Strip postcode that sometimes rides along on the kota token.
    kota = _POSTCODE_RE.sub("", kota).strip()
    kelurahan = _POSTCODE_RE.sub("", kelurahan).strip()

    return {
        "kelurahan": kelurahan.title() if kelurahan else "",
        "kota": kota,
        "parse_confidence": confidence,
    }


def normalize_phone(phone: str) -> str:
    """Standardize an Indonesian phone to +62XXXXXXXXXX (digits only after +62)."""
    if not phone:
        return ""
    digits = re.sub(r"[^\d+]", "", phone)
    digits = digits.replace("+", "")
    if digits.startswith("62"):
        core = digits
    elif digits.startswith("0"):
        core = "62" + digits[1:]
    elif digits.startswith("8"):
        core = "62" + digits
    else:
        core = digits
    return "+" + core if core else ""


def canonical_website(url: str) -> str:
    """Strip tracking params and trailing slashes; lowercase host."""
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    url = url.split("?")[0].split("#")[0]
    url = url.rstrip("/")
    return url.lower()


def normalize_name(name: str) -> str:
    """Trim and collapse whitespace; keep original casing for display."""
    return re.sub(r"\s+", " ", (name or "").strip())


def enrich_row(row: dict) -> dict:
    """Apply all normalizations to a scraped place dict, returning a new dict."""
    out = dict(row)
    out["name"] = normalize_name(row.get("name", ""))
    parsed = parse_address(row.get("address", ""))
    out.update(parsed)
    out["phone_normalized"] = normalize_phone(row.get("phone", ""))
    out["website_canonical"] = canonical_website(row.get("website", ""))
    out["has_website"] = bool(out["website_canonical"])
    return out
