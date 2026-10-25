"""Fetch all NSW medical/health clinics and build medical_centres.json.

Discovery via OpenStreetMap (Overpass, free, keyless) over all NSW clinics,
enriched with Google Places (address/hours + interim doctor-count estimate),
with nearest-pharmacy attachment and Small/Medium/Large tiers. The doctor count
is pluggable: Places estimate now, NHSD when a key is granted.

Usage:  python fetch_medical_centres.py   then   python generate_map.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import httpx

from src.medical_centres import assemble, parse_overpass, select_provider

OUT = Path("medical_centres.json")
API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
NHSD_API_KEY = os.environ.get("NHSD_API_KEY", "")
TEXT_SEARCH = "https://places.googleapis.com/v1/places:searchText"
# Cache dir: POI_CACHE_DIR (set in CI so the cache persists in-repo), else local temp.
CACHE = Path(os.environ.get("POI_CACHE_DIR") or os.environ.get("TEMP", ".")) / "places_medical_cache.json"
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


def _weekly_hours(opening: dict[str, Any] | None) -> float | None:
    """Sum Google regularOpeningHours periods to total open hours per week, else None."""
    if not opening:
        return None
    periods = opening.get("periods") or []
    total = 0.0
    for per in periods:
        o = per.get("open") or {}
        c = per.get("close") or {}
        if "hour" not in o or "hour" not in c:
            return None  # 24h or malformed: treat as unknown
        start = o.get("hour", 0) * 60 + o.get("minute", 0)
        end = c.get("hour", 0) * 60 + c.get("minute", 0)
        if end < start:
            end += 24 * 60
        total += (end - start) / 60.0
    return total if periods else None


def _cache_key(centre: dict[str, Any]) -> str:
    return f"{centre.get('n', '')}|{centre.get('suburb', '')}"


def _apply_enrichment(centre: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if data.get("addr") and not centre.get("addr"):
        centre["addr"] = data["addr"]
    if data.get("weekly_hours") is not None:
        centre["weekly_hours"] = data["weekly_hours"]
    return centre


def enrich_place(
    client: Any, centre: dict[str, Any], cache: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Fill addr/suburb/weekly_hours from Google Places Text Search (best-effort).

    Results are cached by `_cache_key` in `cache` (persisted to CACHE across runs) so
    repeat runs don't re-bill Google. Returns the centre unchanged on any error or
    when no API key is configured and there is no cache hit.
    """
    cache = cache if cache is not None else {}
    key = _cache_key(centre)
    if key in cache:
        return _apply_enrichment(centre, cache[key])
    if not API_KEY:
        return centre
    query = f"{centre['n']} {centre.get('suburb', '')} NSW".strip()
    try:
        r = client.post(
            TEXT_SEARCH,
            headers={
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": (
                    "places.formattedAddress,places.location,"
                    "places.regularOpeningHours,places.displayName"
                ),
            },
            json={"textQuery": query, "maxResultCount": 1},
            timeout=30,
        )
        if r.status_code == 429:
            time.sleep(20)
            r = client.post(
                TEXT_SEARCH,
                headers={
                    "X-Goog-Api-Key": API_KEY,
                    "X-Goog-FieldMask": (
                        "places.formattedAddress,places.location,"
                        "places.regularOpeningHours,places.displayName"
                    ),
                },
                json={"textQuery": query, "maxResultCount": 1},
                timeout=30,
            )
        r.raise_for_status()
        places = r.json().get("places", [])
        data: dict[str, Any] = {}
        if places:
            p = places[0]
            if p.get("formattedAddress"):
                data["addr"] = p["formattedAddress"]
            data["weekly_hours"] = _weekly_hours(p.get("regularOpeningHours"))
        cache[key] = data
        _apply_enrichment(centre, data)
    except Exception as e:  # noqa: BLE001
        print(f"  places enrich failed for {centre['n']}: {type(e).__name__}", file=sys.stderr)
    return centre


def places_estimate_count(
    client: httpx.Client, centre: dict[str, Any]
) -> tuple[int | None, str, str]:
    """Interim doctor-count provider.

    Google Places lists a clinic as a single POI, not its individual doctors, so
    this cannot reliably count doctors: it returns (None, "places_estimate",
    "unknown") — the scaffold case — until NHSD provides real counts.
    """
    return None, "places_estimate", "unknown"


def nhsd_count(
    client: httpx.Client, centre: dict[str, Any], api_key: str
) -> tuple[int | None, str, str]:
    """Authoritative doctor-count provider via the NHSD Service Finder API.

    TODO(nhsd): implement once the API key is granted — search healthcareServices
    at the centre location and count distinct GP/medical-practitioner practitioners,
    returning (count, "nhsd", "verified"). Stubbed until then.
    """
    return None, "nhsd", "unknown"


def main() -> int:
    centres = fetch_overpass()
    if not centres:
        print(
            "no centres discovered; preserving existing medical_centres.json",
            file=sys.stderr,
        )
        return 1
    for c in centres:
        c["id"] = f"{c['n']}|{round(c['lat'], 5)}|{round(c['lng'], 5)}"

    pharmacies: list[dict[str, Any]] = []
    reg = Path("map_data.json")
    if reg.exists():
        pharmacies = json.loads(reg.read_text(encoding="utf-8"))

    provider = select_provider(NHSD_API_KEY)
    print(f"discovered {len(centres)} centres | doctor-count provider: {provider}", flush=True)

    cache: dict[str, Any] = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache = {}

    def _save_cache() -> None:
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"  cache write failed: {type(e).__name__}", file=sys.stderr)

    provider_counts: dict[str, tuple[int | None, str, str]] = {}
    with httpx.Client() as client:
        for i, c in enumerate(centres, 1):
            enrich_place(client, c, cache)
            if provider == "nhsd":
                provider_counts[c["id"]] = nhsd_count(client, c, NHSD_API_KEY)
            else:
                provider_counts[c["id"]] = places_estimate_count(client, c)
            if i % 200 == 0:
                print(f"  enriched {i}/{len(centres)}", flush=True)
                _save_cache()
    _save_cache()

    out = assemble(centres, pharmacies, provider_counts)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tiers = {t: sum(1 for c in out if c["category"] == t) for t in ("small", "medium", "large", "unknown")}
    print(f"wrote {OUT} ({len(out)} centres) tiers={tiers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
