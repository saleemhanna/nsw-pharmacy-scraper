"""Resolve + dedupe shopping centres against Google Places, and tag the major brands.

OSM tags many features per centre (the mall polygon, individual arcades, retail buildings),
so one real centre shows up as several dots. We map each OSM 'mall' POI to its canonical Google
Places shopping-centre (by place id) and collapse every OSM POI that resolves to the SAME centre
into ONE marker at the Places location, with the Places name. Genuinely distinct centres that
happen to sit close together (e.g. QVB vs The Galeries in the CBD) have different place ids, so
they stay separate.

Each surviving centre gets:
  n       - canonical Places name (falls back to the best OSM name)
  brand   - 'westfield' | 'stockland' | None  (for the logo on the map)
  aliases - every OSM/Places name seen for it (so build_poi_counts still matches store counts)
  csrc    - 'places' if Places-resolved, else 'osm'

Runs after fetch_pois.py, before build_poi_counts.py. Resumable cache in the scratchpad.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

import httpx

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
NEARBY = "https://places.googleapis.com/v1/places:searchNearby"
POI = Path("poi_data.json")
# Cache dir: POI_CACHE_DIR (set in CI so the cache persists in-repo), else the local temp dir.
CACHE = Path(os.environ.get("POI_CACHE_DIR") or os.environ.get("TEMP", ".")) / "places_centre_cache.json"

MATCH_M = 140          # an OSM mall resolves to a Places centre only if one is this close
MERGE_M = 130          # agglomerative-merge radius for arcades/sub-features/same-brand duplicates

BRANDS = [("westfield", "westfield"), ("stockland", "stockland")]


def brand_of(name: str) -> str | None:
    n = (name or "").lower()
    for key, brand in BRANDS:
        if key in n:
            return brand
    return None


def hav(a, b, c, d):
    R = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def nearby(client, lat, lng):
    r = client.post(
        NEARBY,
        headers={"X-Goog-Api-Key": API_KEY,
                 "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.primaryType"},
        json={"includedTypes": ["shopping_mall"], "maxResultCount": 10,
              "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": MATCH_M}}},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("places", []):
        loc = p["location"]
        out.append({"id": p.get("id", ""), "name": p.get("displayName", {}).get("text", ""),
                    "lat": loc["latitude"], "lng": loc["longitude"],
                    "ptype": p.get("primaryType", "")})
    return out


def main():
    pois = json.loads(POI.read_text(encoding="utf-8"))
    others = [p for p in pois if p.get("c") != "mall"]     # supermarkets etc. pass through untouched
    malls = [p for p in pois if p.get("c") == "mall"]
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    client = httpx.Client()
    print(f"resolving {len(malls)} OSM mall POIs against Places...", flush=True)

    by_place: dict[str, dict] = {}     # place id -> canonical centre being built
    unresolved = []                    # OSM malls with no Places centre nearby
    for i, m in enumerate(malls, 1):
        key = "%.5f,%.5f" % (m["lat"], m["lng"])
        if key in cache:
            hits = cache[key]
        else:
            try:
                hits = nearby(client, m["lat"], m["lng"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    time.sleep(20)
                    try:
                        hits = nearby(client, m["lat"], m["lng"])
                    except Exception:
                        hits = []
                else:
                    hits = []
            except Exception:
                hits = []
            cache[key] = hits
            time.sleep(0.04)

        # nearest Places shopping_mall within MATCH_M
        best = None
        for h in hits:
            if not h["id"]:
                continue
            d = hav(m["lat"], m["lng"], h["lat"], h["lng"])
            if d <= MATCH_M and (best is None or d < best[0]):
                best = (d, h)
        if best is None:
            unresolved.append(m)
        else:
            h = best[1]
            c = by_place.get(h["id"])
            if c is None:
                c = {"n": h["name"], "lat": h["lat"], "lng": h["lng"], "c": "mall",
                     "b": "", "brand": brand_of(h["name"]), "csrc": "places",
                     "place_id": h["id"], "aliases": set(), "n_osm": 0}
                by_place[h["id"]] = c
            c["aliases"].add(m.get("n", ""))
            c["aliases"].add(h["name"])
            c["n_osm"] += 1
        if i % 150 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {i}/{len(malls)}  centres so far {len(by_place)}, unresolved {len(unresolved)}", flush=True)

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    client.close()

    # seed the node list: one node per Places centre + one per unresolved OSM mall
    centres = list(by_place.values())
    for m in unresolved:
        centres.append({"n": m.get("n", ""), "lat": m["lat"], "lng": m["lng"], "c": "mall",
                        "b": "", "brand": brand_of(m.get("n", "")), "csrc": "osm",
                        "place_id": None, "aliases": {m.get("n", "")}, "n_osm": 1})

    CENTRE_KW = ("centre", "center", "mall", "shopping", "plaza", "arcade", "westfield",
                 "stockland", "marketplace", "galleria", "outlet", "village", "myer", "westpoint")

    def is_centre_name(c):
        return any(k in c["n"].lower() for k in CENTRE_KW)

    def name_related(a, b):
        na, nb = a["n"].strip().lower(), b["n"].strip().lower()
        if not na or not nb:
            return True
        return na in nb or nb in na

    def should_merge(a, b, dist):
        # same brand + proximity = same centre (folds in loading docks / sub-entries just past MERGE_M)
        if a.get("brand") and a["brand"] == b.get("brand"):
            return dist <= 300
        if name_related(a, b):
            return dist <= 600             # same / contained name = same centre (even large ones)
        if a["csrc"] == "places" and b["csrc"] == "places":
            # differently-named Places centres = distinct neighbours (CBD) — keep apart, UNLESS one
            # is a non-centre-named junk POI sitting right on top of a real centre.
            if is_centre_name(a) != is_centre_name(b):
                return dist <= 80
            return False
        return dist <= MERGE_M             # at least one is an unresolved arcade/feature

    def strength(c):
        # prefer a real Places centre, then a proper centre name, then more OSM features, then longer name
        return (c["csrc"] == "places", is_centre_name(c), c.get("n_osm", 0), len(c["n"]))

    # agglomerative merge (per-rule distances inside should_merge; 300 m is the widest any rule uses)
    changed = True
    while changed:
        changed = False
        for i in range(len(centres)):
            if centres[i] is None:
                continue
            for j in range(i + 1, len(centres)):
                if centres[j] is None:
                    continue
                a, b = centres[i], centres[j]
                dist = hav(a["lat"], a["lng"], b["lat"], b["lng"])
                if dist <= 300 and should_merge(a, b, dist):
                    keep, drop, drop_idx = (a, b, j) if strength(a) >= strength(b) else (b, a, i)
                    keep["aliases"] |= drop["aliases"]
                    keep["n_osm"] = keep.get("n_osm", 0) + drop.get("n_osm", 0)
                    keep["brand"] = keep.get("brand") or drop.get("brand")
                    centres[drop_idx] = None
                    changed = True
                    if drop_idx == i:
                        break                           # this i is gone; advance outer loop
        centres = [c for c in centres if c is not None]

    # Choose the best DISPLAY name from all aliases: Places sometimes tags a centre with a junk
    # POI name ("Santa photos", "Erika AU"); a real OSM/Places alias ("Westfield Hornsby") is better.
    def name_score(n):
        nl = n.lower()
        return (any(k in nl for k in ("westfield", "stockland", "westpoint")) * 100
                + is_centre_name({"n": n}) * 10 + min(len(n), 40) / 40.0)

    for c in centres:
        cands = list(c["aliases"]) + [c["n"]]
        c["n"] = max((x for x in cands if x), key=name_score, default=c["n"])
        c["brand"] = c.get("brand") or brand_of(c["n"])
        c["aliases"] = sorted(a for a in c["aliases"] if a)      # set -> list for JSON
        c.pop("n_osm", None)
    out = others + centres
    POI.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    merged = len(malls) - len(centres)
    br = defaultdict(int)
    places_n = sum(1 for c in centres if c["csrc"] == "places")
    for c in centres:
        if c.get("brand"):
            br[c["brand"]] += 1
    print(f"\n{len(malls)} OSM mall POIs -> {len(centres)} distinct centres "
          f"({places_n} Places-resolved, {len(centres) - places_n} OSM-only); merged away {merged} duplicates")
    print("branded: " + ", ".join(f"{k}={v}" for k, v in br.items()))


if __name__ == "__main__":
    main()
