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


def _open_named(worksheet_name: str):
    """Return a gspread handle for an EXISTING named tab in the same spreadsheet,
    or None if it doesn't exist / Sheets isn't configured. Never creates the tab —
    used to read side references (e.g. the Kontak Customer directory)."""
    if not is_configured():
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        return None
    env = _env()
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(env["creds_file"], scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(env["spreadsheet_id"])
    try:
        return sh.worksheet(worksheet_name)
    except Exception:
        return None


def read_column_values(worksheet_name: str, headers: set[str], header_row: int = 1) -> list[str]:
    """Flat list of raw cell strings from the given column labels of a named tab.

    `header_row` is the 1-based row holding the column labels (the Kontak Customer
    directory keeps them on row 3, under a banner + subtitle). Returns [] if the tab
    is missing or has no matching columns — a safe no-op so a renamed/absent
    reference tab never breaks a scrape.
    """
    ws = _open_named(worksheet_name)
    if ws is None:
        return []
    values = ws.get_all_values()
    if len(values) < header_row:
        return []
    labels = [str(h).strip() for h in values[header_row - 1]]
    idxs = [i for i, h in enumerate(labels) if h in headers]
    out: list[str] = []
    for row in values[header_row:]:
        for i in idxs:
            if i < len(row) and str(row[i]).strip():
                out.append(str(row[i]).strip())
    return out


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


def set_validations(rules: list[dict], data_rows: int = 1000) -> int:
    """Apply data-validation (dropdowns / date pickers) to whole columns.

    Each rule is a dict:
        {"col": int,              # 0-based column index
         "kind": "list" | "date",
         "values": list[str],     # options, for kind == "list"
         "strict": bool}          # True rejects off-list input; False just warns

    Validation covers rows 2..(data_rows+1) so newly appended rows inherit it.
    Returns the number of column rules applied.
    """
    if not rules:
        return 0
    ws = _worksheet()
    sheet_id = ws.id
    requests = []
    for rule in rules:
        if rule.get("kind") == "date":
            condition = {"type": "DATE_IS_VALID"}
        else:
            condition = {
                "type": "ONE_OF_LIST",
                "values": [{"userEnteredValue": v} for v in rule.get("values", [])],
            }
        col = rule["col"]
        requests.append({
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": data_rows + 1,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "rule": {
                    "condition": condition,
                    "strict": bool(rule.get("strict", False)),
                    "showCustomUi": True,
                },
            }
        })
    ws.spreadsheet.batch_update({"requests": requests})
    return len(requests)


