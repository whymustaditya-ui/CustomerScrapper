"""Thin gspread wrapper for the living CRM Google Sheet.

The Sheet is Sales' single source of truth: leads arrive in batches of 10; Sales
updates `status` in place, and the batch gate (crm/tracker.py) reads it back.

Auth: a Google service account JSON key. Share the target spreadsheet with the
service account's email (Editor). Config in config/.env:
    GSHEETS_CREDENTIALS_FILE=config/service_account.json
    GSHEETS_SPREADSHEET_ID=<the long id from the sheet URL>
    GSHEETS_WORKSHEET=CRM            # optional, defaults to 'CRM'

If creds are absent or gspread isn't installed, every call is a safe no-op that
raises a clear, actionable error only when the caller actually needs the Sheet —
so the scraper/export half of the app runs fine without any Google setup.
"""

from __future__ import annotations

import os

import pandas as pd
from dotenv import load_dotenv

_ENV_PATH = os.path.join("config", ".env")
_DEFAULT_WORKSHEET = "CRM"


class GSheetsNotConfigured(RuntimeError):
    """Raised when a Sheet operation is attempted without working credentials."""


def _env() -> dict:
    load_dotenv(_ENV_PATH)
    return {
        "creds_file": os.getenv("GSHEETS_CREDENTIALS_FILE", "").strip(),
        "spreadsheet_id": os.getenv("GSHEETS_SPREADSHEET_ID", "").strip(),
        "worksheet": os.getenv("GSHEETS_WORKSHEET", _DEFAULT_WORKSHEET).strip() or _DEFAULT_WORKSHEET,
    }


def is_configured() -> bool:
    """True only if creds file + spreadsheet id are present and the file exists."""
    env = _env()
    return bool(env["creds_file"]) and bool(env["spreadsheet_id"]) and os.path.exists(env["creds_file"])


def status_message() -> str:
    """Human-readable config status for the UI."""
    env = _env()
    if not env["creds_file"] or not env["spreadsheet_id"]:
        return "🔒 Google Sheet not configured — add GSHEETS_* keys to config/.env."
    if not os.path.exists(env["creds_file"]):
        return f"⚠️ Credentials file not found: {env['creds_file']}"
    return "✅ Google Sheet connected."


def _header_for(columns: list[str] | None, header_row: list[str] | None) -> list[str]:
    """The labels to write to row 1 — friendly header_row if given, else the keys."""
    return list(header_row) if header_row else list(columns or [])


def _reverse_map(columns: list[str] | None, header_row: list[str] | None) -> dict:
    """Map any sheet header (friendly label OR raw machine key) back to the key.

    Lets reads stay correct whether the Sheet still has the old machine headers or
    the new friendly labels, so the layout repair never has to run first.
    """
    rev: dict[str, str] = {}
    if header_row and columns:
        for col, label in zip(columns, header_row):
            rev[label] = col
    for col in (columns or []):
        rev.setdefault(col, col)
    return rev


def _worksheet(columns: list[str] | None = None, header_row: list[str] | None = None):
    """Return the gspread worksheet handle, creating it + header if needed."""
    if not is_configured():
        raise GSheetsNotConfigured(status_message())
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as e:
        raise GSheetsNotConfigured(
            "gspread / google-auth not installed. Run: pip install -r requirements.txt"
        ) from e

    env = _env()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(env["creds_file"], scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(env["spreadsheet_id"])

    try:
        ws = sh.worksheet(env["worksheet"])
    except Exception:
        ws = sh.add_worksheet(title=env["worksheet"], rows=1000, cols=max(len(columns or []), 26))

    # Write a header row if the sheet is empty and columns were provided.
    if columns:
        existing = ws.row_values(1)
        if not existing:
            # Named args: gspread changed update()'s positional order across versions.
            ws.update(values=[_header_for(columns, header_row)], range_name="A1")
    return ws


def read_tracker(columns: list[str] | None = None, header_row: list[str] | None = None) -> pd.DataFrame:
    """Return the full CRM sheet as a DataFrame keyed by machine column names.

    Tolerant to the header style: friendly labels or old machine keys both map back
    to the canonical `columns` names.
    """
    ws = _worksheet(columns, header_row)
    records = ws.get_all_records()  # list of dicts keyed by header row
    df = pd.DataFrame(records)
    if not df.empty:
        rev = _reverse_map(columns, header_row)
        df = df.rename(columns={c: rev[c] for c in df.columns if c in rev})
    if df.empty and columns:
        df = pd.DataFrame(columns=columns)
    return df


def append_rows(rows: list[dict], columns: list[str], header_row: list[str] | None = None) -> int:
    """Append rows (ordered by `columns`) to the sheet. Returns count appended."""
    if not rows:
        return 0
    ws = _worksheet(columns, header_row)
    values = [[_cell(r.get(c, "")) for c in columns] for r in rows]
    ws.append_rows(values, value_input_option="USER_ENTERED")
    return len(values)


def overwrite_tracker(rows: list[dict], columns: list[str], header_row: list[str] | None = None) -> int:
    """Clear the worksheet and rewrite header + all rows in canonical order.

    Used by the layout repair to switch an existing Sheet to friendly headers and
    backfill new columns. Returns the number of data rows written.
    """
    ws = _worksheet(columns, header_row)
    values = [_header_for(columns, header_row)]
    values += [[_cell(r.get(c, "")) for c in columns] for r in rows]
    ws.clear()
    ws.update(values=values, range_name="A1")
    return len(rows)


def _cell(v) -> str:
    if v is None:
        return ""
    return str(v)
