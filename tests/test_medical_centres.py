"""Tests for src.medical_centres pure helpers (geo, tiers, attachment, Item 136)."""
from __future__ import annotations

import json

from src.medical_centres import (
    assemble,
    classify,
    haversine_m,
    is_item136_opportunity,
    is_pharmacy_attached,
    nearest_pharmacy,
    parse_overpass,
    select_provider,
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


def test_parse_overpass_excludes_pharmacies():
    elements = [
        {"type": "node", "lat": -33.7, "lon": 150.4,
         "tags": {"name": "TerryWhite Chemmart", "healthcare": "pharmacy"}},
        {"type": "node", "lat": -33.71, "lon": 150.41,
         "tags": {"name": "Corner Pharmacy", "amenity": "pharmacy"}},
        {"type": "node", "lat": -33.72, "lon": 150.42,
         "tags": {"name": "Real Clinic", "amenity": "clinic"}},
    ]
    names = {r["n"] for r in parse_overpass(elements)}
    assert names == {"Real Clinic"}


def test_assemble_dedupes_and_classifies():
    centres = [
        {"id": "A", "n": "Alpha MC", "lat": -33.80, "lng": 151.00, "weekly_hours": 72},
        {"id": "A2", "n": "Alpha MC", "lat": -33.80001, "lng": 151.00001, "weekly_hours": None},  # dup
        {"id": "B", "n": "Beta Clinic", "lat": -34.0, "lng": 150.5, "weekly_hours": None},
    ]
    phs = [{"latitude": -33.8004, "longitude": 151.0, "trading_name": "Near Pharmacy"}]  # ~44 m
    counts = {"A": (8, "nhsd", "verified"), "B": (None, "places_estimate", "unknown")}
    out = assemble(centres, phs, counts)
    assert len(out) == 2  # deduped
    alpha = next(c for c in out if c["n"] == "Alpha MC")
    assert alpha["category"] == "large" and alpha["doctor_count_confidence"] == "verified"
    assert alpha["pharmacy_attached"] is True
    beta = next(c for c in out if c["n"] == "Beta Clinic")
    assert beta["category"] == "unknown" and beta["nearest_pharmacy_name"] == "Near Pharmacy"


def test_enrich_place_uses_cache_without_http():
    import fetch_medical_centres as fmc

    class BoomClient:
        def post(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("HTTP called despite a cache hit")

    centre = {"n": "Alpha MC", "suburb": "Testville"}
    cache = {"Alpha MC|Testville": {"addr": "1 Cached St", "weekly_hours": 70}}
    out = fmc.enrich_place(BoomClient(), centre, cache)
    assert out["addr"] == "1 Cached St" and out["weekly_hours"] == 70


def test_main_preserves_existing_on_empty(tmp_path, monkeypatch):
    import fetch_medical_centres as fmc

    out_file = tmp_path / "medical_centres.json"
    out_file.write_text('[{"n": "Keep me"}]', encoding="utf-8")
    monkeypatch.setattr(fmc, "OUT", out_file)
    monkeypatch.setattr(fmc, "fetch_overpass", lambda: [])
    rc = fmc.main()
    assert rc != 0
    assert json.loads(out_file.read_text(encoding="utf-8")) == [{"n": "Keep me"}]


def test_select_provider():
    assert select_provider(None) == "places_estimate"
    assert select_provider("") == "places_estimate"
    assert select_provider("secret-key") == "nhsd"


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
