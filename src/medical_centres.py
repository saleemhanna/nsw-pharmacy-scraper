"""Pure helpers for the medical-centres map layer.

Geo distance, doctor-count tier classification, nearest-pharmacy / attachment,
and the Item 136 "large medical centre" opportunity test. No I/O here so these
stay unit-testable and mypy-strict clean.
"""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in metres."""
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * asin(sqrt(a))


def parse_overpass(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Overpass elements to centre records, skipping unnamed / coordless ones.

    Each record is {"n","lat","lng","addr","suburb","source"}. Coordinates come
    from a node's `lat`/`lon` or a way/relation `center`. Deduped by
    (name.lower(), round(lat,4), round(lng,4)).
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    for el in elements:
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        lat = el.get("lat")
        lng = el.get("lon")
        if lat is None or lng is None:
            center = el.get("center") or {}
            lat = center.get("lat")
            lng = center.get("lon")
        if lat is None or lng is None:
            continue
        lat_f, lng_f = float(lat), float(lng)
        key = (name.lower(), round(lat_f, 4), round(lng_f, 4))
        if key in seen:
            continue
        seen.add(key)
        num = (tags.get("addr:housenumber") or "").strip()
        street = (tags.get("addr:street") or "").strip()
        addr = " ".join(p for p in (num, street) if p)
        out.append(
            {
                "n": name,
                "lat": lat_f,
                "lng": lng_f,
                "addr": addr,
                "suburb": (tags.get("addr:suburb") or "").strip(),
                "source": "overpass",
            }
        )
    return out


def classify(doctor_count: int | None) -> str:
    """Tier a centre by its doctor (prescribing medical practitioner) count.

    1-3 -> small, 4-6 -> medium, 7+ -> large, 0/None/unknown -> "unknown".
    """
    if doctor_count is None or doctor_count <= 0:
        return "unknown"
    if doctor_count <= 3:
        return "small"
    if doctor_count <= 6:
        return "medium"
    return "large"


def select_provider(nhsd_key: str | None) -> str:
    """Pick the doctor-count provider: NHSD when a key is present, else Places estimate."""
    return "nhsd" if nhsd_key else "places_estimate"


def nearest_pharmacy(
    lat: float, lng: float, pharmacies: list[dict[str, Any]]
) -> tuple[float, str | None]:
    """Return (distance_m, trading_name) of the closest pharmacy.

    Pharmacies use `latitude`/`longitude`/`trading_name` (map_data.json shape).
    Records without usable coordinates are skipped; an empty/absent set yields
    (inf, None).
    """
    best_dist = float("inf")
    best_name: str | None = None
    for ph in pharmacies:
        plat = ph.get("latitude")
        plng = ph.get("longitude")
        if plat is None or plng is None:
            continue
        d = haversine_m(lat, lng, float(plat), float(plng))
        if d < best_dist:
            best_dist = d
            best_name = ph.get("trading_name")
    return best_dist, best_name


def is_pharmacy_attached(distance_m: float, threshold_m: float = 60.0) -> bool:
    """True when the nearest pharmacy is within `threshold_m` (same building)."""
    return distance_m <= threshold_m


def assemble(
    centres: list[dict[str, Any]],
    pharmacies: list[dict[str, Any]],
    provider_counts: dict[str, tuple[int | None, str, str]],
) -> list[dict[str, Any]]:
    """Build enriched centre records: dedupe, attach doctor count + tier + nearest pharmacy.

    `provider_counts` maps a centre `id` to (count, source, confidence). Dedupe is by
    (name.lower(), round(lat,4), round(lng,4)); the first occurrence wins.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    for c in centres:
        name = c.get("n", "")
        lat, lng = float(c["lat"]), float(c["lng"])
        key = (name.lower(), round(lat, 4), round(lng, 4))
        if key in seen:
            continue
        seen.add(key)
        count, source, confidence = provider_counts.get(c.get("id", ""), (None, "unknown", "unknown"))
        dist, phname = nearest_pharmacy(lat, lng, pharmacies)
        out.append(
            {
                "id": c.get("id"),
                "n": name,
                "lat": lat,
                "lng": lng,
                "addr": c.get("addr", ""),
                "suburb": c.get("suburb", ""),
                "doctor_count": count,
                "doctor_count_source": source,
                "doctor_count_confidence": confidence,
                "category": classify(count),
                "weekly_hours": c.get("weekly_hours"),
                "nearest_pharmacy_m": round(dist, 1) if dist != float("inf") else None,
                "nearest_pharmacy_name": phname,
                "pharmacy_attached": is_pharmacy_attached(dist),
            }
        )
    return out


def is_item136_opportunity(
    centre: dict[str, Any],
    min_gap_m: float = 300.0,
    min_weekly_hours: float = 70.0,
) -> bool:
    """Indicative Item 136 (large medical centre) pharmacy opportunity.

    Large tier AND no pharmacy attached AND >= min_gap_m to the nearest
    pharmacy AND (hours unknown OR >= min_weekly_hours). Unknown hours do not
    disqualify a lead.
    """
    if centre.get("category") != "large":
        return False
    if centre.get("pharmacy_attached") is not False:
        return False
    nearest = centre.get("nearest_pharmacy_m")
    if nearest is None or nearest < min_gap_m:
        return False
    hours = centre.get("weekly_hours")
    if hours is not None and hours < min_weekly_hours:
        return False
    return True
