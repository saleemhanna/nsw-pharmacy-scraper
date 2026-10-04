"""Build the pharmacy map data from the Google Sheet 'Current' tab.

Reads the Sheet (read-only), uses the coordinates already stored there, and fills any
missing coordinates via the free NSW AddressPoint geocoder (OpenStreetMap Nominatim as a
fallback). Writes map_data.json, which generate_map.py consumes.

Does NOT modify the Sheet, and does NOT touch the scraper's snapshot.json (which src/main.py
uses as its change-detection cache). No Google API calls.

Usage:  python build_map_from_sheet.py   then   python generate_map.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

from src.sheets import open_sheet, read_previous_current

SHEET_ID = "1502YpdciO9NSeyzBEr6wf75Y5Gpan-qPXw0elKHve0c"
OUTPUT = Path("map_data.json")

NSW_ADDR_URL = (
    "https://portal.spatial.nsw.gov.au/server/rest/services/"
    "NSW_Geocoded_Addressing_Theme/MapServer/1/query"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
UA = "PharmacyMap/1.0 (NSW pharmacy locator; info@ch-legal.com.au)"

# Fields generate_map.py reads from each record.
MAP_FIELDS = [
    "trading_name", "licensee", "premises_details", "address", "suburb",
    "postcode", "owners", "financial_interests", "start_date", "registration_number",
]


def _to_float(value: object) -> float:
    try:
        return float(str(value).strip())
    except (ValueError, AttributeError):
        return 0.0


def _normalise_for_nsw(address: str) -> str:
    """Sheet address -> NSW AddressPoint 'address' shape ("HOUSENUM STREET SUBURB").

    Strips the trailing state/postcode and common retail prefixes (Shop/Unit/Tenancy),
    uppercases, and collapses whitespace so it can be used as a left-anchored LIKE prefix.
    """
    a = address.upper().replace(",", " ")
    a = re.sub(r"\s+NSW\b.*$", "", a)                      # drop ' NSW 2133' tail
    a = re.sub(r"^\s*(SHOP|UNIT|TENANCY|SUITE|LEVEL)\s+\S+\s+", "", a)  # drop 'SHOP 5 '
    return " ".join(a.split())


def geocode_nsw(client: httpx.Client, address: str) -> tuple[float, float] | None:
    prefix = _normalise_for_nsw(address).replace("'", "''")
    if not prefix:
        return None
    params = {
        "where": f"address LIKE '{prefix}%'",
        "outFields": "address",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
        "resultRecordCount": "1",
    }
    try:
        resp = client.get(NSW_ADDR_URL, params=params, timeout=20)
        feats = resp.json().get("features", [])
        if feats:
            g = feats[0]["geometry"]
            return (g["y"], g["x"])
    except Exception:
        return None
    return None


def _nominatim(client: httpx.Client, query: str) -> tuple[float, float] | None:
    try:
        resp = client.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "countrycodes": "au", "limit": "1"},
            headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20,
        )
        data = resp.json()
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        return None
    return None


def geocode_nominatim(client: httpx.Client, address: str) -> tuple[float, float] | None:
    """Street-level: geocode the full address via Nominatim."""
    return _nominatim(client, f"{address}, NSW, Australia")


def geocode_area(client: httpx.Client, postcode: str, suburb: str) -> tuple[float, float] | None:
    """Approximate fallback: postcode centroid (clean numeric field, no typos),
    then suburb. Used only when street-level geocoding fails (e.g. shopping-centre
    addresses or typo'd suburbs in the source data)."""
    pc = str(postcode).strip()
    if pc:
        coord = _nominatim(client, f"{pc}, NSW, Australia")
        if coord:
            return coord
    if suburb.strip():
        return _nominatim(client, f"{suburb}, NSW, Australia")
    return None


def main() -> int:
    try:
        book = open_sheet(SHEET_ID)
        rows = read_previous_current(book)
    except Exception as e:  # noqa: BLE001 - report any read failure and bail
        print(f"ERROR: could not read Sheet: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"read {len(rows)} rows from Sheet 'Current'")

    records: list[dict] = []
    need_geocode: list[dict] = []
    for row in rows:
        rec = {k: row.get(k, "") for k in MAP_FIELDS}
        rec["latitude"] = _to_float(row.get("latitude"))
        rec["longitude"] = _to_float(row.get("longitude"))
        records.append(rec)
        if rec["latitude"] == 0.0 or rec["longitude"] == 0.0:
            need_geocode.append(rec)

    print(f"coords from Sheet: {len(records) - len(need_geocode)}; need geocoding: {len(need_geocode)}")

    exact = 0
    approx = 0
    if need_geocode:
        with httpx.Client() as client:
            for rec in need_geocode:
                addr = (rec.get("address") or rec.get("premises_details") or "").strip()
                if not addr:
                    continue
                # Tier 1+2: street-level (authoritative / exact)
                coord = geocode_nsw(client, addr)
                if coord is None:
                    coord = geocode_nominatim(client, addr)
                    time.sleep(1.0)  # respect Nominatim's <=1 req/sec policy
                if coord:
                    rec["latitude"], rec["longitude"] = coord
                    exact += 1
                    print(f"  exact:  {addr[:55]} -> {coord[0]:.5f},{coord[1]:.5f}")
                    continue
                # Tier 3: approximate postcode/suburb centroid (last resort)
                coord = geocode_area(client, rec.get("postcode", ""), rec.get("suburb", ""))
                time.sleep(1.0)
                if coord:
                    rec["latitude"], rec["longitude"] = coord
                    rec["geo_approx"] = True
                    approx += 1
                    print(f"  approx: [{rec.get('postcode','?')}] {rec.get('trading_name','')[:40]} -> {coord[0]:.5f},{coord[1]:.5f}")

    still_missing = len(need_geocode) - exact - approx
    print(f"\ngeocoded {exact} exact + {approx} approximate (postcode centroid); still missing: {still_missing}")

    OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(records)} records, {len(records) - still_missing} mappable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
