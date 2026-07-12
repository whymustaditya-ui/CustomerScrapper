"""Scrape-coverage tracker: a cursor over the map so runs advance, not restart.

The scraper walks a grid of (kota × kecamatan). Google Maps returns the same top
results for a broad city query, so once those are in the ledger a city-wide run
goes dry. To keep surfacing new businesses we drill per-kecamatan and remember
which slices we've already worked and when — this file is that memory.

`data/coverage.csv` holds one row per (kota, kecamatan):
    kota, kecamatan, last_scraped (ISO date), new_leads (last run), exhausted (0/1)

Each run asks `next_slices()` for the freshest un-worked slices in priority order
(Jaktim first, then Bekasi, then the rest), scrapes them until the batch target is
met, and calls `record()` for each. A slice that yields ~0 new leads is marked
`exhausted` and cooled down longer before a recheck (new businesses do open).
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd

from scraper.areas import JABODETABEK

COVERAGE_PATH = os.path.join("data", "coverage.csv")
_COLUMNS = ["kota", "kecamatan", "last_scraped", "new_leads", "exhausted"]

# Tuning knobs (days). Productive slices come back sooner than dry ones.
COOLDOWN_DAYS = 14   # a slice that found new leads: don't re-scrape within 2 weeks
REFRESH_DAYS = 45    # an exhausted slice: recheck only after ~6 weeks
EXHAUSTED_BELOW = 1  # < this many new leads on a run marks the slice exhausted


def _priority_kota_index(priority_kota: list[str]) -> dict[str, int]:
    """Map kota -> sort rank: priority kota first (in order), everything else after."""
    order = {k: i for i, k in enumerate(priority_kota)}
    nxt = len(priority_kota)
    for kota in JABODETABEK:
        if kota not in order:
            order[kota] = nxt
            nxt += 1
    return order


def load() -> pd.DataFrame:
    """Return the coverage table (empty, correctly-typed frame if the file is absent)."""
    if not os.path.exists(COVERAGE_PATH):
        return pd.DataFrame(columns=_COLUMNS)
    try:
        df = pd.read_csv(COVERAGE_PATH, dtype=str).fillna("")
    except Exception:
        return pd.DataFrame(columns=_COLUMNS)
    for c in _COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[_COLUMNS]


def _days_since(iso: str) -> float:
    if not iso:
        return float("inf")  # never scraped -> infinitely stale -> highest priority
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d").date()
    except Exception:
        return float("inf")
    return (date.today() - d).days


def next_slices(priority_kota: list[str], limit: int = 30) -> list[tuple[str, str]]:
    """Pick the freshest eligible (kota, kecamatan) slices to scrape this run.

    Eligible = never scraped, OR a productive slice past its cooldown, OR an
    exhausted slice past its longer refresh window. Ordered by kota priority, then
    oldest-first, so Jaktim's un-worked kecamatan lead, then Bekasi's, then the rest.
    Returns up to `limit`; the runner stops early once the batch target is met.
    """
    cov = load()
    seen = {(r["kota"], r["kecamatan"]): r for _, r in cov.iterrows()}
    rank = _priority_kota_index(priority_kota)

    candidates: list[tuple] = []  # (kota_rank, -staleness, kota, kecamatan)
    for kota, kecs in JABODETABEK.items():
        for kec in kecs:
            row = seen.get((kota, kec))
            stale = _days_since(row["last_scraped"]) if row is not None else float("inf")
            exhausted = bool(row is not None and str(row.get("exhausted", "")).strip() in ("1", "True", "true"))
            if row is None:
                eligible = True
            elif exhausted:
                eligible = stale >= REFRESH_DAYS
            else:
                eligible = stale >= COOLDOWN_DAYS
            if not eligible:
                continue
            # Sort: kota priority, then most-stale first (never-scraped = inf on top),
            # then exhausted after productive at equal staleness.
            stale_key = 1e9 if stale == float("inf") else stale
            candidates.append((rank[kota], -stale_key, 1 if exhausted else 0, kota, kec))

    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(c[3], c[4]) for c in candidates[:limit]]


def record(kota: str, kecamatan: str, new_leads: int) -> None:
    """Upsert a slice's outcome: stamp today, store new-lead count, flag exhaustion."""
    cov = load()
    mask = (cov["kota"] == kota) & (cov["kecamatan"] == kecamatan)
    exhausted = "1" if int(new_leads) < EXHAUSTED_BELOW else "0"
    payload = {
        "kota": kota, "kecamatan": kecamatan,
        "last_scraped": date.today().isoformat(),
        "new_leads": str(int(new_leads)), "exhausted": exhausted,
    }
    if mask.any():
        for k, v in payload.items():
            cov.loc[mask, k] = v
    else:
        cov = pd.concat([cov, pd.DataFrame([payload])], ignore_index=True)

    os.makedirs(os.path.dirname(COVERAGE_PATH), exist_ok=True)
    cov[_COLUMNS].to_csv(COVERAGE_PATH, index=False)
