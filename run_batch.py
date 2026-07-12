"""Headless scheduled runner for the ROSH Super Customer Scraper.

Runs unattended (Windows Task Scheduler, weekdays 05:20 on Bro's home PC — never
DJP/MoF network). It scrapes Google Maps for F&B leads, **prioritizing Jakarta
Timur then Bekasi** and only widening to the rest of Jabodetabek if it still needs
more, dedups against the Kontak Customer directory + the master ledger, then
releases the next gated batch into the `CRM Leads (Scraper)` tab.

It respects the batch gate: if the previous batch isn't fully worked, it logs
"skipped" and writes nothing — the same anti-blast discipline the UI enforces.

Usage:
    python run_batch.py                # release the next batch of 10
    python run_batch.py --size 5       # smaller batch
    python run_batch.py --dry-run      # scrape + rank, but never touch Sheet/ledger
    python run_batch.py --cap 200      # raise the per-kota safety cap (anti-block)

The scraper stays in Python here (not Apps Script) because it needs a real
Chromium via Playwright and a residential IP; Apps Script can't drive a browser,
runs on Google IPs (instant Maps block), and caps at ~6 min.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime

from scraper import categories as cat_mod
from scraper import coverage as coverage_mod
from scraper import gmaps
from enrich.parser import enrich_row
from enrich.scoring import score_row
from enrich import dedup as dedup_mod
from integrations import gsheets
from crm import tracker as crm_tracker

# Priority order: fill from Jaktim + Bekasi first, only widen if still short.
PRIORITY_KOTA = ["Jakarta Timur", "Bekasi"]

# Kontak Customer directory (same spreadsheet). The tab title carries a leading 📇
# emoji and MUST match exactly — gspread does no fuzzy match, so a wrong name makes
# the customer dedup a silent no-op. Overridable via env for safety.
CUSTOMER_DIR_TAB = os.getenv("GSHEETS_CUSTOMER_WORKSHEET", "📇 Kontak Customer")
CUSTOMER_DIR_PHONE_COLS = {"No WA", "No Bisnis"}
CUSTOMER_DIR_HEADER_ROW = int(os.getenv("GSHEETS_CUSTOMER_HEADER_ROW", "3") or "3")

LOG_PATH = os.path.join("data", "output", "scheduled_run.log")


def _setup_logging() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("rosh.run_batch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def _customer_dir_phones(log: logging.Logger) -> set[str]:
    try:
        raw = gsheets.read_column_values(
            CUSTOMER_DIR_TAB, CUSTOMER_DIR_PHONE_COLS, header_row=CUSTOMER_DIR_HEADER_ROW
        )
        phones = dedup_mod.phone_set(raw)
        log.info(f"Kontak Customer directory: {len(phones)} unique customer phones loaded.")
        return phones
    except Exception as e:
        log.info(f"Kontak Customer directory unavailable ({e}); customer dedup skipped.")
        return set()


def _make_qualifier(dir_phones: set[str], min_reviews: int):
    """A per-lead predicate mirroring the release rules, so the scraper stops once
    it has collected enough leads that will actually survive: review floor + a real
    WhatsApp number + net-new vs ledger / customers / already-counted this run.

    Returns (qualifies_fn, get_count) — counted sets persist across kota so the same
    place is never counted twice when we widen the search.
    """
    lg_pid, lg_phone, lg_key = dedup_mod.ledger_identity_sets()
    q_pid: set[str] = set()
    q_phone: set[str] = set()
    q_key: set[str] = set()

    def qualifies(raw_row: dict) -> bool:
        er = enrich_row(raw_row)
        if (er.get("review_count") or 0) < min_reviews:
            return False
        if not str(er.get("phone_normalized", "") or "").strip():
            return False  # WA-only: no mobile number, doesn't count
        pid, phone, name_kel = dedup_mod._row_identity(er)
        if (pid and pid in lg_pid) or (phone and phone in lg_phone) or (name_kel and name_kel in lg_key):
            return False
        if dir_phones and (dedup_mod._row_phones(er) & dir_phones):
            return False  # already an Accurate customer
        if (pid and pid in q_pid) or (phone and phone in q_phone) or (name_kel and name_kel in q_key):
            return False
        if pid:
            q_pid.add(pid)
        if phone:
            q_phone.add(phone)
        if name_kel:
            q_key.add(name_kel)
        return True

    def get_count() -> int:
        # phones are the strongest signal present on every WA-qualified lead.
        return len(q_phone)

    return qualifies, get_count


def _process(raw_all: list[dict], dir_phones: set[str], min_reviews: int) -> list[dict]:
    """Enrich → dedup → score → filter the combined raw listings into a fresh pool.
    Mirrors the app's pipeline (customer directory + ledger), minus the UI."""
    enriched = [enrich_row(r) for r in raw_all]
    unique, _dropped = dedup_mod.internal_dedup(enriched)

    name_counts: dict[str, int] = {}
    for r in unique:
        key = (r.get("name", "") or "").strip().lower()
        name_counts[key] = name_counts.get(key, 0) + 1
    for r in unique:
        bc = name_counts.get((r.get("name", "") or "").strip().lower(), 1)
        r.update(score_row(r, branch_count=bc))

    legit = [r for r in unique if (r.get("review_count") or 0) >= min_reviews]
    # WA-only, same as the release gate: only mobile (+628…) numbers reach Sales, so
    # the dry-run preview matches exactly what release_next_batch would push.
    legit = [r for r in legit if str(r.get("phone_normalized", "") or "").strip()]
    kept, _cust = dedup_mod.dedup_customer_phones(legit, dir_phones)
    new_leads, _seen = dedup_mod.filter_against_ledger(kept)
    return new_leads


