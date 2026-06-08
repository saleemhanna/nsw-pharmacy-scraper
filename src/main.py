from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

from src.api import NSWApiClient
from src.diff import compute_diffs
from src.models import Pharmacy
from src.tabs import rebuild_all_tabs
from src.sheets import (
    append_changes,
    append_run,
    open_sheet,
    pharmacy_to_dict_for_diff,
    read_previous_current,
    utc_now_iso,
    write_current,
)

log = structlog.get_logger()

DEFAULT_SHEET_ID = "1502YpdciO9NSeyzBEr6wf75Y5Gpan-qPXw0elKHve0c"
SNAPSHOT_PATH = Path("snapshot.json")
ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
POLITE_DELAY = 5.0
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

STRIP_PATTERNS = [
    re.compile(r"^Shops?\s+[\d&/\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^SHOP\s+[\d&/\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^Suites?\s+[\w./&\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^Units?\s+[\d&/\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^Lots?\s+[\d&/\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^Rooms?\s+[\d&/\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^Tenancy\s+[\d&/\s-]+,?\s*", re.IGNORECASE),
    re.compile(r"^Ground\s+Floor,?\s*", re.IGNORECASE),
    re.compile(r"^Level\s+[\dA-Z]+,?\s*", re.IGNORECASE),
    re.compile(r"^MB\d+\s+", re.IGNORECASE),
    re.compile(r"^\d+[A-Z]?\s+"),
    re.compile(r"^Pharmacy\s+Shop,?\s*", re.IGNORECASE),
]
SHOPPING_CENTRE_RE = re.compile(
    r"(?:Westfield\s+[\w\s]+?,|Stockland\s+[\w\s]+?,|Castle\s+Towers\s+[\w\s]*?,|"
    r"Myer\s+Centrepoint,|"
    r"[\w\s]+?(?:Shopping\s+Centre|Shopping\s+Village|Interchange|Marketplace|"
    r"Central\s+Shopping|Plaza|Arcade|Mall))\s*,?\s*",
    re.IGNORECASE,
)
EXTRA_STRIP = [
    re.compile(r"^(?:Departure|Arrival)\s+Level\s+", re.IGNORECASE),
    re.compile(r"^Primary\s+Medical\s+Centre\s+\w+\s+", re.IGNORECASE),
    re.compile(r"^Concourse\s+", re.IGNORECASE),
]


def _clean_address(raw: str) -> str:
    addr = raw.strip()
    for pat in STRIP_PATTERNS:
        addr = pat.sub("", addr).strip()
    addr = re.sub(r"^[\d/&\s-]+,?\s*", "", addr).strip()
    addr = SHOPPING_CENTRE_RE.sub("", addr).strip()
    for pat in EXTRA_STRIP:
        addr = pat.sub("", addr).strip()
    addr = re.sub(r"^,\s*", "", addr).strip()
    addr = re.sub(r"\bCnr\b", "Corner", addr)
    if not re.search(r"\d", addr):
        addr = raw.strip()
    return addr


def _geocode(client: httpx.Client, address: str) -> tuple[float, float]:
    params = {"q": address, "format": "json", "countrycodes": "au", "limit": 1}
    try:
        r = client.get(NOMINATIM_URL, params=params, headers={
            "User-Agent": "nsw-pharmacy-scraper/0.2 (contact: info@ch-legal.com.au)"
        })
        r.raise_for_status()
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return 0.0, 0.0


