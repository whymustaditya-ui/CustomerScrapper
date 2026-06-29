"""Deduplication: internal, against an existing-customer list, and the master ledger.

Three layers, in order:
  1. internal_dedup    — collapse duplicate listings within this run.
  2. dedup_customers   — exclude rows matching an uploaded existing-customer file.
  3. ledger filtering  — exclude rows already seen in prior runs (idempotent weekly).

Excluded rows are returned/flagged separately, never silently dropped, so Bro
can audit what was removed and why.
"""

from __future__ import annotations

import os
from difflib import SequenceMatcher

import pandas as pd

from enrich.parser import normalize_phone, normalize_name

LEDGER_PATH = os.path.join("data", "ledger.csv")
_FUZZY_NAME_THRESHOLD = 0.88


def _name_key(name: str) -> str:
    return normalize_name(name).lower()


def _fuzzy_equal(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _FUZZY_NAME_THRESHOLD


def _row_identity(row: dict) -> tuple[str, str]:
    """A stable identity key: (normalized phone, name+kelurahan)."""
    phone = row.get("phone_normalized") or normalize_phone(row.get("phone", ""))
    name_kel = f"{_name_key(row.get('name', ''))}|{(row.get('kelurahan') or '').lower()}"
    return phone, name_kel


def internal_dedup(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Collapse duplicates within the run. Returns (unique_rows, dropped_rows)."""
    seen_phones: set[str] = set()
    seen_namekel: set[str] = set()
    unique, dropped = [], []
    for row in rows:
        phone, name_kel = _row_identity(row)
        is_dup = False
        if phone and phone in seen_phones:
            is_dup = True
        elif name_kel and name_kel in seen_namekel:
            is_dup = True
        if is_dup:
            r = dict(row)
            r["exclude_reason"] = "internal duplicate"
            dropped.append(r)
        else:
            if phone:
                seen_phones.add(phone)
            if name_kel:
                seen_namekel.add(name_kel)
            unique.append(row)
    return unique, dropped


def _load_customer_keys(df: pd.DataFrame) -> tuple[set[str], list[str]]:
    """Extract normalized phones + names from an existing-customer dataframe.

    Tolerant to column naming: looks for any column containing 'phone'/'telp'/'wa'
    and any containing 'name'/'nama'.
    """
    phone_cols = [c for c in df.columns if any(k in c.lower() for k in ("phone", "telp", "wa", "hp"))]
    name_cols = [c for c in df.columns if any(k in c.lower() for k in ("name", "nama"))]

    phones: set[str] = set()
    names: list[str] = []
    for _, r in df.iterrows():
        for pc in phone_cols:
            p = normalize_phone(str(r.get(pc, "")))
            if p:
                phones.add(p)
        for nc in name_cols:
            n = _name_key(str(r.get(nc, "")))
            if n and n != "nan":
                names.append(n)
    return phones, names


def dedup_customers(
    rows: list[dict], customer_df: pd.DataFrame | None
) -> tuple[list[dict], list[dict]]:
    """Exclude rows matching the existing-customer list. Returns (kept, excluded)."""
    if customer_df is None or customer_df.empty:
        return rows, []

    cust_phones, cust_names = _load_customer_keys(customer_df)
    kept, excluded = [], []
    for row in rows:
        phone = row.get("phone_normalized") or normalize_phone(row.get("phone", ""))
        name = _name_key(row.get("name", ""))
        match = False
        reason = ""
        if phone and phone in cust_phones:
            match, reason = True, "existing customer (phone match)"
        elif name and any(_fuzzy_equal(name, cn) for cn in cust_names):
            match, reason = True, "existing customer (fuzzy name match)"
        if match:
            r = dict(row)
            r["exclude_reason"] = reason
            excluded.append(r)
        else:
            kept.append(row)
    return kept, excluded


def filter_against_ledger(
    rows: list[dict], ledger_path: str = LEDGER_PATH
) -> tuple[list[dict], list[dict]]:
    """Exclude rows already present in the master ledger from previous runs.

    Returns (new_rows, already_seen_rows). The ledger makes weekly re-runs only
    surface genuinely new leads.
    """
    if not os.path.exists(ledger_path):
        return rows, []
    try:
        ledger = pd.read_csv(ledger_path, dtype=str).fillna("")
    except Exception:
        return rows, []

    seen_phones = set(ledger.get("phone_normalized", pd.Series(dtype=str)).tolist())
    seen_keys = set(ledger.get("identity_key", pd.Series(dtype=str)).tolist())

    new_rows, seen_rows = [], []
    for row in rows:
        phone, name_kel = _row_identity(row)
        if (phone and phone in seen_phones) or (name_kel and name_kel in seen_keys):
            r = dict(row)
            r["exclude_reason"] = "already in ledger (seen in a prior run)"
            seen_rows.append(r)
        else:
            new_rows.append(row)
    return new_rows, seen_rows


def append_to_ledger(rows: list[dict], ledger_path: str = LEDGER_PATH) -> None:
    """Append accepted leads to the master ledger for future idempotent runs."""
    if not rows:
        return
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    records = []
    for row in rows:
        phone, name_kel = _row_identity(row)
        records.append({
            "name": row.get("name", ""),
            "phone_normalized": phone,
            "identity_key": name_kel,
            "kota": row.get("kota", ""),
            "industry": row.get("industry", ""),
        })
    df_new = pd.DataFrame(records)
    if os.path.exists(ledger_path):
        df_old = pd.read_csv(ledger_path, dtype=str).fillna("")
        df_new = pd.concat([df_old, df_new], ignore_index=True)
    df_new.to_csv(ledger_path, index=False)
