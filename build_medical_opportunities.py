"""Write ranked Item 136 (large medical centre) pharmacy opportunities to a PRIVATE
Google Sheet tab.

These leads are deliberately NOT emitted into the public medical_centres.json or the
public map. They go only to the Sheet, reusing the existing gspread credentials.

Usage:  python build_medical_opportunities.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from src.medical_centres import is_item136_opportunity
from src.sheets import open_sheet, write_tab

MED_PATH = Path("medical_centres.json")
TAB_NAME = "Medical Centre Opportunities"
OPP_COLUMNS = [
    "Centre",
    "Address",
    "Suburb",
    "Doctors",
    "Tier",
    "Nearest pharmacy (m)",
    "Weekly hours",
    "Count source",
]
SHEET_ID = os.environ.get("SHEET_ID", "1502YpdciO9NSeyzBEr6wf75Y5Gpan-qPXw0elKHve0c")


def build_opportunity_rows(centres: list[dict[str, Any]]) -> list[list[str]]:
    """Filter to Item 136 opportunities and return spreadsheet rows (OPP_COLUMNS order).

    Sorted by doctor count desc, then distance to nearest pharmacy desc (further from
    an existing pharmacy = stronger lead).
    """
    opps = [c for c in centres if is_item136_opportunity(c)]
    opps.sort(
        key=lambda c: (c.get("doctor_count") or 0, c.get("nearest_pharmacy_m") or 0),
        reverse=True,
    )
    rows: list[list[str]] = []
    for c in opps:
        hours = c.get("weekly_hours")
        nearest = c.get("nearest_pharmacy_m")
        rows.append(
            [
                str(c.get("n", "")),
                str(c.get("addr", "")),
                str(c.get("suburb", "")),
                str(c.get("doctor_count", "")),
                str(c.get("category", "")),
                str(round(nearest)) if isinstance(nearest, (int, float)) else "",
                str(round(hours)) if isinstance(hours, (int, float)) else "",
                str(c.get("doctor_count_source", "")),
            ]
        )
    return rows


def main() -> int:
    if not MED_PATH.exists():
        print(f"ERROR: {MED_PATH} not found; run fetch_medical_centres.py first", file=sys.stderr)
        return 1
    centres = json.loads(MED_PATH.read_text(encoding="utf-8"))
    rows = build_opportunity_rows(centres)
    try:
        book = open_sheet(SHEET_ID)
        n = write_tab(book, TAB_NAME, OPP_COLUMNS, rows)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: could not write opportunity sheet: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} Item 136 opportunities to '{TAB_NAME}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