async def _collect_all(client: NSWApiClient) -> dict[str, dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    queue = list(ALPHA.strip())
    visited: set[str] = set()

    while queue:
        term = queue.pop(0)
        if term in visited:
            continue
        visited.add(term)

        try:
            results, exceeded = await client.search_by_name(term)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                log.warning("rate_limited", term=term)
                await asyncio.sleep(30)
                queue.insert(0, term)
                continue
            raise

        for rec in results:
            reg = rec.get("licenceNumber", "")
            if reg and reg not in seen:
                seen[reg] = rec

        log.info("search_done", term=term, results=len(results), exceeded=exceeded, unique=len(seen))

        if exceeded and len(term) < 4:
            for ch in ALPHA:
                sub = term + ch
                if sub not in visited:
                    queue.append(sub)

        await asyncio.sleep(POLITE_DELAY)

    return seen


async def run() -> int:
    sheet_id = os.environ.get("SHEET_ID") or DEFAULT_SHEET_ID
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

    started = utc_now_iso()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    status = "success"
    notes = ""
    total = 0
    new_count = 0
    changes_count = 0

    try:
        # Load previous snapshot for smart caching
        prev_pharmacies: dict[str, Pharmacy] = {}
        if SNAPSHOT_PATH.exists():
            prev_data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8-sig"))
            for raw in prev_data:
                p = Pharmacy(**{**raw, "start_date": raw.get("start_date", "")})
                prev_pharmacies[p.registration_number] = p
            log.info("loaded_previous", count=len(prev_pharmacies))

        # Phase 1: name-based search for all pharmacies
        log.info("phase1_search")
        async with NSWApiClient() as client:
            all_raw = await _collect_all(client)
        log.info("phase1_done", found=len(all_raw))

        # Parse and identify new/changed
        pharmacies: dict[str, Pharmacy] = {}
        need_detail: list[Pharmacy] = []

        for reg, raw in all_raw.items():
            p = Pharmacy.from_api(raw)
            prev = prev_pharmacies.get(reg)
            if prev and prev.licence_id == p.licence_id and prev.owners:
                p.start_date = prev.start_date
                p.owners = prev.owners
                p.financial_interests = prev.financial_interests
                p.premises_details = prev.premises_details
                p.latitude = prev.latitude
                p.longitude = prev.longitude
                pharmacies[reg] = p
            else:
                need_detail.append(p)
                pharmacies[reg] = p

        log.info("need_detail_fetch", new_or_changed=len(need_detail), cached=len(pharmacies) - len(need_detail))

        # Phase 2: fetch details for new/changed only
        if need_detail:
            async with NSWApiClient() as client:
                for i, pharm in enumerate(need_detail, 1):
                    if not pharm.licence_id:
                        continue
                    try:
                        detail = await client.fetch_detail(pharm.licence_type, pharm.licence_id)
                        pharm.enrich_from_detail(detail)
                    except Exception as e:
                        log.error("detail_failed", reg=pharm.registration_number, error=str(e))
                    await asyncio.sleep(POLITE_DELAY)
                    if i % 50 == 0:
                        log.info("detail_progress", done=i, total=len(need_detail))

        # Phase 3: geocode new/changed addresses
        needs_geocode = [p for p in need_detail if p.premises_details and p.latitude == 0.0]
        if needs_geocode:
            log.info("geocoding", count=len(needs_geocode))
            geo_client = httpx.Client(timeout=30)
            for p in needs_geocode:
                raw_addr = p.premises_details.rsplit(" (", 1)[0].strip()
                cleaned = _clean_address(raw_addr)
                for attempt in [cleaned, raw_addr, f"{p.address}, {p.suburb} NSW {p.postcode}"]:
                    if not attempt:
                        continue
                    lat, lon = _geocode(geo_client, attempt)
                    time.sleep(1.1)
                    if lat != 0.0:
                        p.latitude = lat
                        p.longitude = lon
                        break
            geo_client.close()

        # Cull expired
        result_list = [p for p in pharmacies.values() if not (p.expiry_date and p.expiry_date[:10] < today)]
        expired = len(pharmacies) - len(result_list)
        if expired:
            log.info("culled_expired", count=expired)

        new_count = len(need_detail)
        total = len(result_list)

        # Save snapshot
        SNAPSHOT_PATH.write_text(
            json.dumps([p.model_dump() for p in result_list], indent=2, sort_keys=True),
            encoding="utf-8",
        )

        # Write to sheet
        book = open_sheet(sheet_id, sa_json)
        previous = read_previous_current(book)
        current_rows = [pharmacy_to_dict_for_diff(p, started) for p in result_list]
        changes = compute_diffs(previous, current_rows)
        changes_count = len(changes)
        write_current(book, result_list, started)
        append_changes(book, changes, started)
        rebuild_all_tabs(book, result_list)

        log.info("scrape_complete", records=total, new=new_count, changes=changes_count, expired=expired)
        notes = f"new/changed: {new_count}, expired culled: {expired}"

    except Exception as e:
        status = "failed"
        notes = str(e)
        log.exception("run_failed")

    finished = utc_now_iso()
    try:
        book = open_sheet(sheet_id, sa_json)
        append_run(book, {
            "started_at": started,
            "finished_at": finished,
            "postcodes_queried": 0,
            "postcodes_with_results": 0,
            "records_scraped": total,
            "records_failed": 0,
            "duplicates_dropped": 0,
            "status": status,
            "notes": notes,
        })
    except Exception:
        log.exception("run_log_failed")

    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
