"""Fetch the real website favicon for every MAJOR shopping centre and attach it to the map.

A major centre = a Westfield/Stockland, or any centre with >=80 stores (from poi_counts.json).
For each we find the centre's official website (hard-coded for the two national brands, else via
Google Places websiteUri), then pull that site's favicon through Google's favicon service — the
same icon you see next to a site in Google results. Saved under logos/centres/<domain>.png and
referenced from poi_data.json via each centre's `logo` field; generate_map.py embeds them.

Runs after build_poi_counts.py (needs store counts to know which centres are major). Resumable:
a domain's favicon is only fetched once.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
SEARCHTEXT = "https://places.googleapis.com/v1/places:searchText"
POI = Path("poi_data.json")
PC = Path("poi_counts.json")
LOGODIR = Path("logos/centres")
LOGODIR.mkdir(parents=True, exist_ok=True)

MAJOR_STORES = 80
BRAND_SITE = {"westfield": "westfield.com.au", "stockland": "stockland.com.au"}
# manual website overrides for centres whose Places listing has no (or a wrong) website
MANUAL_SITE = {
    "chatswood chase": "chatswoodchase.com.au",
    "chatswood chase shopping centre": "chatswoodchase.com.au",
}


def domain_of(url: str) -> str | None:
    if not url:
        return None
    try:
        net = urlparse(url).netloc.lower()
    except Exception:
        return None
    net = net.split(":")[0]
    if net.startswith("www."):
        net = net[4:]
    return net or None


def find_website(client, name, lat, lng):
    try:
        r = client.post(
            SEARCHTEXT,
            headers={"X-Goog-Api-Key": API_KEY,
                     "X-Goog-FieldMask": "places.displayName,places.websiteUri,places.location"},
            json={"textQuery": f"{name} shopping centre", "regionCode": "AU", "maxResultCount": 1,
                  "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lng}, "radius": 3000}}},
            timeout=30,
        )
        r.raise_for_status()
        pl = r.json().get("places", [])
        return domain_of(pl[0].get("websiteUri", "")) if pl else None
    except Exception:
        return None


def fetch_favicon(client, domain) -> bool:
    out = LOGODIR / f"{domain}.png"
    if out.exists() and out.stat().st_size > 200:
        return True
    for url in (f"https://www.google.com/s2/favicons?domain={domain}&sz=64",
                f"https://icons.duckduckgo.com/ip3/{domain}.ico"):
        try:
            r = client.get(url, timeout=20, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 200 and "image" in r.headers.get("content-type", ""):
                out.write_bytes(r.content)
                return True
        except Exception:
            continue
    return False


def main():
    pois = json.loads(POI.read_text(encoding="utf-8"))
    pc = json.loads(PC.read_text(encoding="utf-8")) if PC.exists() else {}

    def key(p):
        return "%s|%.6f|%.6f" % (p.get("n") or "", p["lat"], p["lng"])

    def stores(p):
        c = pc.get(key(p))
        try:
            return int(c["stores"]) if c and c.get("stores") not in (None, "") else 0
        except Exception:
            return 0

    majors = [p for p in pois if p.get("c") == "mall" and (p.get("brand") or stores(p) >= MAJOR_STORES)]
    print(f"major centres: {len(majors)}", flush=True)

    client = httpx.Client()
    got = miss = 0
    for p in majors:
        brand = p.get("brand")
        cn = pc.get(key(p), {}).get("cname") or p["n"]
        domain = (BRAND_SITE.get(brand)
                  or MANUAL_SITE.get(cn.strip().lower()) or MANUAL_SITE.get(p["n"].strip().lower())
                  or domain_of(p.get("website", ""))
                  or find_website(client, cn, p["lat"], p["lng"]))
        if domain and fetch_favicon(client, domain):
            p["logo"] = domain
            got += 1
        else:
            p.pop("logo", None)
            miss += 1
            print(f"  no favicon: {p['n']} (domain={domain})", flush=True)
    client.close()

    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nfavicons attached: {got}/{len(majors)} majors ({miss} without a usable site)")


if __name__ == "__main__":
    main()
