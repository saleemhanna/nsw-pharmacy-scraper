"""Cross-reference every pharmacy's authoritative NSW AddressPoint pin against Google
Places (New) — a QA pass that flags where the two disagree.

We do NOT move the map pins here (AddressPoint stays the source of truth for display,
per licensing). This produces a review file: for each pharmacy it records the Google
business location, the distance between the two pins, and a status:

    MATCH        <= 75 m   pins agree; doorstep confirmed by Google
    MINOR        <= 150 m  small offset (unit vs building), fine
    FLAG         >  150 m  pins disagree -> AddressPoint pin may be on the wrong spot
    NO_RESULT    Google has no listing for this pharmacy
    NAME_WEAK    Google returned something, but not a pharmacy/store type (weak match)

Outputs (in project root):
    xref_google.json  - full per-pharmacy cross-reference (feeds the map QA layer)
    xref_review.csv   - human-readable, sorted worst-first (the FLAG list)

Cache: writes progress to the scratchpad so a re-run resumes instead of re-billing.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from pathlib import Path

import httpx

# Prefer the CI/Railway secret; fall back to the public unrestricted Places key for local runs.
API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY") or "AIzaSyBsCtw8rVhQpb3G91DbhvhDRyDscA4MBeM"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "map_data.json"
OUT_JSON = ROOT / "xref_google.json"
OUT_CSV = ROOT / "xref_review.csv"
# Persist the cache in-repo (committed, like the other Places caches) so CI only ever
# bills Google for genuinely new/moved pharmacies instead of re-querying all 2000+ each run.
CACHE = ROOT / ".cache" / "xref_google_cache.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)

MATCH_M = 75
MINOR_M = 150
PHARM_TYPES = {"pharmacy", "drugstore", "store", "health", "point_of_interest", "establishment"}


def hav_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = (math.sin(dLat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dLon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def search_place(client, query):
    r = client.post(
        PLACES_URL,
        headers={
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location,places.types,places.businessStatus",
        },
        json={"textQuery": query, "regionCode": "AU", "maxResultCount": 1},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("places"):
        return None
    p = data["places"][0]
    loc = p["location"]
    return {
        "lat": loc["latitude"],
        "lng": loc["longitude"],
        "name": p.get("displayName", {}).get("text", ""),
        "addr": p.get("formattedAddress", ""),
        "types": p.get("types", []),
        "status": p.get("businessStatus", ""),
    }


def classify(dist_m, hit):
    if not (set(hit["types"]) & PHARM_TYPES):
        return "NAME_WEAK"
    if dist_m <= MATCH_M:
        return "MATCH"
    if dist_m <= MINOR_M:
        return "MINOR"
    return "FLAG"


def main():
    recs = json.loads(SRC.read_text(encoding="utf-8"))
    recs = [r for r in recs if r.get("latitude") and r.get("longitude")]
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    print(f"pharmacies to cross-reference: {len(recs)}  (cached: {len(cache)})", flush=True)

    client = httpx.Client()
    results = []
    counts = {}
    done = 0
    for i, p in enumerate(recs, 1):
        # include rounded coords so a moved AddressPoint pin re-queries next run (else the
        # cached distance would be stale); reg number alone keys the business.
        key = "%s|%.5f|%.5f" % (
            p.get("registration_number") or p.get("trading_name") or "",
            round(p["latitude"], 5), round(p["longitude"], 5),
        )
        name = (p.get("trading_name") or "").strip()
        suburb = (p.get("suburb") or "").strip()
        postcode = (p.get("postcode") or "").strip()

        legacy_key = p.get("registration_number") or f"{name}|{p['latitude']}|{p['longitude']}"
        if key in cache:
            row = cache[key]
        elif legacy_key in cache:
            row = cache[legacy_key]          # reuse pre-migration cache; don't re-bill
            cache[key] = row
        else:
            query = f"{name}, {suburb} NSW {postcode}"
            hit = None
            for attempt in range(3):
                try:
                    hit = search_place(client, query)
                    break
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        print(f"  rate limited @ {i}; wait 20s", flush=True)
                        time.sleep(20)
                        continue
                    print(f"  HTTP {e.response.status_code} @ {i} {name}", flush=True)
                    break
                except Exception as e:
                    time.sleep(2)
                    if attempt == 2:
                        print(f"  err @ {i} {name}: {str(e)[:50]}", flush=True)
            if hit is None:
                row = {"status": "NO_RESULT", "dist_m": None,
                       "g_lat": None, "g_lng": None, "g_name": "", "g_addr": "", "g_status": ""}
            else:
                dist = hav_m(p["latitude"], p["longitude"], hit["lat"], hit["lng"])
                row = {"status": classify(dist, hit), "dist_m": round(dist, 1),
                       "g_lat": hit["lat"], "g_lng": hit["lng"], "g_name": hit["name"],
                       "g_addr": hit["addr"], "g_status": hit["status"]}
            cache[key] = row
            time.sleep(0.05)

        counts[row["status"]] = counts.get(row["status"], 0) + 1
        results.append({
            "registration_number": p.get("registration_number", ""),
            "trading_name": name, "suburb": suburb, "postcode": postcode,
            "address": p.get("address", ""),
            "ap_lat": p["latitude"], "ap_lng": p["longitude"],
            **row,
        })
        done += 1
        if done % 100 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {done}/{len(recs)}  {counts}", flush=True)

    client.close()
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    # review CSV: worst-first (FLAG, then NAME_WEAK/NO_RESULT, then by distance)
    order = {"FLAG": 0, "NAME_WEAK": 1, "NO_RESULT": 2, "MINOR": 3, "MATCH": 4}
    review = sorted(results, key=lambda r: (order.get(r["status"], 9), -(r["dist_m"] or 0)))
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["status", "dist_m", "trading_name", "suburb", "postcode",
                    "registered_address", "google_name", "google_address",
                    "google_business_status", "ap_lat", "ap_lng", "g_lat", "g_lng"])
        for r in review:
            w.writerow([r["status"], r["dist_m"], r["trading_name"], r["suburb"], r["postcode"],
                        r["address"], r["g_name"], r["g_addr"], r["g_status"],
                        r["ap_lat"], r["ap_lng"], r["g_lat"], r["g_lng"]])

    print("\n=== cross-reference complete ===", flush=True)
    for k in ["MATCH", "MINOR", "FLAG", "NAME_WEAK", "NO_RESULT"]:
        print(f"  {k:10} {counts.get(k, 0)}", flush=True)
    print(f"\nwrote {OUT_JSON.name} and {OUT_CSV.name}", flush=True)


if __name__ == "__main__":
    main()
