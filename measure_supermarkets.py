"""Measure the floor area (m2) of NSW supermarkets from OpenStreetMap geometry.

Outside-the-box: OSM building/shop outlines are already traced from satellite imagery,
so a polygon's area IS the measured footprint (car parks are separate polygons and are
excluded). We enrich poi_data.json (produced by fetch_pois.py) with:

  sqm  : int | None   ground-floor footprint in square metres
  asrc : str  | None   provenance -> "osm_store" | "osm_building"

Resolution order per store:
  1. store element is a polygon        -> its footprint            (osm_store)
  2. store is a point inside a mall     -> None (can't isolate in-centre store)
  3. store is a point, standalone       -> smallest enclosing building (osm_building)
  4. otherwise                          -> None

Metric is ground-floor FOOTPRINT, not footprint x levels: building:levels reflects the
whole building (e.g. apartments above ground-floor retail), not the store.

Fetching is split so no single Overpass request is heavy: one light query for
supermarket + mall geometry, then small BATCHED queries for buildings around only the
standalone point-stores that need them.

Usage:  python fetch_pois.py  ->  python measure_supermarkets.py  ->  python generate_map.py
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fetch_pois import supermarket_brand  # reuse the canonical brand matcher

POI = Path("poi_data.json")
ENDPOINTS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Light query: supermarket geometry + shopping-centre polygons only.
QUERY_STORES = """
[out:json][timeout:180];
area["ISO3166-2"="AU-NSW"]->.nsw;
( nwr["shop"="supermarket"](area.nsw); nwr["shop"="wholesale"](area.nsw); )->.sm;
( way["shop"="mall"](area.nsw); relation["shop"="mall"](area.nsw); )->.malls;
.sm out geom tags;
.malls out geom tags;
"""

MIN_AREA, MAX_AREA = 150.0, 25000.0  # plausible standalone-supermarket footprint (m2)
MATCH_RADIUS_M = 60.0                # attach an OSM measurement to a poi within this distance
BUILDING_RADIUS_M = 50               # search buildings within this of a point-store
BATCH = 40                           # point-stores per building query
BATCH_PAUSE_S = 3.0                  # gap between building batches (avoid rate limiting)
RETRIES = 4                          # per-batch retries on 429/timeout, with backoff


def overpass(query: str, timeout: int = 200) -> dict:
    payload = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for url in ENDPOINTS:
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"User-Agent": "PharmaSite/1.0 (NSW supermarket footprints; info@ch-legal.com.au)"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  endpoint failed ({url}): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    raise RuntimeError(f"all Overpass endpoints failed: {last}")


def _proj(coords):
    lat0 = sum(c[0] for c in coords) / len(coords)
    k = math.cos(math.radians(lat0))
    return [(lon * 111320.0 * k, lat * 110540.0) for lat, lon in coords]


def ring_area_m2(coords) -> float:
    if len(coords) < 3:
        return 0.0
    pts = _proj(coords)
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def way_coords(el):
    return [(g["lat"], g["lon"]) for g in el.get("geometry", []) if g.get("lat") is not None]


def relation_rings(el):
    outer, inner = [], []
    for m in el.get("members", []):
        if m.get("type") != "way" or not m.get("geometry"):
            continue
        ring = [(g["lat"], g["lon"]) for g in m["geometry"] if g.get("lat") is not None]
        if len(ring) >= 3:
            (inner if m.get("role") == "inner" else outer).append(ring)
    return outer, inner


def element_area_and_point(el):
    if el["type"] == "way":
        coords = way_coords(el)
        if len(coords) < 3:
            return None, None
        lat = sum(c[0] for c in coords) / len(coords)
        lon = sum(c[1] for c in coords) / len(coords)
        return ring_area_m2(coords), (lat, lon)
    if el["type"] == "relation":
        outer, inner = relation_rings(el)
        if not outer:
            return None, None
        area = sum(ring_area_m2(r) for r in outer) - sum(ring_area_m2(r) for r in inner)
        allc = [c for r in outer for c in r]
        lat = sum(c[0] for c in allc) / len(allc)
        lon = sum(c[1] for c in allc) / len(allc)
        return (area if area > 0 else None), (lat, lon)
    return None, None


def point_in_ring(lat, lon, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def bbox(ring):
    lats = [c[0] for c in ring]
    lons = [c[1] for c in ring]
    return min(lats), min(lons), max(lats), max(lons)


def metres_between(a, b) -> float:
    lat0 = math.radians((a[0] + b[0]) / 2)
    dx = (a[1] - b[1]) * 111320.0 * math.cos(lat0)
    dy = (a[0] - b[0]) * 110540.0
    return math.hypot(dx, dy)


def smallest_containing_area(lat, lon, buildings):
    """buildings: list of (bbox, ring). Return smallest plausible containing footprint."""
    best = None
    for (blo_lat, blo_lon, bhi_lat, bhi_lon), ring in buildings:
        if not (blo_lat <= lat <= bhi_lat and blo_lon <= lon <= bhi_lon):
            continue
        if point_in_ring(lat, lon, ring):
            a = ring_area_m2(ring)
            if MIN_AREA <= a <= MAX_AREA and (best is None or a < best):
                best = a
    return best


def fetch_buildings_for(points):
    """Batched building lookup. points: [(lat,lon),...]. Returns list of (bbox, ring)."""
    rings = []
    seen_ids = set()
    total = (len(points) + BATCH - 1) // BATCH
    for bi in range(0, len(points), BATCH):
        chunk = points[bi:bi + BATCH]
        arounds = "".join(
            f'way(around:{BUILDING_RADIUS_M},{lat},{lon})["building"];' for lat, lon in chunk
        )
        q = f"[out:json][timeout:120];({arounds});out geom tags;"
        print(f"  buildings batch {bi // BATCH + 1}/{total} ({len(chunk)} points)...")
        data = None
        for attempt in range(RETRIES):
            try:
                data = overpass(q, timeout=140)
                break
            except RuntimeError as e:
                wait = 8 * (attempt + 1)  # 8s, 16s, 24s backoff — lets rate limit reset
                print(f"    attempt {attempt + 1}/{RETRIES} failed ({str(e)[:60]}); waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
        if data is None:
            print("    batch failed after retries, skipping", file=sys.stderr)
            continue
        for el in data.get("elements", []):
            if el.get("type") != "way" or el.get("id") in seen_ids:
                continue
            ring = way_coords(el)
            if len(ring) >= 3:
                seen_ids.add(el["id"])
                rings.append((bbox(ring), ring))
        time.sleep(BATCH_PAUSE_S)  # be polite to the public endpoint
    return rings


def main() -> int:
    if not POI.exists():
        raise SystemExit("poi_data.json not found — run fetch_pois.py first")
    pois = json.loads(POI.read_text(encoding="utf-8"))

    print("querying OpenStreetMap (Overpass) for supermarket + mall geometry...")
    data = overpass(QUERY_STORES, timeout=200)
    elements = data.get("elements", [])

    supers, malls = [], []
    for el in elements:
        tags = el.get("tags", {})
        shop = tags.get("shop")
        if shop in ("supermarket", "wholesale"):
            supers.append(el)
        elif shop == "mall":
            if el["type"] == "way":
                r = way_coords(el)
                if len(r) >= 3:
                    malls.append((bbox(r), r))
            elif el["type"] == "relation":
                outer, _ = relation_rings(el)
                for r in outer:
                    malls.append((bbox(r), r))
    print(f"  supermarkets={len(supers)}  mall polygons={len(malls)}")

    def in_mall(lat, lon) -> bool:
        for (blo_lat, blo_lon, bhi_lat, bhi_lon), ring in malls:
            if blo_lat <= lat <= bhi_lat and blo_lon <= lon <= bhi_lon and point_in_ring(lat, lon, ring):
                return True
        return False

    # First pass: polygon stores measured directly; collect standalone point-stores.
    measured = []           # (lat, lon, brand, sqm, asrc)
    need_building = []      # (index-into-measured, lat, lon)
    for el in supers:
        brand = supermarket_brand(el.get("tags", {}))
        if not brand:
            continue
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
            if lat is None or lon is None:
                continue
            if in_mall(lat, lon):
                measured.append((lat, lon, brand, None, None))
            else:
                need_building.append((len(measured), lat, lon))
                measured.append((lat, lon, brand, None, None))  # placeholder, filled below
        else:
            area, pt = element_area_and_point(el)
            if pt is None:
                continue
            if area and MIN_AREA <= area <= MAX_AREA:
                measured.append((pt[0], pt[1], brand, round(area), "osm_store"))
            else:
                measured.append((pt[0], pt[1], brand, None, None))

    # Second pass: batched building lookup for standalone point-stores.
    if need_building:
        print(f"resolving {len(need_building)} standalone point-stores via building footprints...")
        buildings = fetch_buildings_for([(lat, lon) for _, lat, lon in need_building])
        print(f"  fetched {len(buildings)} candidate building polygons")
        for idx, lat, lon in need_building:
            a = smallest_containing_area(lat, lon, buildings)
            if a:
                m = measured[idx]
                measured[idx] = (m[0], m[1], m[2], round(a), "osm_building")

    # Attach to poi_data.json supermarket entries by nearest same-brand OSM point.
    attached = 0
    for p in pois:
        if p.get("c") != "supermarket":
            continue
        p_pt = (p["lat"], p["lng"])
        best, best_d = None, MATCH_RADIUS_M
        for lat, lon, brand, sqm, asrc in measured:
            if brand != p.get("b"):
                continue
            d = metres_between(p_pt, (lat, lon))
            if d <= best_d:
                best_d, best = d, (sqm, asrc)
        if best is not None:
            p["sqm"], p["asrc"] = best
            if best[0] is not None:
                attached += 1
        else:
            p.setdefault("sqm", None)
            p.setdefault("asrc", None)

    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=2), encoding="utf-8")

    sm = [p for p in pois if p.get("c") == "supermarket"]
    have = [p for p in sm if p.get("sqm")]
    from collections import Counter
    by_src = Counter(p.get("asrc") for p in have)
    print(f"\nmeasured {len(have)}/{len(sm)} supermarkets ({100 * len(have) // max(len(sm), 1)}%)")
    print("  by source: " + ", ".join(f"{k}={v}" for k, v in by_src.items()))
    for b in ["Woolworths", "Coles", "ALDI", "IGA", "Costco", "Harris Farm"]:
        bs = [p for p in sm if p.get("b") == b]
        bh = sorted(p["sqm"] for p in bs if p.get("sqm"))
        if bs:
            med = bh[len(bh) // 2] if bh else 0
            print(f"  {b:12} {len(bh):3}/{len(bs):3} measured   median {med} m2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
