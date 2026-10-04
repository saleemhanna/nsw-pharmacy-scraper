"""Measure supermarket GLA for stores OSM never traced, using Microsoft's ML-derived
Global Building Footprints (open, satellite-derived — covers buildings OSM misses).

Runs AFTER gla_supermarkets.py. For every supermarket still flagged 'estimate', find the
Microsoft building at its location and use that footprint (subject to the same plausibility
cap, so a mall building can't slip in). Standalone OSM-gap stores (e.g. Khan Super IGA) get
a real measurement; genuinely in-mall stores have only the centre building available, so the
cap keeps them as estimates.

Pure Python (no geo deps) — reuses the point-in-polygon + shoelace-area helpers.
Tiles are cached under the session temp dir so re-runs don't re-download.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import urllib.request
from collections import defaultdict
from pathlib import Path

from measure_supermarkets import point_in_ring, ring_area_m2, metres_between

POI = Path("poi_data.json")
LINKS = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
CACHE = Path(os.environ.get("POI_CACHE_DIR") or os.environ.get("TEMP", ".")) / "msf_cache"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "PharmaSite/1.0 (NSW supermarket footprints; info@ch-legal.com.au)"}
Z = 9
NEAR_M = 60          # if no building contains the (possibly offset) store node, nearest within this


def quadkey(lat, lon, z=Z):
    s = math.sin(lat * math.pi / 180)
    x = (lon + 180) / 360
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    n = 2 ** z
    tx = max(0, min(n - 1, int(x * n)))
    ty = max(0, min(n - 1, int(y * n)))
    q = ""
    for i in range(z, 0, -1):
        d = 0
        m = 1 << (i - 1)
        if tx & m:
            d += 1
        if ty & m:
            d += 2
        q += str(d)
    return q


def au_links():
    cache = CACHE / "au_links.csv"
    if not cache.exists():
        print("downloading Australia building-tile index...")
        txt = urllib.request.urlopen(urllib.request.Request(LINKS, headers=UA), timeout=180).read().decode()
        rows = [r for r in csv.DictReader(io.StringIO(txt)) if r["Location"] == "Australia"]
        cache.write_text(json.dumps({r["QuadKey"]: r["Url"] for r in rows}), encoding="utf-8")
    return json.loads(cache.read_text(encoding="utf-8"))


def tile_path(qk):
    return CACHE / f"{qk}.csv.gz"


def fetch_tile(qk, url):
    p = tile_path(qk)
    if p.exists() and p.stat().st_size > 0:
        return p
    print(f"  downloading tile {qk} ...", flush=True)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        p.write_bytes(r.read())
    return p


def measure_tile(qk, stores):
    """stores: [(idx, lat, lon, cap)]. Returns {idx: sqm} from MS building footprints."""
    p = tile_path(qk)
    best = {}          # idx -> smallest containing footprint
    near = {}          # idx -> (dist, area) nearest building centroid (fallback)
    # coarse bbox around this tile's stores to skip far-away buildings cheaply
    pad = 0.0008
    boxes = [(idx, lat, lon, cap, lat - pad, lat + pad, lon - pad, lon + pad) for idx, lat, lon, cap in stores]
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().rstrip(",")
            if not line or line[0] != "{":
                continue
            try:
                coords = json.loads(line)["geometry"]["coordinates"][0]
            except Exception:
                continue
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            blo_lat, bhi_lat, blo_lon, bhi_lon = min(lats), max(lats), min(lons), max(lons)
            ring = None
            for idx, lat, lon, cap, slo, shi, wlo, whi in boxes:
                if bhi_lat < slo or blo_lat > shi or bhi_lon < wlo or blo_lon > whi:
                    continue
                if ring is None:
                    ring = [(c[1], c[0]) for c in coords]      # -> (lat, lon)
                    area = ring_area_m2(ring)
                    clat = sum(lats) / len(lats)
                    clon = sum(lons) / len(lons)
                if not (150 <= area <= cap):
                    continue
                if blo_lat <= lat <= bhi_lat and blo_lon <= lon <= bhi_lon and point_in_ring(lat, lon, ring):
                    if idx not in best or area < best[idx]:
                        best[idx] = area
                else:
                    d = metres_between((lat, lon), (clat, clon))
                    if d <= NEAR_M and (idx not in near or d < near[idx][0]):
                        near[idx] = (d, area)
    out = {}
    for idx, _, _, _ in stores:
        if idx in best:
            out[idx] = round(best[idx])
        elif idx in near:
            out[idx] = round(near[idx][1])
    return out


def main():
    pois = json.loads(POI.read_text(encoding="utf-8"))
    todo = [(i, p) for i, p in enumerate(pois)
            if p.get("c") == "supermarket" and p.get("asrc") != "measured"]
    print(f"{len(todo)} supermarkets still estimated — trying Microsoft footprints")
    links = au_links()

    by_qk = defaultdict(list)
    for i, p in todo:
        cap = 16000 if p.get("b") == "Costco" else 5500
        by_qk[quadkey(p["lat"], p["lng"])].append((i, p["lat"], p["lng"], cap))

    upgraded = 0
    qks = [qk for qk in by_qk if qk in links]
    print(f"{len(by_qk)} tiles needed, {len(qks)} available in Australia dataset")
    for n, qk in enumerate(qks, 1):
        try:
            fetch_tile(qk, links[qk])
            got = measure_tile(qk, by_qk[qk])
        except Exception as e:  # noqa: BLE001
            print(f"  tile {qk} failed: {str(e)[:70]}")
            continue
        for idx, sqm in got.items():
            pois[idx]["sqm"], pois[idx]["asrc"] = sqm, "measured"
        upgraded += len(got)
        POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")  # save per tile (resumable)
        print(f"  [{n}/{len(qks)}] tile {qk}: +{len(got)} measured (total +{upgraded})", flush=True)

    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")
    sm = [p for p in pois if p.get("c") == "supermarket"]
    meas = sum(1 for p in sm if p.get("asrc") == "measured")
    print(f"\nGLA now: {meas}/{len(sm)} measured ({100 * meas // len(sm)}%), {len(sm) - meas} still estimate")


if __name__ == "__main__":
    main()
