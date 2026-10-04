"""Fetch all NSW supermarkets and shopping centres from OpenStreetMap (Overpass API).

Free, keyless, complete coverage. Writes poi_data.json, which generate_map.py reads to
pin supermarkets and shopping centres as two toggleable layers on the map.

Usage:  python fetch_pois.py   then   python generate_map.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path("poi_data.json")
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = """
[out:json][timeout:240];
area["ISO3166-2"="AU-NSW"]->.nsw;
(
  nwr["shop"="supermarket"](area.nsw);
  nwr["shop"="wholesale"](area.nsw);
  nwr["shop"="mall"](area.nsw);
);
out center tags;
"""

# Only these major supermarket chains (drop small / independent grocers).
ALLOWED_SUPERMARKETS = {
    "aldi": "ALDI",
    "coles": "Coles",
    "costco": "Costco",
    "iga": "IGA",
    "woolworths": "Woolworths",
    "harris farm": "Harris Farm",
}


def supermarket_brand(tags: dict) -> str | None:
    """Return the canonical chain name if this store is one of the allowed majors, else None."""
    brand = (tags.get("brand") or "").strip().lower()
    if brand:  # branded store: must match an allowed chain
        for key, canon in ALLOWED_SUPERMARKETS.items():
            if key in brand:
                return canon
        return None
    text = ((tags.get("name") or "") + " " + (tags.get("operator") or "")).lower()
    for key, canon in ALLOWED_SUPERMARKETS.items():
        if key in text:
            return canon
    return None


def fetch() -> dict:
    payload = urllib.parse.urlencode({"data": QUERY}).encode()
    last = None
    for url in ENDPOINTS:
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"User-Agent": "PharmaSite/1.0 (NSW pharmacy locator; info@ch-legal.com.au)"},
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  endpoint failed ({url}): {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
    raise SystemExit(f"ERROR: all Overpass endpoints failed: {last}")


def main() -> int:
    print("querying OpenStreetMap (Overpass) for NSW supermarkets + shopping centres...")
    data = fetch()
    pois = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        shop = tags.get("shop")
        brand = ""
        if shop in ("supermarket", "wholesale"):
            brand = supermarket_brand(tags)
            if not brand:
                continue  # exclude small / independent grocers
            cat = "supermarket"
        elif shop == "mall":
            cat = "mall"
        else:
            continue
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center", {})
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        osm_name = (tags.get("name") or "").strip()
        suburb = (tags.get("addr:suburb") or tags.get("addr:city")
                  or tags.get("addr:town") or "").strip()
        if cat == "supermarket":
            # Prefer a specific OSM name; otherwise "Brand Suburb" (e.g. IGA -> "IGA Northmead").
            generic = {brand.lower(), (brand + " supermarket").lower(), "supermarket"}
            if osm_name and osm_name.lower() not in generic:
                name = osm_name
            elif suburb:
                name = f"{brand} {suburb}"
            else:
                name = osm_name or brand
        else:
            name = osm_name or "Shopping Centre"
        rec = {"n": name, "lat": round(lat, 6), "lng": round(lon, 6), "c": cat, "b": brand}
        if suburb:
            rec["suburb"] = suburb
        pois.append(rec)

    # de-dupe near-identical points (same name + ~same spot)
    seen = set()
    unique = []
    for p in pois:
        key = (p["n"].lower(), round(p["lat"], 4), round(p["lng"], 4))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    OUT.write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    sm = sum(1 for p in unique if p["c"] == "supermarket")
    ml = sum(1 for p in unique if p["c"] == "mall")
    from collections import Counter
    by_chain = Counter(p["b"] for p in unique if p["c"] == "supermarket")
    print(f"supermarkets: {sm} | shopping centres: {ml} | total: {len(unique)} (deduped from {len(pois)})")
    print("by chain: " + ", ".join(f"{b}={n}" for b, n in by_chain.most_common()))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
