"""Hybrid Google Places enrichment of the supermarket list.

OSM stays the base (it carries the building footprints GLA needs). Places is used to:
  1. NAME  - replace generic OSM names ("Coles", "IGA") with the real branch name
             ("Coles Dubbo Southlakes", "Ashcroft's IGA") when a same-brand Places store sits
             right on the OSM point.
  2. GAPS  - add MAJOR-brand supermarkets that Places knows and OSM missed (common in regional
             towns). Only recognised major brands are added, so ethnic/convenience grocers that
             Places tags "supermarket" (which would skew the >=2,500 m2 pharmacy-rule analysis)
             are NOT added.

Runs after fetch_pois.py, before the GLA steps (new stores then get measured/estimated too).
Resumable: per-point Places results are cached in the scratchpad so re-runs don't re-bill.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import httpx

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
NEARBY = "https://places.googleapis.com/v1/places:searchNearby"
POI = Path("poi_data.json")
# Cache dir: POI_CACHE_DIR (set in CI so the cache persists in-repo), else the local temp dir.
CACHE = Path(os.environ.get("POI_CACHE_DIR") or os.environ.get("TEMP", ".")) / "places_super_cache.json"

SEED_RADIUS = 250          # m: look this far around each OSM store for Places supermarkets
NAME_SNAP_M = 90           # rename only if a same-brand Places store is this close to the OSM point
ADD_MIN_GAP_M = 220        # add a Places major only if this far from every OSM store (not a dup)

# name substring -> canonical brand. Order matters (check longer/specific first).
BRAND_KEYS = [
    ("woolworths", "Woolworths"), ("coles", "Coles"), ("aldi", "ALDI"),
    ("costco", "Costco"), ("harris farm", "Harris Farm"), ("iga", "IGA"),
]
# brands we will ADD as new stores (recognised majors only — keeps small grocers out)
ADDABLE = {"Woolworths", "Coles", "ALDI", "Costco", "Harris Farm", "IGA"}


def brand_of(name: str) -> str | None:
    n = (name or "").lower()
    for key, brand in BRAND_KEYS:
        if key in n:
            return brand
    return None


def hav(a, b, c, d):
    R = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def nearby(client, lat, lng, radius=SEED_RADIUS):
    r = client.post(
        NEARBY,
        headers={"X-Goog-Api-Key": API_KEY,
                 "X-Goog-FieldMask": "places.displayName,places.location,places.primaryType,places.types"},
        json={"includedTypes": ["supermarket"], "maxResultCount": 20,
              "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": radius}}},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("places", []):
        loc = p["location"]
        out.append({"name": p.get("displayName", {}).get("text", ""),
                    "lat": loc["latitude"], "lng": loc["longitude"],
                    "ptype": p.get("primaryType", "")})
    return out


def main():
    pois = json.loads(POI.read_text(encoding="utf-8"))
    supers = [p for p in pois if p.get("c") == "supermarket"]
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    client = httpx.Client()

    seen = {}          # dedup Places stores across all calls: (round lat,lng) -> store

    # Also sweep around shopping centres (malls) — the commonest place OSM misses a major
    # supermarket (an in-centre Woolworths/Coles traced only as part of the mall). Wider radius.
    malls = [p for p in pois if p.get("c") == "mall"]
    print(f"sweeping {len(malls)} shopping centres for in-centre majors OSM missed...", flush=True)
    for j, m in enumerate(malls, 1):
        key = "mall:%.5f,%.5f" % (m["lat"], m["lng"])
        if key in cache:
            hits = cache[key]
        else:
            try:
                hits = nearby(client, m["lat"], m["lng"], radius=400)
            except Exception:
                hits = []
            cache[key] = hits
            time.sleep(0.04)
        for h in hits:
            seen[(round(h["lat"], 5), round(h["lng"], 5))] = h
        if j % 150 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  malls {j}/{len(malls)}  distinct Places stores seen {len(seen)}", flush=True)

    renamed = 0
    print(f"enriching {len(supers)} supermarkets via Places nearby...", flush=True)
    for i, p in enumerate(supers, 1):
        key = "%.5f,%.5f" % (p["lat"], p["lng"])
        if key in cache:
            hits = cache[key]
        else:
            try:
                hits = nearby(client, p["lat"], p["lng"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    time.sleep(20)
                    try:
                        hits = nearby(client, p["lat"], p["lng"])
                    except Exception:
                        hits = []
                else:
                    hits = []
            except Exception:
                hits = []
            cache[key] = hits
            time.sleep(0.04)

        for h in hits:
            seen[(round(h["lat"], 5), round(h["lng"], 5))] = h

        # rename: nearest same-brand Places store within snap distance
        my_brand = p.get("b")
        best = None
        for h in hits:
            d = hav(p["lat"], p["lng"], h["lat"], h["lng"])
            if d <= NAME_SNAP_M and brand_of(h["name"]) == my_brand and h["name"]:
                if best is None or d < best[0]:
                    best = (d, h["name"])
        if best and best[1] != p.get("n"):
            p["n"] = best[1]
            p["nsrc"] = "places"
            renamed += 1
        if i % 150 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(supers)}  renamed {renamed}, distinct Places stores seen {len(seen)}", flush=True)

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    client.close()

    # GAPS: add recognised-major Places stores that are far from every OSM supermarket
    osm_pts = [(p["lat"], p["lng"]) for p in supers]
    added = 0
    add_brands = {}
    for (la, lo), h in seen.items():
        brand = brand_of(h["name"])
        if brand not in ADDABLE:
            continue                                   # skip ethnic/convenience/unknown grocers
        if any(hav(la, lo, a, b) <= ADD_MIN_GAP_M for a, b in osm_pts):
            continue                                   # already have this store from OSM
        pois.append({"n": h["name"], "lat": la, "lng": lo, "c": "supermarket",
                     "b": brand, "src": "places"})
        osm_pts.append((la, lo))                       # so we don't add two Places dups of each other
        added += 1
        add_brands[brand] = add_brands.get(brand, 0) + 1

    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrenamed {renamed} to branch names; added {added} major supermarkets OSM missed", flush=True)
    if add_brands:
        print("  added by brand: " + ", ".join(f"{k}={v}" for k, v in sorted(add_brands.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
