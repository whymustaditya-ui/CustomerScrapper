"""CRM batch gate, backed by the living Google Sheet.

The discipline, enforced in code: Sales gets leads in batches of 10, and a new
batch is only released once the current one is *worked* (every lead moved past
`New`). This makes blasting structurally impossible — you can't get batch N+1
until batch N is handled. Quality is enforced by the system, not requested.

No-double guarantee: a lead enters a batch only if its identity (place_id first,
per enrich/dedup) is absent from BOTH the Sheet and the master ledger.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote_plus

import pandas as pd

from enrich import dedup as dedup_mod
from enrich.parser import sanitize_text
from integrations import gsheets

# Free-text fields that may carry leaked icon-font glyphs from old scrapes.
_TEXT_FIELDS = ("name", "industry", "store_size", "address", "kelurahan", "notes")

# Order of columns written to the Sheet. Workflow fields first (what Sales touches),
# then lead data, then the dedup key. `maps_link` is intentionally kept LAST so that
# adding it never shifts the existing columns of a live Sheet — old rows just get a
# blank trailing cell until the layout repair backfills them.
TRACKER_COLUMNS = [
    "batch_id", "date_added", "status", "owner", "next_action_date", "notes",
    "name", "industry", "store_size", "store_size_score",
    "phone_normalized", "wa_link", "website_canonical",
    "kelurahan", "kota", "address", "place_id", "maps_link",
]

# Human-readable header labels (Bahasa Indonesia) shown in row 1 of the Sheet.
# The code keeps using the machine keys above internally; only the display label
# changes. Reads are tolerant of BOTH the friendly label and the old machine name,
# so an existing Sheet keeps working before the layout is repaired.
TRACKER_HEADERS = {
    "batch_id": "Batch",
    "date_added": "Tgl Masuk",
    "status": "Status",
    "owner": "PIC",
    "next_action_date": "Tgl Follow-up",
    "notes": "Catatan",
    "name": "Nama Bisnis",
    "industry": "Industri",
    "store_size": "Ukuran Usaha",
    "store_size_score": "Skor",
    "phone_normalized": "No. WhatsApp",
    "wa_link": "Chat WA",
    "website_canonical": "Website",
    "kelurahan": "Kelurahan",
    "kota": "Kota",
    "address": "Alamat",
    "place_id": "ID Lokasi",
    "maps_link": "Lokasi Maps",
}
TRACKER_HEADER_ROW = [TRACKER_HEADERS[c] for c in TRACKER_COLUMNS]

# Pipeline stages, in order. The first ("New") means untouched; everything after
# counts as "worked" by the batch gate. Single source of truth for the Status
# dropdown written to the Sheet, so the options can never drift from the logic.
STATUS_OPTIONS = ["New", "Contacted", "Replied", "Quoted", "Won", "Lost"]
STATUS_NEW = STATUS_OPTIONS[0]
WORKED_STATUSES = set(STATUS_OPTIONS[1:])

# Suggested quick-notes for the Catatan dropdown. Non-strict — Sales can still
# type anything; these just speed up the common dispositions.
NOTE_OPTIONS = [
    "Tidak diangkat",
    "Nomor salah / tidak aktif",
    "Minta dihubungi lagi",
    "Kirim penawaran",
    "Sudah dikirim sampel",
    "Nego harga",
    "Sudah punya supplier",
    "Tertarik, lanjut follow-up",
    "Tidak berminat",
    "Closing",
]

DEFAULT_BATCH_SIZE = 10


def build_wa_link(phone_normalized: str) -> str:
    """wa.me click-to-chat link from a +62 number (digits only, no +)."""
    digits = "".join(ch for ch in (phone_normalized or "") if ch.isdigit())
    return f"https://wa.me/{digits}" if digits else ""


_CID_RE = re.compile(r"0x[0-9a-fA-F]+:0x([0-9a-fA-F]+)$")


def build_maps_link(row: dict) -> str:
    """A clickable Google Maps link for a lead, most precise source first.

    1. `place_url` — the exact listing URL captured at scrape time.
    2. `place_id` in hex feature form (0xAAAA:0xBBBB) -> a direct `?cid=` link.
       The CID is the decimal of the second hex half and lands on the exact pin.
    3. Fallback — a Maps search by name + address (always resolves for a named
       business), so even rows without a place id still get a working link.
    """
    url = (row.get("place_url") or "").strip()
    if url.startswith("http"):
        return url

    m = _CID_RE.match((row.get("place_id") or "").strip())
    if m:
        return f"https://maps.google.com/?cid={int(m.group(1), 16)}"

    query = ", ".join(
        p for p in (row.get("name", ""), row.get("address", ""), row.get("kota", "")) if p
    ).strip()
    if query:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}"
    return ""


def _is_worked(status: str) -> bool:
    return (status or "").strip() in WORKED_STATUSES


def _existing_keys(tracker: pd.DataFrame) -> tuple[set, set, set]:
    """Identity sets already present in the Sheet: (place_ids, phones, name_kels)."""
    if tracker.empty:
        return set(), set(), set()
    pids, phones, namekels = set(), set(), set()
    for _, r in tracker.iterrows():
        row = r.to_dict()
        pid, phone, name_kel = dedup_mod._row_identity(row)
        if pid:
            pids.add(pid)
        if phone:
            phones.add(phone)
        if name_kel:
            namekels.add(name_kel)
    return pids, phones, namekels


def _latest_batch_id(tracker: pd.DataFrame) -> int:
    if tracker.empty or "batch_id" not in tracker.columns:
        return 0
    ids = pd.to_numeric(tracker["batch_id"], errors="coerce").dropna()
    return int(ids.max()) if not ids.empty else 0


def current_batch_complete(tracker: pd.DataFrame) -> tuple[bool, list[dict]]:
    """Is the latest batch fully worked? Returns (complete, unworked_rows)."""
    latest = _latest_batch_id(tracker)
    if latest == 0:
        return True, []  # no batch yet -> free to release the first
    batch = tracker[pd.to_numeric(tracker["batch_id"], errors="coerce") == latest]
    unworked = [r.to_dict() for _, r in batch.iterrows() if not _is_worked(r.get("status", ""))]
    return (len(unworked) == 0), unworked


def release_next_batch(pool: list[dict], size: int = DEFAULT_BATCH_SIZE) -> dict:
    """The gate. Release the next `size` highest-scored fresh leads — or refuse.

    Returns a result dict:
      {released: bool, reason: str, batch_id: int|None,
       leads: list[dict], unworked: list[dict], remaining_pool: int}
    """
    tracker = gsheets.read_tracker(TRACKER_COLUMNS, TRACKER_HEADER_ROW)

    complete, unworked = current_batch_complete(tracker)
    if not complete:
        return {
            "released": False,
            "reason": f"Current batch not finished — {len(unworked)} lead(s) still 'New'. "
                      "Work them in the Sheet before pulling the next 10.",
            "batch_id": _latest_batch_id(tracker),
            "leads": [],
            "unworked": unworked,
            "remaining_pool": 0,
        }

    # Defensive internal dedup first — never let two rows for the same place share
    # a batch, even if the caller passed a pool that wasn't pre-deduped.
    pool, _internal_dropped = dedup_mod.internal_dedup(pool)

    # Filter the scored pool against the Sheet — the Sheet is the dedup authority
    # for batches. (We deliberately do NOT filter against the master ledger here:
    # the scrape step already appended this run's leads to the ledger, so a ledger
    # filter would reject the very pool we're trying to release.)
    pids, phones, namekels = _existing_keys(tracker)
    fresh = []
    for row in pool:
        pid, phone, name_kel = dedup_mod._row_identity(row)
        if pid and pid in pids:
            continue
        if phone and phone in phones:
            continue
        if name_kel and name_kel in namekels:
            continue
        fresh.append(row)

    if not fresh:
        return {
            "released": False,
            "reason": "No fresh leads left in the pool — scrape more, or widen the area.",
            "batch_id": _latest_batch_id(tracker),
            "leads": [], "unworked": [], "remaining_pool": 0,
        }

    fresh.sort(key=lambda r: r.get("store_size_score") or 0, reverse=True)
    chosen = fresh[:size]

    new_batch_id = _latest_batch_id(tracker) + 1
    today = date.today().isoformat()
    batch_rows = []
    for r in chosen:
        row = dict(r)
        row["batch_id"] = new_batch_id
        row["date_added"] = today
        row["status"] = STATUS_NEW
        row["owner"] = row.get("owner", "Sales")
        row["next_action_date"] = ""
        row["notes"] = ""
        row["wa_link"] = build_wa_link(row.get("phone_normalized", ""))
        row["maps_link"] = build_maps_link(row)
        batch_rows.append(row)

    gsheets.append_rows(batch_rows, TRACKER_COLUMNS, TRACKER_HEADER_ROW)
    dedup_mod.append_to_ledger(chosen)

    return {
        "released": True,
        "reason": f"Released batch #{new_batch_id} — {len(batch_rows)} leads to the Sheet.",
        "batch_id": new_batch_id,
        "leads": batch_rows,
        "unworked": [],
        "remaining_pool": len(fresh) - len(chosen),
    }


def apply_dropdowns() -> int:
    """Add Sheet dropdowns: Status (strict), Tgl Follow-up (date picker),
    Catatan (suggested notes, free text still allowed). Idempotent — safe to
    re-run. Returns the number of columns given a validation rule.
    """
    rules = [
        {"col": TRACKER_COLUMNS.index("status"),
         "kind": "list", "values": STATUS_OPTIONS, "strict": True},
        {"col": TRACKER_COLUMNS.index("next_action_date"),
         "kind": "date", "strict": False},
        {"col": TRACKER_COLUMNS.index("notes"),
         "kind": "list", "values": NOTE_OPTIONS, "strict": False},
    ]
    return gsheets.set_validations(rules)


def repair_sheet_layout() -> dict:
    """One-click tidy-up for an existing Sheet: friendly headers + Maps links.

    Rewrites row 1 to the human-readable labels and backfills `maps_link` (and any
    missing `wa_link`) for rows already in the Sheet, then writes everything back in
    the canonical column order. Idempotent — safe to run repeatedly. Existing status
    edits and notes are preserved because we read the rows first and only fill gaps.
    """
    df = gsheets.read_tracker(TRACKER_COLUMNS, TRACKER_HEADER_ROW)
    rows = df.to_dict("records") if not df.empty else []
    filled = 0
    for r in rows:
        # Scrub leaked icon glyphs (tofu ▯) from rows written by older scrapes.
        for field in _TEXT_FIELDS:
            if field in r:
                r[field] = sanitize_text(str(r.get(field, "") or ""))
        if not str(r.get("maps_link", "") or "").strip():
            link = build_maps_link(r)
            if link:
                r["maps_link"] = link
                filled += 1
        if not str(r.get("wa_link", "") or "").strip():
            r["wa_link"] = build_wa_link(r.get("phone_normalized", ""))
    written = gsheets.overwrite_tracker(rows, TRACKER_COLUMNS, TRACKER_HEADER_ROW)
    return {"rows": written, "maps_links_filled": filled}
