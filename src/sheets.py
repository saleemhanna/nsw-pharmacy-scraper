from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import google.auth
import gspread
import structlog
from google.oauth2.service_account import Credentials

from src.diff import Change
from src.models import Pharmacy

Spreadsheet = Any
Worksheet = Any

log = structlog.get_logger()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CREDENTIALS = PROJECT_ROOT / "credentials.json"
DEFAULT_TOKEN = PROJECT_ROOT / "token.json"

CURRENT_COLUMNS = [
    "trading_name",
    "registration_number",
    "status",
    "start_date",
    "granted_date",
    "expiry_date",
    "licensee",
    "owners",
    "financial_interests",
    "premises_details",
    "address",
    "suburb",
    "postcode",
    "state",
    "latitude",
    "longitude",
    "source_url",
    "scraped_at",
    "raw_json",
]
CHANGES_COLUMNS = [
    "detected_at",
    "change_type",
    "registration_number",
    "field_changed",
    "old_value",
    "new_value",
]
RUNS_COLUMNS = [
    "started_at",
    "finished_at",
    "postcodes_queried",
    "postcodes_with_results",
    "records_scraped",
    "records_failed",
    "duplicates_dropped",
    "status",
    "notes",
]

SOURCE_URL = "https://verify.licence.nsw.gov.au/home/Pharmacies"


def open_sheet(
    sheet_id: str,
    service_account_json: str | None = None,
    credentials_path: Path | None = None,
    token_path: Path | None = None,
) -> Spreadsheet:
    if service_account_json:
        creds: Any = Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            json.loads(service_account_json), scopes=SCOPES
        )
        return gspread.authorize(creds).open_by_key(sheet_id)

    creds_file = credentials_path or DEFAULT_CREDENTIALS
    tok_file = token_path or DEFAULT_TOKEN
    for f in [creds_file, tok_file]:
        if f.exists():
            raw = f.read_bytes()
            if raw[:3] == b"\xef\xbb\xbf":
                f.write_bytes(raw[3:])
    if creds_file.exists():
        gc = gspread.oauth(
            credentials_filename=str(creds_file),
            authorized_user_filename=str(tok_file),
            scopes=SCOPES,
        )
        return gc.open_by_key(sheet_id)

    creds, _ = google.auth.default(scopes=SCOPES)  # type: ignore[no-untyped-call]
    return gspread.authorize(creds).open_by_key(sheet_id)


def _ensure_tab(book: Spreadsheet, name: str, columns: list[str]) -> Worksheet:
    try:
        ws = book.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(name, rows=2, cols=len(columns))
        ws.update(values=[columns], range_name="A1")
    return ws


def read_previous_current(book: Spreadsheet) -> list[dict[str, str]]:
    ws = _ensure_tab(book, "Current", CURRENT_COLUMNS)
    rows = ws.get_all_records()
    return [{k: str(v) for k, v in r.items()} for r in rows]


def _pharmacy_to_row(p: Pharmacy, scraped_at: str) -> list[str]:
    d = p.model_dump()
    return [
        d["trading_name"],
        d["registration_number"],
        d["status"],
        d["start_date"],
        d["granted_date"] or "",
        d["expiry_date"] or "",
        d["licensee"],
        d["owners"],
        d["financial_interests"],
        d["premises_details"],
        d["address"],
        d["suburb"],
        d["postcode"],
        d["state"],
        str(d["latitude"]),
        str(d["longitude"]),
        SOURCE_URL,
        scraped_at,
        d["raw_json"],
    ]


def pharmacy_to_dict_for_diff(p: Pharmacy, scraped_at: str) -> dict[str, str]:
    row = _pharmacy_to_row(p, scraped_at)
    return dict(zip(CURRENT_COLUMNS, row, strict=True))


def write_current(
    book: Spreadsheet, pharmacies: list[Pharmacy], scraped_at: str
) -> None:
    ws = _ensure_tab(book, "Current", CURRENT_COLUMNS)
    ws.clear()
    sorted_pharmacies = sorted(pharmacies, key=lambda p: (p.trading_name or "").lower())
    rows = [CURRENT_COLUMNS] + [_pharmacy_to_row(p, scraped_at) for p in sorted_pharmacies]
    ws.update(values=rows, range_name="A1", value_input_option="RAW")
    _format_current_tab(ws, len(rows))


def _format_current_tab(ws: Worksheet, total_rows: int) -> None:
    from gspread.utils import rowcol_to_a1
    num_cols = len(CURRENT_COLUMNS)
    header_range = f"A1:{rowcol_to_a1(1, num_cols)}"
    ws.format(header_range, {
        "textFormat": {"bold": True, "fontSize": 10},
        "backgroundColor": {"red": 0.2, "green": 0.3, "blue": 0.5},
        "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "horizontalAlignment": "CENTER",
    })
    data_range = f"A2:{rowcol_to_a1(total_rows, num_cols)}"
    ws.format(data_range, {
        "textFormat": {"fontSize": 9},
        "wrapStrategy": "CLIP",
    })
    ws.freeze(rows=1)
    raw_json_col = CURRENT_COLUMNS.index("raw_json") + 1
    ws.hide_columns(raw_json_col - 1, raw_json_col)


def append_changes(
    book: Spreadsheet, changes: list[Change], detected_at: str
) -> None:
    if not changes:
        return
    ws = _ensure_tab(book, "Changes", CHANGES_COLUMNS)
    rows = [
        [
            detected_at,
            c.change_type,
            c.registration_number,
            c.field_changed,
            c.old_value,
            c.new_value,
        ]
        for c in changes
    ]
    ws.append_rows(rows, value_input_option="RAW")


def append_run(book: Spreadsheet, row: dict[str, Any]) -> None:
    ws = _ensure_tab(book, "Runs", RUNS_COLUMNS)
    ws.append_row(
        [str(row.get(c, "")) for c in RUNS_COLUMNS], value_input_option="RAW"
    )


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
