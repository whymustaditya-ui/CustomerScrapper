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


def _is_icon_glyph(codepoint: int) -> bool:
    """Private Use Area — where icon fonts (e.g. Google Maps' Material Icons)
    live. These have no glyph in normal fonts, so Sheets/Excel show tofu (▯)."""
    return (
        0xE000 <= codepoint <= 0xF8FF        # BMP PUA (Maps' "place" pin = U+E0C8)
        or 0xF0000 <= codepoint <= 0xFFFFD   # Supplementary PUA-A
        or 0x100000 <= codepoint <= 0x10FFFD  # Supplementary PUA-B
    )


def sanitize_text(value: str) -> str:
    """Drop icon-font/PUA glyphs leaked from scraped DOM, collapse whitespace."""
    if not value:
        return ""
    stripped = "".join(ch for ch in value if not _is_icon_glyph(ord(ch)))
    return re.sub(r"\s+", " ", stripped).strip()


_KOTA_RE = re.compile(r"\b(kota|kabupaten|kab\.?)\s+([a-z ]+)", re.IGNORECASE)
_POSTCODE_RE = re.compile(r"\b\d{5}\b")
_RTRW_RE = re.compile(r"\brt\.?\s*\d+", re.IGNORECASE)
# An abbreviated/spelled-out kecamatan marker, e.g. "Kec. Kby. Baru", "Kecamatan X".
_KEC_RE = re.compile(r"^(kec\.?|kecamatan)\b", re.IGNORECASE)
# Tokens that are clearly street/building lines, never a kelurahan.
_NOT_KELURAHAN = ("jl", "jalan", "gg", "gang", "gedung", "blok", "komplek",
                  "kompleks", "ruko", "lantai", "no.", "no ")


def _clean_token(t: str) -> str:
    return t.strip(" .,").strip()


def _normalize_kota(kota: str) -> str:
    """Drop a leading 'Kota '/'Kabupaten ' so values match the areas reference."""
    k = re.sub(r"^(kota|kabupaten|kab\.?)\s+", "", kota, flags=re.IGNORECASE).strip()
    return k.title() if k else ""


def parse_address(address: str) -> dict:
    """Return {kelurahan, kota, parse_confidence} for one address string."""
    if not address:
        return {"kelurahan": "", "kota": "", "parse_confidence": "none"}

    raw_tokens = [_clean_token(t) for t in address.split(",")]
    tokens = [t for t in raw_tokens if t]

    kecamatan_idx = None
    matched_kota = ""
    # Pass 1: an exact kecamatan name from the reference (e.g. "Kebayoran Baru").
    for idx, tok in enumerate(tokens):
        key = tok.lower()
        if key in KECAMATAN_TO_KOTA:
            kecamatan_idx = idx
            matched_kota = KECAMATAN_TO_KOTA[key]
            break
    # Pass 2: an abbreviated marker like "Kec. Kby. Baru" — very common on GMaps,
    # and the reason the first pilot only parsed 2/6 kelurahan.
    if kecamatan_idx is None:
        for idx, tok in enumerate(tokens):
            if _KEC_RE.match(tok):
                kecamatan_idx = idx
                break

    kelurahan = ""
    kota = ""
    confidence = "low"

    if kecamatan_idx is not None:
        # Kelurahan is the nearest token before kecamatan that isn't street/RT-RW noise.
        for back in range(kecamatan_idx - 1, -1, -1):
            cand = tokens[back]
            low = cand.lower()
            if _RTRW_RE.search(cand) or low.startswith(_NOT_KELURAHAN):
                continue
            kelurahan = cand
            break
        if matched_kota:
            kota = matched_kota
            confidence = "high"
        else:
            # Kecamatan found by marker but not in reference — resolve kota from the
            # explicit "Kota/Kab. X" token if present.
            m = _KOTA_RE.search(address)
            kota = _normalize_kota(m.group(0)) if m else ""
            confidence = "high" if kelurahan else "medium"
    else:
        # Fallback: regex for explicit "Kota/Kab. X".
        m = _KOTA_RE.search(address)
        if m:
            kota = _normalize_kota(m.group(0))
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


def is_mobile_number(normalized: str) -> bool:
    """True if a +62-normalized number is an Indonesian mobile (WA-capable).

    Mobile numbers always start with 8 after the country code (+628…). Landlines
    use area codes instead (Jakarta 021 → +6221…, Bandung 022 → +6222…, etc.) and
    have no WhatsApp, so we treat only +628… as a usable WA contact.
    """
    return normalized.startswith("+628")


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
    # Scrub icon-font/PUA glyphs (e.g. Maps' pin U+E0C8) from every free-text
    # field before anything downstream parses, dedups, or writes it to a Sheet.
    for field in ("name", "address", "industry", "gmaps_category", "store_size"):
        if field in out:
            out[field] = sanitize_text(out.get(field, ""))
    out["name"] = normalize_name(out.get("name", ""))
    parsed = parse_address(out.get("address", ""))
    out.update(parsed)
    # WA-only: keep mobile (+628…) as the contact number; park landlines (021, etc.)
    # in phone_landline so they're auditable but never used for WA outreach.
    _phone = normalize_phone(row.get("phone", ""))
    if is_mobile_number(_phone):
        out["phone_normalized"] = _phone
        out["phone_landline"] = ""
    else:
        out["phone_normalized"] = ""
        out["phone_landline"] = _phone
    out["website_canonical"] = canonical_website(row.get("website", ""))
    out["has_website"] = bool(out["website_canonical"])
    return out
