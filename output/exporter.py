"""Export leads to xlsx (multi-sheet) and Qontak-import-friendly csv.

Files land in data/output/ with timestamped names per ROSH convention:
    YYYY-MM-DD_LEADS_{area}-{category}.xlsx
"""

from __future__ import annotations

import os
import re
from datetime import date

import pandas as pd

OUTPUT_DIR = os.path.join("data", "output")

# Display/export column order. Qontak-import-friendly fields come first in the CSV.
LEAD_COLUMNS = [
    "name", "industry", "gmaps_category", "store_size", "store_size_score",
    "phone_normalized", "phone_landline", "website_canonical", "kelurahan", "kota",
    "address", "rating", "review_count", "price_level", "photo_count",
    "parse_confidence", "matched_term", "query_area", "place_id", "place_url",
    "skipped_fields",
]

CSV_COLUMNS = [
    "name", "phone_normalized", "industry", "store_size", "store_size_score",
    "kelurahan", "kota", "address", "website_canonical",
]


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "").strip())
    return s.strip("-") or "all"


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def export(
    leads: list[dict],
    excluded: list[dict],
    summary: dict,
    area: str,
    category: str,
    output_dir: str = OUTPUT_DIR,
) -> dict:
    """Write xlsx + csv. Returns {xlsx_path, csv_path}."""
    os.makedirs(output_dir, exist_ok=True)
    stamp = date.today().isoformat()
    base = f"{stamp}_LEADS_{_slug(area)}-{_slug(category)}"
    xlsx_path = os.path.join(output_dir, base + ".xlsx")
    csv_path = os.path.join(output_dir, base + ".csv")

    leads_df = _frame(leads, LEAD_COLUMNS).sort_values(
        "store_size_score", ascending=False, na_position="last"
    )
    excluded_cols = LEAD_COLUMNS + ["exclude_reason"]
    excluded_df = _frame(excluded, excluded_cols) if excluded else pd.DataFrame(columns=excluded_cols)
    summary_df = pd.DataFrame([summary])

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        leads_df.to_excel(writer, sheet_name="Leads", index=False)
        excluded_df.to_excel(writer, sheet_name="Excluded-Dedup", index=False)
        summary_df.to_excel(writer, sheet_name="Run summary", index=False)

    _frame(leads, CSV_COLUMNS).to_csv(csv_path, index=False)

    return {"xlsx_path": xlsx_path, "csv_path": csv_path}
