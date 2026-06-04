"""Rebuild all analysis tabs on the Google Sheet."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import gspread
import structlog
from gspread.utils import rowcol_to_a1

from src.models import Pharmacy

log = structlog.get_logger()

Spreadsheet = Any
Worksheet = Any

EXCLUDED_SURNAMES = {"tascone", "verrocchi", "gance"}

SYD_LAT_MIN, SYD_LAT_MAX = -34.2, -33.4
SYD_LON_MIN, SYD_LON_MAX = 150.5, 151.5


def _format_tab(ws: Worksheet, columns: list[str], total_rows: int) -> None:
    num_cols = len(columns)
    header_range = f"A1:{rowcol_to_a1(1, num_cols)}"
    ws.format(header_range, {
        "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "backgroundColor": {"red": 0.2, "green": 0.3, "blue": 0.5},
        "horizontalAlignment": "CENTER",
    })
    data_range = f"A2:{rowcol_to_a1(total_rows, num_cols)}"
    ws.format(data_range, {"textFormat": {"fontSize": 9}})
    ws.freeze(rows=1)


def _write_tab(book: Spreadsheet, tab_name: str, columns: list[str], rows: list[list[str]]) -> None:
    try:
        ws = book.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(tab_name, rows=2, cols=len(columns))
    ws.update(values=rows, range_name="A1", value_input_option="RAW")
    _format_tab(ws, columns, len(rows))


def _is_excluded(name: str) -> bool:
    name_lower = name.lower()
    if "pty" in name_lower or "trading" in name_lower or "pharmacy o2" in name_lower or "pharmacy 0" in name_lower:
        return True
    return any(s in name_lower for s in EXCLUDED_SURNAMES)


def _is_individual(name: str) -> bool:
    name_lower = name.lower()
    if "pty" in name_lower or "trading" in name_lower or "pharmacy o2" in name_lower or "pharmacy 0" in name_lower:
        return False
    return True


def _get_owners_and_fi(p: dict[str, str]) -> set[str]:
    owners = [o.strip() for o in (p.get("owners") or "").split(" | ") if o.strip()]
    fis = [f.split(" [related:")[0].strip() for f in (p.get("financial_interests") or "").split(" | ") if f.strip()]
    return set(owners) | set(fis)


def _is_sydney(p: dict[str, Any]) -> bool:
    lat = p.get("latitude", 0)
    lon = p.get("longitude", 0)
    return SYD_LAT_MIN <= lat <= SYD_LAT_MAX and SYD_LON_MIN <= lon <= SYD_LON_MAX


def _get_address(p: dict[str, Any]) -> str:
    premises = p.get("premises_details", "")
    if premises:
        return premises.rsplit(" (", 1)[0]
    return p.get("address", "")


def rebuild_all_tabs(book: Spreadsheet, pharmacies: list[Pharmacy]) -> None:
    pharmacy_dicts = [p.model_dump() for p in pharmacies]

    _rebuild_cwh_all(book, pharmacy_dicts)
    _rebuild_cwh_owner_tabs(book, pharmacy_dicts)
    _rebuild_non_cwh_owner_tabs(book, pharmacy_dicts)
    _rebuild_20yr_tabs(book, pharmacy_dicts)
    log.info("all_tabs_rebuilt")


def _rebuild_cwh_all(book: Spreadsheet, data: list[dict]) -> None:
    from src.sheets import CURRENT_COLUMNS, SOURCE_URL

    cw = [p for p in data if "chemist warehouse" in (p.get("trading_name") or "").lower()
          or "chemist warehouse" in (p.get("licensee") or "").lower()]
    cw.sort(key=lambda p: (p.get("trading_name") or "").lower())

    columns = CURRENT_COLUMNS
    rows = [columns]
    for p in cw:
        rows.append([
            p["trading_name"],
            p["registration_number"],
            p["status"],
            p.get("start_date", ""),
            p.get("granted_date") or "",
            p.get("expiry_date") or "",
            p["licensee"],
            p["owners"],
            p["financial_interests"],
            p.get("premises_details", ""),
            p["address"],
            p["suburb"],
            p["postcode"],
            p["state"],
            str(p["latitude"]),
            str(p["longitude"]),
            SOURCE_URL,
            "",
            p["raw_json"],
        ])

    _write_tab(book, "Chemist Warehouse", columns, rows)
    log.info("tab_rebuilt", tab="Chemist Warehouse", records=len(cw))


def _rebuild_cwh_owner_tabs(book: Spreadsheet, data: list[dict]) -> None:
    cw = [p for p in data if "chemist warehouse" in (p.get("trading_name") or "").lower()
          or "chemist warehouse" in (p.get("licensee") or "").lower()]

    store_lookup: dict[str, dict] = {}
    person_stores: dict[str, set[str]] = defaultdict(set)
    person_dates: dict[str, str] = {}

    for p in cw:
        store = p["trading_name"] or p["registration_number"]
        start = p.get("start_date", "")
        store_lookup[store] = p
        for person in _get_owners_and_fi(p):
            person_stores[person].add(store)
            if start:
                existing = person_dates.get(person, "")
                if not existing or start > existing:
                    person_dates[person] = start

    individuals = {k: v for k, v in person_stores.items() if not _is_excluded(k) and _is_individual(k)}

    # Single-store owners
    cols_single = ["#", "start_date", "owner", "store", "registration_number", "address", "suburb", "postcode"]
    singles = []
    for person, stores in individuals.items():
        if len(stores) != 1:
            continue
        date = person_dates.get(person, "")
        store = list(stores)[0]
        singles.append((date, person, store))
    singles.sort(key=lambda x: x[0], reverse=True)

    rows = [cols_single]
    for i, (date, person, store) in enumerate(singles, 1):
        p = store_lookup.get(store, {})
        rows.append([str(i), date or "", person, store, p.get("registration_number", ""),
                     _get_address(p), p.get("suburb", ""), p.get("postcode", "")])
    _write_tab(book, "CWH Single Owners", cols_single, rows)
    log.info("tab_rebuilt", tab="CWH Single Owners", records=len(singles))

    # Multi-store owners (2, 3, 4, 5)
    cols_multi = ["#", "owner", "stores", "store_name", "registration_number", "start_date", "address", "suburb", "postcode"]
    for count in [2, 3, 4, 5]:
        tab_name = f"CWH {count}-Store Owners"
        matching = {k: v for k, v in individuals.items() if len(v) == count}
        sorted_owners = sorted(matching.keys(), key=lambda k: person_dates.get(k, ""), reverse=True)

        rows = [cols_multi]
        i = 1
        for owner in sorted_owners:
            stores = sorted(matching[owner],
                          key=lambda s: store_lookup.get(s, {}).get("start_date", ""), reverse=True)
            for j, store in enumerate(stores):
                p = store_lookup.get(store, {})
                rows.append([
                    str(i) if j == 0 else "",
                    owner if j == 0 else "",
                    str(count) if j == 0 else "",
                    store,
                    p.get("registration_number", ""),
                    p.get("start_date", ""),
                    _get_address(p),
                    p.get("suburb", ""),
                    p.get("postcode", ""),
                ])
            i += 1

        _write_tab(book, tab_name, cols_multi, rows)
        log.info("tab_rebuilt", tab=tab_name, records=len(sorted_owners))


def _rebuild_20yr_tabs(book: Spreadsheet, data: list[dict]) -> None:
    person_stores: dict[str, set[str]] = defaultdict(set)
    person_dates: dict[str, str] = {}
    store_lookup: dict[str, dict] = {}

    for p in data:
        store = p["trading_name"] or p["registration_number"]
        start = p.get("start_date", "")
        store_lookup[store] = p
        for person in _get_owners_and_fi(p):
            person_stores[person].add(store)
            if start:
                existing = person_dates.get(person, "")
                if not existing or start < existing:
                    person_dates[person] = start

    cutoff = "2006-06-01"
    columns = ["#", "start_date", "years_held", "owner", "store", "registration_number", "address", "suburb", "postcode", "latitude", "longitude"]

    # All 20+ year single owners
    all_results = []
    sydney_results = []

    for person, stores in person_stores.items():
        if len(stores) != 1:
            continue
        if not _is_individual(person):
            continue
        date = person_dates.get(person, "")
        if not date or date >= cutoff:
            continue
        store = list(stores)[0]
        p = store_lookup.get(store, {})
        year = int(date[:4]) if date[:4].isdigit() else 0
        years_held = str(2026 - year) if year > 1900 else "20+"

        entry = (date, person, store, p, years_held)
        all_results.append(entry)
        if _is_sydney(p):
            sydney_results.append(entry)

    # All NSW tab - sorted oldest first
    all_results.sort(key=lambda x: x[0])
    rows = [columns]
    for i, (date, person, store, p, years) in enumerate(all_results, 1):
        rows.append([str(i), date, years, person, store, p.get("registration_number", ""),
                     _get_address(p), p.get("suburb", ""), p.get("postcode", ""),
                     str(p.get("latitude", "")), str(p.get("longitude", ""))])
    _write_tab(book, "Single Owner 20+ Years", columns, rows)
    log.info("tab_rebuilt", tab="Single Owner 20+ Years", records=len(all_results))

    # Sydney tab - sorted by suburb
    sydney_results.sort(key=lambda x: (x[3].get("suburb", "").upper(), x[0]))
    rows = [columns]
    for i, (date, person, store, p, years) in enumerate(sydney_results, 1):
        rows.append([str(i), date, years, person, store, p.get("registration_number", ""),
                     _get_address(p), p.get("suburb", ""), p.get("postcode", ""),
                     str(p.get("latitude", "")), str(p.get("longitude", ""))])
    _write_tab(book, "Sydney 20+ Year Owners", columns, rows)
    log.info("tab_rebuilt", tab="Sydney 20+ Year Owners", records=len(sydney_results))


def _rebuild_non_cwh_owner_tabs(book: Spreadsheet, data: list[dict]) -> None:
    non_cwh = [p for p in data if "chemist warehouse" not in (p.get("trading_name") or "").lower()
               and "chemist warehouse" not in (p.get("licensee") or "").lower()]

    store_lookup: dict[str, dict] = {}
    person_stores: dict[str, set[str]] = defaultdict(set)
    person_dates: dict[str, str] = {}

    for p in non_cwh:
        store = p["trading_name"] or p["registration_number"]
        start = p.get("start_date", "")
        store_lookup[store] = p
        for person in _get_owners_and_fi(p):
            person_stores[person].add(store)
            if start:
                existing = person_dates.get(person, "")
                if not existing or start > existing:
                    person_dates[person] = start

    individuals = {k: v for k, v in person_stores.items() if _is_individual(k)}

    # Single-store owners
    cols_single = ["#", "start_date", "owner", "store", "registration_number", "address", "suburb", "postcode"]
    singles = []
    for person, stores in individuals.items():
        if len(stores) != 1:
            continue
        date = person_dates.get(person, "")
        store = list(stores)[0]
        singles.append((date, person, store))
    singles.sort(key=lambda x: x[0], reverse=True)

    rows = [cols_single]
    for i, (date, person, store) in enumerate(singles, 1):
        p = store_lookup.get(store, {})
        rows.append([str(i), date or "", person, store, p.get("registration_number", ""),
                     _get_address(p), p.get("suburb", ""), p.get("postcode", "")])
    _write_tab(book, "Non-CWH Single Owners", cols_single, rows)
    log.info("tab_rebuilt", tab="Non-CWH Single Owners", records=len(singles))

    # Multi-store owners (2, 3, 4, 5)
    cols_multi = ["#", "owner", "stores", "store_name", "registration_number", "start_date", "address", "suburb", "postcode"]
    for count in [2, 3, 4, 5]:
        tab_name = f"Non-CWH {count}-Store Owners"
        matching = {k: v for k, v in individuals.items() if len(v) == count}
        sorted_owners = sorted(matching.keys(), key=lambda k: person_dates.get(k, ""), reverse=True)

        rows = [cols_multi]
        i = 1
        for owner in sorted_owners:
            stores = sorted(matching[owner],
                          key=lambda s: store_lookup.get(s, {}).get("start_date", ""), reverse=True)
            for j, store in enumerate(stores):
                p = store_lookup.get(store, {})
                rows.append([
                    str(i) if j == 0 else "",
                    owner if j == 0 else "",
                    str(count) if j == 0 else "",
                    store,
                    p.get("registration_number", ""),
                    p.get("start_date", ""),
                    _get_address(p),
                    p.get("suburb", ""),
                    p.get("postcode", ""),
                ])
            i += 1

        _write_tab(book, tab_name, cols_multi, rows)
        log.info("tab_rebuilt", tab=tab_name, records=len(sorted_owners))
