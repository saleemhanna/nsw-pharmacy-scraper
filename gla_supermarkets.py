"""Assign a GLA (m2) to EVERY supermarket in poi_data.json.

Tier 1 - MEASURED: OSM footprint via small batched `around` queries (reliable, unlike the
                   heavy state-wide query that times out). Per store, prefer the shop=supermarket
                   polygon at the point; else the smallest building containing the point.
Tier 2 - estimate: brand's measured median (only if measurement genuinely unavailable).

Every supermarket ends with sqm (int) + asrc ('measured' | 'estimate'). Run after fetch_pois.py.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from measure_supermarkets import (
    overpass, way_coords, ring_area_m2, point_in_ring, bbox, metres_between,
    MIN_AREA, MAX_AREA,
)

POI = Path("poi_data.json")
TYPICAL = {"Woolworths": 3600, "Coles": 3500, "ALDI": 1500,
           "Costco": 13500, "IGA": 900, "Harris Farm": 1300}
BATCH = 25          # supermarket points per Overpass query (small = reliable)
RADIUS = 80         # metres to search around each point
PAUSE = 5.0         # gap between batches (polite; avoids rate limiting)
RETRIES = 5


MAX_CONSEC_FAIL = 5     # if this many batches in a row fail, Overpass is down — bail to MS/estimates


def measure_batched(points):
    """points: [(idx, lat, lon)]. Returns {idx: sqm} from OSM footprints.
    Aborts early if Overpass is persistently failing, so a dead Overpass can't wedge the
    weekly pipeline for hours (the Microsoft-footprint + estimate tiers still run after)."""
    out = {}
    total = (len(points) + BATCH - 1) // BATCH
    consec_fail = 0
    for bi in range(0, len(points), BATCH):
        chunk = points[bi:bi + BATCH]
        parts = "".join(
            f'way(around:{RADIUS},{lat},{lon})["shop"="supermarket"];'
            f'way(around:{RADIUS},{lat},{lon})["shop"="mall"];'
            f'way(around:{RADIUS},{lat},{lon})["building"];' for _, lat, lon in chunk
        )
        q = f"[out:json][timeout:90];({parts});out geom tags;"
        data = None
        for attempt in range(RETRIES):
            try:
                data = overpass(q, timeout=110)
                break
            except Exception as e:  # noqa: BLE001
                wait = 12 * (attempt + 1)
                print(f"  batch {bi // BATCH + 1}/{total} retry {attempt + 1} in {wait}s ({str(e)[:50]})")
                time.sleep(wait)
        if not data:
            consec_fail += 1
            print(f"  batch {bi // BATCH + 1}/{total} failed; leaving estimates "
                  f"(consec fail {consec_fail}/{MAX_CONSEC_FAIL})")
            if consec_fail >= MAX_CONSEC_FAIL:
                print(f"  Overpass unavailable ({consec_fail} batches failed in a row) — "
                      f"aborting OSM tier; Microsoft footprints + estimates will cover it")
                break
            time.sleep(PAUSE)
            continue
        consec_fail = 0
        # classify returned polygons: shop=supermarket (the store), shop=mall (a centre), or building
        SM, MALL, BLD = [], [], []
        for el in data.get("elements", []):
            if el.get("type") != "way":
                continue
            ring = way_coords(el)
            if len(ring) < 3:
                continue
            entry = (bbox(ring), ring, ring_area_m2(ring))
            shop = el.get("tags", {}).get("shop")
            (SM if shop == "supermarket" else MALL if shop == "mall" else BLD).append(entry)

        def contains(polys, lat, lon):
            for (blo, blolon, bhi, bhilon), ring, a in polys:
                if blo <= lat <= bhi and blolon <= lon <= bhilon and point_in_ring(lat, lon, ring):
                    yield a

        for idx, lat, lon in chunk:
            # 1) traced supermarket outline containing the point = the store's own footprint.
            sm = [a for a in contains(SM, lat, lon) if MIN_AREA <= a <= 16000]
            if sm:
                out[idx] = round(min(sm)); continue
            # 2) else the smallest building containing the point. This measures standalone AND
            #    small-centre stores (building ~= store). A big mall's footprint is caught by the
            #    plausibility cap in main() and demoted to an estimate, so it can't pollute.
            bld = [a for a in contains(BLD, lat, lon) if MIN_AREA <= a <= 16000]
            if bld:
                out[idx] = round(min(bld))
        got = sum(1 for idx, _, _ in chunk if idx in out)
        print(f"  batch {bi // BATCH + 1}/{total}: {got}/{len(chunk)} measured", flush=True)
        time.sleep(PAUSE)
    return out


def main():
    pois = json.loads(POI.read_text(encoding="utf-8"))
    supers = [p for p in pois if p.get("c") == "supermarket"]
    for p in supers:                      # clear any prior (possibly stale) measurement
        p.pop("sqm", None)
        p.pop("asrc", None)
    pts = [(i, p["lat"], p["lng"]) for i, p in enumerate(pois) if p.get("c") == "supermarket"]
    print(f"measuring footprints for {len(supers)} supermarkets in batches of {BATCH}...")

    measured = measure_batched(pts)
    # Reject implausibly large footprints (a mall traced as shop=supermarket). Only Costco
    # legitimately exceeds ~5,500 m2; everything else that big is a building/centre, not a store.
    for idx, sqm in measured.items():
        cap = 16000 if pois[idx].get("b") == "Costco" else 5500
        if sqm <= cap:
            pois[idx]["sqm"], pois[idx]["asrc"] = sqm, "measured"

    # brand medians (for any the OSM footprint genuinely couldn't resolve)
    by_brand = defaultdict(list)
    for p in supers:
        if p.get("asrc") == "measured":
            by_brand[p["b"]].append(p["sqm"])
    median = {b: (sorted(v)[len(v) // 2] if v else None) for b, v in by_brand.items()}
    for p in supers:
        if p.get("asrc") == "measured":
            continue
        p["sqm"] = int(median.get(p.get("b")) or TYPICAL.get(p.get("b")) or 1000)
        p["asrc"] = "estimate"

    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")
    meas = sum(1 for p in supers if p.get("asrc") == "measured")
    print(f"\nMEASURED {meas}/{len(supers)} ({100 * meas // max(len(supers), 1)}%); rest estimate")
    print("by source: " + ", ".join(f"{k}={v}" for k, v in Counter(p.get("asrc") for p in supers).items()))


if __name__ == "__main__":
    main()
