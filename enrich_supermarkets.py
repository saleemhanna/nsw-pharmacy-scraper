"""Add a street address (and containing shopping-centre name) to each supermarket in
poi_data.json, so map popups can show e.g. "Woolworths — St Marys Village, 12 Charles
Hackett Dr, St Marys".

- address: nearest NSW DCS AddressPoint (authoritative) to the supermarket's coordinates.
- centre:  nearest shopping-centre POI (shop=mall) within 250 m, if any.

Idempotent. Adds keys "addr" and "centre" (centre omitted if none nearby).

Usage:  python measure_supermarkets.py -> python enrich_supermarkets.py -> python generate_map.py
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

POI = Path("poi_data.json")
ADDRPT_URL = "https://portal.spatial.nsw.gov.au/server/rest/services/NSW_Geocoded_Addressing_Theme/MapServer/1/query"
CENTRE_RADIUS_M = 250


def haversine_m(a, b, c, d):
    R = 6371000.0
    dlat = math.radians(c - a)
    dlon = math.radians(d - b)
    x = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a)) * math.cos(math.radians(c)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


# Full-word street types only (NSW AddressPoint spells them out, e.g. "QUEEN STREET").
# Deliberately excludes abbreviations (ST, RD...) and words that also end suburb names
# (HILL, HEIGHTS, POINT, GREEN...) so "QUEEN STREET ST MARYS" -> "ST MARYS", not "MARYS".
STREET_TYPES = {
    "AVENUE", "ROAD", "STREET", "DRIVE", "PLACE", "LANE", "COURT", "CRESCENT", "PARADE",
    "HIGHWAY", "CLOSE", "WAY", "TERRACE", "CIRCUIT", "BOULEVARD", "BOULEVARDE", "ESPLANADE",
    "GROVE", "SQUARE", "WALK", "MEWS", "ROW", "LOOP", "PARKWAY", "CIRCLE", "CIRCUS", "QUAY",
    "PROMENADE", "CONCOURSE", "ARCADE", "CRES", "PARKLANDS", "FREEWAY", "MOTORWAY",
}


def parse_suburb(address):
    """NSW AddressPoint address strings are '<num> <STREET> <TYPE> <SUBURB...>' (no postcode).
    The suburb is whatever follows the LAST street-type token."""
    if not address:
        return None
    toks = address.upper().split()
    last = -1
    for i, t in enumerate(toks):
        if t in STREET_TYPES:
            last = i
    if last != -1 and last < len(toks) - 1:
        return " ".join(toks[last + 1:])
    return toks[-1] if toks else None  # fallback: trailing token


def title_case(s):
    small = {"of", "and", "the", "at"}
    out = []
    for w in s.split():
        out.append(w if (w.isupper() and len(w) <= 3 and any(ch.isdigit() for ch in w)) else
                   (w.lower() if w.lower() in small else w.capitalize()))
    return " ".join(out)


def nearest_address(lat, lng, radius_deg=0.0015):
    """Nearest AddressPoint to (lat,lng) -> (address, suburb) title-cased, or (None, None)."""
    params = {
        "where": "1=1",
        "geometry": f"{lng - radius_deg},{lat - radius_deg},{lng + radius_deg},{lat + radius_deg}",
        "geometryType": "esriGeometryEnvelope", "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects", "outFields": "address",
        "returnGeometry": "true", "outSR": "4326", "f": "json", "resultRecordCount": "60",
    }
    url = ADDRPT_URL + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PharmaSite/1.0 (info@ch-legal.com.au)"})
            with urllib.request.urlopen(req, timeout=40) as r:
                feats = json.load(r).get("features", [])
            break
        except Exception as e:  # noqa: BLE001
            print(f"    addr attempt {attempt + 1} failed: {str(e)[:60]}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    else:
        return None, None
    best, best_d = None, 1e18
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("y") is None:
            continue
        d = haversine_m(lat, lng, g["y"], g["x"])
        if d < best_d:
            best_d, best = d, (f.get("attributes") or {})
    if not best:
        return None, None
    addr = best.get("address")
    sub = parse_suburb(addr)
    return (title_case(addr) if addr else None, title_case(sub) if sub else None)


def main() -> int:
    pois = json.loads(POI.read_text(encoding="utf-8"))
    supers = [p for p in pois if p.get("c") == "supermarket"]
    malls = [p for p in pois if p.get("c") != "supermarket"]
    print(f"enriching {len(supers)} supermarkets (addresses + centre names)...")

    done = 0
    for i, p in enumerate(supers, 1):
        lat, lng = p["lat"], p["lng"]
        # nearest shopping centre
        cbest, cd = None, CENTRE_RADIUS_M
        for m in malls:
            d = haversine_m(lat, lng, m["lat"], m["lng"])
            if d <= cd:
                cd, cbest = d, m["n"]
        if cbest:
            p["centre"] = cbest
        # nearest address + suburb
        addr, sub = nearest_address(lat, lng)
        if not addr and not sub:
            addr, sub = nearest_address(lat, lng, radius_deg=0.004)  # widen once
        if addr:
            p["addr"] = addr
            done += 1
        if sub and not p.get("suburb"):
            p["suburb"] = sub
        # Upgrade a bare-brand name ("IGA") to "IGA Northmead" once we know the suburb.
        if p.get("b") and p.get("suburb") and p.get("n", "").strip().lower() == p["b"].strip().lower():
            p["n"] = f"{p['b']} {p['suburb']}"
        time.sleep(0.2)
        if i % 100 == 0:
            print(f"  {i}/{len(supers)} ({done} addressed)")
            POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")

    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")
    withc = sum(1 for p in supers if p.get("centre"))
    print(f"\ndone: {done}/{len(supers)} got an address, {withc} matched to a shopping centre")
    for p in supers[:5]:
        print(f"  {p['n']}  |  centre={p.get('centre','-')}  |  {p.get('addr','-')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
