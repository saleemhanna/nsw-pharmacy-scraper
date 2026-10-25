"""Catch NEW major supermarkets that OpenStreetMap hasn't mapped yet (e.g. stores in
brand-new suburbs like Melonba), which the OSM-anchored enrich step misses.

Sweeps a coarse grid over NSW's populated growth corridors with Google Places
Nearby search, keeps the recognised major chains, and MERGES any that are far enough
from every store already in poi_data.json (i.e. genuinely new). Run AFTER enrich_places.

Cost: grid is deliberately coarse (~100 cells) so recurring Google spend stays small
(~a few dollars per fortnightly run). Not cached, so new openings are caught each run.

Usage:  python sweep_new_supermarkets.py   (needs GOOGLE_PLACES_API_KEY)
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

POI = Path("poi_data.json")
API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
NEARBY = "https://places.googleapis.com/v1/places:searchNearby"

# Populated NSW growth corridors (where new suburbs/stores appear). bbox = (min_lat,min_lng,max_lat,max_lng)
REGIONS: list[tuple[float, float, float, float]] = [
    (-34.20, 150.60, -33.55, 151.35),   # Greater Sydney (incl. NW & SW growth corridors)
    (-33.55, 151.15, -33.20, 151.60),   # Central Coast
    (-33.05, 151.30, -32.60, 151.85),   # Newcastle / Lake Macquarie / Maitland
    (-34.75, 150.70, -34.30, 151.00),   # Illawarra (Wollongong / Shellharbour)
]
STEP_LAT = 0.072   # ~8 km
STEP_LNG = 0.086   # ~8 km at Sydney latitude
RADIUS_M = 6000    # covers the 8 km cell (with overlap)
ADD_MIN_GAP_M = 250

BRAND_KEYS = [
    ("woolworths", "Woolworths"), ("coles", "Coles"), ("aldi", "ALDI"),
    ("costco", "Costco"), ("harris farm", "Harris Farm"), ("iga", "IGA"),
]


# Non-retail / small-format listings that Places returns under "supermarket" but which
# are not full-line major supermarkets — excluded from the layer.
_EXCLUDE = [
    "distribution", "online", "ocado", "fulfil", "warehouse", "head office",
    "x-press", "xpress", "express", "spar", "petrol", "fuel", "caltex", "ampol",
]


def is_full_supermarket(name: str) -> bool:
    n = (name or "").lower()
    return not any(k in n for k in _EXCLUDE)


def brand_of(name: str) -> str | None:
    n = (name or "").lower()
    for key, brand in BRAND_KEYS:
        if key in n:
            return brand
    return None


def hav_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(x))


def grid_cells(
    bbox: tuple[float, float, float, float], step_lat: float = STEP_LAT, step_lng: float = STEP_LNG
) -> list[tuple[float, float]]:
    """Grid-cell centre points covering a bounding box."""
    min_lat, min_lng, max_lat, max_lng = bbox
    cells: list[tuple[float, float]] = []
    lat = min_lat
    while lat <= max_lat + 1e-9:
        lng = min_lng
        while lng <= max_lng + 1e-9:
            cells.append((round(lat, 5), round(lng, 5)))
            lng += step_lng
        lat += step_lat
    return cells


def merge_new(
    existing: list[dict[str, Any]],
    found: list[dict[str, Any]],
    min_gap_m: float = ADD_MIN_GAP_M,
    same_brand_gap_m: float = 500,
) -> list[dict[str, Any]]:
    """Return genuinely-new, de-duplicated major supermarkets from `found`.

    A found store is kept only if it is a full-line major (branded + not a
    non-retail/convenience format), is >= min_gap_m from ANY existing store, is
    >= same_brand_gap_m from every existing store of the SAME brand (guards against
    the same store pinned imprecisely), and isn't a duplicate of one already kept.
    """
    new: list[dict[str, Any]] = []
    for f in found:
        if not f.get("b") or not is_full_supermarket(f.get("n", "")):
            continue
        if any(hav_m(f["lat"], f["lng"], e["lat"], e["lng"]) < min_gap_m for e in existing):
            continue
        if any(
            e.get("b") == f["b"] and hav_m(f["lat"], f["lng"], e["lat"], e["lng"]) < same_brand_gap_m
            for e in existing
        ):
            continue
        if any(hav_m(f["lat"], f["lng"], a["lat"], a["lng"]) < min_gap_m for a in new):
            continue
        new.append(f)
    return new


def search_cell(client: httpx.Client, lat: float, lng: float) -> list[dict[str, Any]]:
    r = client.post(
        NEARBY,
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "places.displayName,places.location,places.formattedAddress,places.primaryType",
        },
        json={
            "includedTypes": ["supermarket"],
            "maxResultCount": 20,
            "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": RADIUS_M}},
        },
        timeout=30,
    )
    if r.status_code == 429:
        time.sleep(20)
        return search_cell(client, lat, lng)
    r.raise_for_status()
    out: list[dict[str, Any]] = []
    for p in r.json().get("places", []):
        loc = p.get("location") or {}
        name = p.get("displayName", {}).get("text", "")
        out.append({
            "n": name, "lat": loc.get("latitude"), "lng": loc.get("longitude"),
            "b": brand_of(name), "addr": p.get("formattedAddress", ""),
        })
    return out


def main() -> int:
    if not API_KEY:
        print("ERROR: GOOGLE_PLACES_API_KEY not set", file=sys.stderr)
        return 1
    pois = __import__("json").loads(POI.read_text(encoding="utf-8")) if POI.exists() else []
    existing = [p for p in pois if p.get("c") == "supermarket"]

    cells: list[tuple[float, float]] = []
    for region in REGIONS:
        cells += grid_cells(region)
    print(f"sweeping {len(cells)} cells over {len(REGIONS)} regions for new supermarkets...", flush=True)

    found: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    with httpx.Client() as client:
        for i, (lat, lng) in enumerate(cells, 1):
            try:
                for s in search_cell(client, lat, lng):
                    if s["lat"] is None:
                        continue
                    k = (round(s["lat"], 5), round(s["lng"], 5))
                    if k in seen:
                        continue
                    seen.add(k)
                    found.append(s)
            except Exception as e:  # noqa: BLE001
                print(f"  cell {lat},{lng} failed: {type(e).__name__}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  {i}/{len(cells)} cells, {len(found)} distinct stores seen", flush=True)
            time.sleep(0.03)

    new = merge_new(existing, found)
    for f in new:
        pois.append({
            "n": f["n"], "lat": f["lat"], "lng": f["lng"], "c": "supermarket",
            "b": f["b"], "nsrc": "places-sweep", "centre": "", "addr": f["addr"],
            "suburb": "", "asrc": "none",
        })
    by_brand: dict[str, int] = {}
    for f in new:
        by_brand[f["b"]] = by_brand.get(f["b"], 0) + 1
    POI.write_text(__import__("json").dumps(pois, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"added {len(new)} new supermarkets OSM/enrich missed: {by_brand}")
    for f in new:
        print(f"  + {f['n']} — {f['addr']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