def main() -> int:
    ap = argparse.ArgumentParser(description="ROSH scheduled lead-batch runner.")
    default_size = os.getenv("SCHEDULED_BATCH_SIZE", "").strip()
    ap.add_argument("--size", type=int,
                    default=int(default_size) if default_size.isdigit() else crm_tracker.DEFAULT_BATCH_SIZE,
                    help="Fresh leads to release this run (default 10 / SCHEDULED_BATCH_SIZE).")
    ap.add_argument("--cap", type=int, default=80,
                    help="Safety cap: max listings to scan per kecamatan slice (anti-block).")
    ap.add_argument("--max-slices", type=int, default=30,
                    help="Max kecamatan slices to try this run before giving up.")
    ap.add_argument("--min-reviews", type=int, default=5,
                    help="Drop listings below this review count (ghost-lead filter).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Scrape + rank only; never write to the Sheet or ledger.")
    args = ap.parse_args()

    log = _setup_logging()
    log.info("=" * 68)
    log.info(f"RUN START  size={args.size}  cap/kota={args.cap}  "
             f"min_reviews={args.min_reviews}  dry_run={args.dry_run}")

    if not gsheets.is_configured():
        log.info("ABORT: Google Sheet not configured (config/.env). Nothing to do.")
        return 2

    # Gate check BEFORE scraping: if Sales hasn't worked the last batch, we can't
    # release anyway, so don't waste a scrape (or wrongly advance the coverage
    # cursor). A dry-run skips this so it can still preview a pool.
    if not args.dry_run:
        tracker_df = gsheets.read_tracker(crm_tracker.TRACKER_COLUMNS, crm_tracker.TRACKER_HEADER_ROW)
        complete, unworked = crm_tracker.current_batch_complete(tracker_df)
        if not complete:
            log.info(f"SKIPPED — previous batch not worked ({len(unworked)} lead(s) still 'New'). "
                     "No scrape run. Work them in the Sheet, then the next run releases.")
            return 0

    dir_phones = _customer_dir_phones(log)
    qualifies, get_count = _make_qualifier(dir_phones, args.min_reviews)
    buckets = cat_mod.buckets()  # all F&B categories

    # Feed-stage skip set: places already released to the Sheet (the master ledger).
    # Passed to the scraper so it skips their cards before the costly detail visit —
    # each run resumes on new listings instead of re-scraping the same top results.
    known_place_ids, _lg_phone, _lg_key = dedup_mod.ledger_identity_sets()
    log.info(f"Ledger: {len(known_place_ids)} known place_ids will be skipped at the feed.")

    # Walk the coverage cursor: the freshest un-worked kecamatan first (Jaktim, then
    # Bekasi, then the rest). Each slice is scraped, processed, and recorded, so the
    # next run picks up where this one stopped instead of re-querying the same area.
    slices = coverage_mod.next_slices(PRIORITY_KOTA, limit=args.max_slices)
    if not slices:
        log.info("All kecamatan recently covered or exhausted — nothing fresh to scrape. "
                 "They'll become eligible again after their cooldown/refresh window.")
        return 0
    log.info(f"Coverage picked {len(slices)} fresh slices; first few: {slices[:5]}")

    pool: list[dict] = []
    pool_ids: set[str] = set()  # cross-slice dedup as we merge
    for kota, kec in slices:
        if get_count() >= args.size:
            break
        remaining = args.size - get_count()
        log.info(f"Scraping {kec}, {kota} (need {remaining} more)…")
        try:
            raw = gmaps.scrape(
                kota=kota,
                kecamatan_list=[kec],
                buckets=buckets,
                max_results=args.cap,
                headless=True,
                watch_mode=False,
                target_qualified=remaining,
                qualifies=qualifies,
                known_place_ids=known_place_ids,
            )
        except Exception as e:
            log.info(f"  {kec}, {kota}: scrape error ({e}); moving on.")
            continue

        slice_leads = _process(raw, dir_phones, args.min_reviews)
        new_here = 0
        for r in slice_leads:
            pid, phone, name_kel = dedup_mod._row_identity(r)
            key = pid or phone or name_kel
            if key in pool_ids:
                continue
            pool_ids.add(key)
            pool.append(r)
            new_here += 1
        if not args.dry_run:
            coverage_mod.record(kota, kec, new_here)  # advance the cursor for this slice
        log.info(f"  {kec}, {kota}: {len(raw)} scanned → {new_here} new; "
                 f"cumulative pool={len(pool)}.")

    pool.sort(key=lambda r: r.get("store_size_score") or 0, reverse=True)
    log.info(f"Fresh pool after dedup (customers + ledger): {len(pool)} leads.")

    if args.dry_run:
        complete, unworked = crm_tracker.current_batch_complete(
            gsheets.read_tracker(crm_tracker.TRACKER_COLUMNS, crm_tracker.TRACKER_HEADER_ROW)
        )
        gate = "OPEN" if complete else f"HELD ({len(unworked)} unworked)"
        log.info(f"[DRY-RUN] gate={gate}. Would release top {args.size}:")
        for i, r in enumerate(pool[:args.size], 1):
            log.info(f"  {i:>2}. {r.get('name','?')[:40]:<40} "
                     f"score={r.get('store_size_score',0)!s:<5} "
                     f"{r.get('phone_normalized','')}  {r.get('kota','')}")
        log.info("[DRY-RUN] no writes made.")
        return 0

    res = crm_tracker.release_next_batch(pool, size=args.size)
    if res["released"]:
        names = ", ".join(r.get("name", "?") for r in res["leads"])
        log.info(f"RELEASED batch #{res['batch_id']} — {len(res['leads'])} leads. "
                 f"Pool left: {res['remaining_pool']}.")
        log.info(f"  Leads: {names}")
        return 0

    log.info(f"SKIPPED — {res['reason']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