def _rgb(r: int, g: int, b: int) -> dict:
    """A Sheets API color from 0–255 RGB ints."""
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def beautify(
    columns: list[str],
    header_row: list[str],
    status_col: int | None = None,
    status_colors: dict | None = None,
    score_col: int | None = None,
    col_widths: dict[int, int] | None = None,
    freeze_cols: int = 0,
    data_rows: int = 1000,
) -> int:
    """Apply a clean, professional visual layout to the worksheet. Idempotent.

    Dark navy header (white bold) with a green accent underline, frozen header,
    zebra-banded data rows, tuned column widths, a colour-coded Status column, and
    a subtle heat gradient on the score column. Safe to re-run — existing banding
    and conditional-format rules on this sheet are cleared first so clicking the
    button twice never stacks duplicates. Returns the number of API requests sent.
    """
    ws = _worksheet(columns, header_row)
    sheet_id = ws.id
    ncols = len(columns)
    end_row = data_rows + 1  # header + data_rows

    # Discover existing banding + conditional formats on THIS sheet so we can clear
    # them first (keeps the operation idempotent across repeated clicks).
    existing_bandings: list[int] = []
    existing_cf: int = 0
    try:
        meta = ws.spreadsheet.fetch_sheet_metadata()
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") == sheet_id:
                existing_bandings = [b["bandedRangeId"] for b in s.get("bandedRanges", [])]
                existing_cf = len(s.get("conditionalFormats", []))
                break
    except Exception:
        pass

    NAVY = _rgb(0x1F, 0x2A, 0x3C)
    WHITE = _rgb(0xFF, 0xFF, 0xFF)
    GREEN_ACCENT = _rgb(0x0F, 0x9D, 0x58)
    BAND = _rgb(0xF1, 0xF4, 0xF9)
    full = {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": end_row,
            "startColumnIndex": 0, "endColumnIndex": ncols}
    data = {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": end_row,
            "startColumnIndex": 0, "endColumnIndex": ncols}

    reqs: list[dict] = []

    # 0. Clear prior banding + conditional formats (idempotency).
    for bid in existing_bandings:
        reqs.append({"deleteBanding": {"bandedRangeId": bid}})
    for _ in range(existing_cf):
        reqs.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": 0}})

    # 1. Freeze header row (+ optional leading columns).
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {
            "frozenRowCount": 1, "frozenColumnCount": freeze_cols}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}})

    # 2. Header row: navy fill, white bold text, middle-aligned.
    reqs.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": ncols},
        "cell": {"userEnteredFormat": {
            "backgroundColor": NAVY,
            "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
            "wrapStrategy": "CLIP",
            "textFormat": {"foregroundColor": WHITE, "bold": True, "fontSize": 10}}},
        "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,"
                  "verticalAlignment,wrapStrategy,textFormat)"}})

    # 3. Header height + green accent underline.
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 38}, "fields": "pixelSize"}})
    reqs.append({"updateBorders": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": ncols},
        "bottom": {"style": "SOLID_THICK", "color": GREEN_ACCENT}}})

    # 4. Data rows: middle-aligned, readable size, comfortable height.
    reqs.append({"repeatCell": {
        "range": data,
        "cell": {"userEnteredFormat": {
            "verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10}}},
        "fields": "userEnteredFormat(verticalAlignment,textFormat.fontSize)"}})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 1, "endIndex": end_row},
        "properties": {"pixelSize": 28}, "fields": "pixelSize"}})

    # 5. Zebra banding on the data rows (header excluded — we styled it above).
    reqs.append({"addBanding": {"bandedRange": {
        "range": data,
        "rowProperties": {"firstBandColor": WHITE, "secondBandColor": BAND}}}})

    # 6. Column widths.
    for idx, px in (col_widths or {}).items():
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": idx, "endIndex": idx + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})

    # 7. Score column: 1-decimal number, centered, subtle red→green heat gradient.
    if score_col is not None:
        srange = {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": end_row,
                  "startColumnIndex": score_col, "endColumnIndex": score_col + 1}
        reqs.append({"repeatCell": {
            "range": srange,
            "cell": {"userEnteredFormat": {
                "horizontalAlignment": "CENTER",
                "numberFormat": {"type": "NUMBER", "pattern": "0.0"}}},
            "fields": "userEnteredFormat(horizontalAlignment,numberFormat)"}})
        reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [srange],
            "gradientRule": {
                "minpoint": {"color": _rgb(0xF8, 0xD2, 0xD2), "type": "MIN"},
                "midpoint": {"color": _rgb(0xFF, 0xF3, 0xCD), "type": "PERCENTILE", "value": "50"},
                "maxpoint": {"color": _rgb(0xC6, 0xE7, 0xD0), "type": "MAX"}}}}})

    # 8. Status column: one coloured chip per stage (bg + bold text).
    if status_col is not None and status_colors:
        # Center the status column for a tidy "chip" look.
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": end_row,
                      "startColumnIndex": status_col, "endColumnIndex": status_col + 1},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"}})
        for value, (bg, fg) in status_colors.items():
            reqs.append({"addConditionalFormatRule": {"index": 0, "rule": {
                "ranges": [{"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": end_row,
                            "startColumnIndex": status_col, "endColumnIndex": status_col + 1}],
                "booleanRule": {
                    "condition": {"type": "TEXT_EQ",
                                  "values": [{"userEnteredValue": value}]},
                    "format": {"backgroundColor": _rgb(*bg),
                               "textFormat": {"foregroundColor": _rgb(*fg), "bold": True}}}}}})

    ws.spreadsheet.batch_update({"requests": reqs})
    return len(reqs)


def _cell(v) -> str:
    if v is None:
        return ""
    return str(v)
