"""Fetch all NSW medical/health clinics and build medical_centres.json.

Discovery via OpenStreetMap (Overpass, free, keyless) over all NSW clinics,
enriched with Google Places (address/hours + interim doctor-count estimate),
with nearest-pharmacy attachment and Small/Medium/Large tiers. The doctor count
is pluggable: Places estimate now, NHSD when a key is granted.

Usage:  python fetch_medical_centres.py   then   python generate_map.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from src.medical_centres import parse_overpass

OUT = Path("medical_centres.json")
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# All NSW medical/health clinics (spec: all clinics, not GP-only).
QUERY = """
[out:json][timeout:240];
area["ISO3166-2"="AU-NSW"]->.nsw;
(
  nwr["amenity"="clinic"](area.nsw);
  nwr["amenity"="doctors"](area.nsw);
  nwr["amenity"="hospital"](area.nsw);
  nwr["healthcare"](area.nsw);
);
out center tags;
"""


def fetch_overpass() -> list[dict[str, Any]]:
    """Query the Overpass mirrors for NSW clinics; return parsed centre records.

    Best-effort: on total failure returns [] so the caller can preserve any
    existing medical_centres.json rather than overwrite it with nothing.
    """
    payload = urllib.parse.urlencode({"data": QUERY}).encode()
    for url in ENDPOINTS:
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "User-Agent": "PharmaSite/1.0 (NSW medical-centre locator; info@ch-legal.com.au)"
                },
            )
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.load(r)
            return parse_overpass(data.get("elements", []))
        except Exception as e:  # noqa: BLE001
            print(
                f"  endpoint failed ({url}): {type(e).__name__}: {str(e)[:120]}",
                file=sys.stderr,
            )
    print("  all Overpass endpoints failed", file=sys.stderr)
    return []
