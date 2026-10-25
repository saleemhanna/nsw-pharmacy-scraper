"""Tests for src.medical_centres pure helpers (geo, tiers, attachment, Item 136)."""
from __future__ import annotations

from src.medical_centres import (
    classify,
    haversine_m,
    is_item136_opportunity,
    is_pharmacy_attached,
    nearest_pharmacy,
    parse_overpass,
)


def test_classify_tiers():
    assert classify(0) == "unknown"
    assert classify(None) == "unknown"
    assert classify(1) == "small" and classify(3) == "small"
    assert classify(4) == "medium" and classify(6) == "medium"
    assert classify(7) == "large" and classify(25) == "large"


def test_haversine_known_distance():
    # ~1.11 km per 0.01 deg latitude
    assert 1100 < haversine_m(-33.80, 151.00, -33.81, 151.00) < 1130


def test_nearest_pharmacy_empty_list():
    assert nearest_pharmacy(-33.8, 151.0, []) == (float("inf"), None)


def test_nearest_pharmacy_picks_closest():
    phs = [
        {"latitude": -33.90, "longitude": 151.0, "trading_name": "Far"},
        {"latitude": -33.801, "longitude": 151.0, "trading_name": "Near"},
    ]
    dist, name = nearest_pharmacy(-33.80, 151.0, phs)
    assert name == "Near" and dist < 200


def test_pharmacy_attached_threshold():
    assert is_pharmacy_attached(40) is True
    assert is_pharmacy_attached(500) is False


def test_parse_overpass_maps_and_skips_missing_coords():
    elements = [
        {
            "type": "node",
            "lat": -33.8,
            "lon": 151.0,
            "tags": {
                "name": "Alpha Medical Centre",
                "amenity": "clinic",
                "addr:suburb": "Testville",
            },
        },
        {
            "type": "way",
            "center": {"lat": -34.0, "lon": 150.5},
            "tags": {"name": "Beta Clinic", "amenity": "doctors"},
        },
        {"type": "node", "tags": {"name": "No Coords Clinic", "amenity": "clinic"}},  # skipped
    ]
    out = parse_overpass(elements)
    names = {r["n"] for r in out}
    assert names == {"Alpha Medical Centre", "Beta Clinic"}
    assert all("lat" in r and "lng" in r for r in out)


def test_item136_opportunity_rules():
    base = {
        "category": "large",
        "pharmacy_attached": False,
        "nearest_pharmacy_m": 450,
        "weekly_hours": 72,
    }
    assert is_item136_opportunity(base) is True
    assert is_item136_opportunity({**base, "category": "medium"}) is False
    assert is_item136_opportunity({**base, "pharmacy_attached": True}) is False
    assert is_item136_opportunity({**base, "nearest_pharmacy_m": 200}) is False
    assert is_item136_opportunity({**base, "weekly_hours": None}) is True  # unknown hours allowed
    assert is_item136_opportunity({**base, "weekly_hours": 40}) is False
