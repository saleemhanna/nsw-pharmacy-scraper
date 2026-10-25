"""Tests for sweep_new_supermarkets pure helpers (grid + merge)."""
from __future__ import annotations

from sweep_new_supermarkets import grid_cells, is_full_supermarket, merge_new


def test_grid_cells_covers_bbox():
    cells = grid_cells((-34.0, 150.0, -33.9, 150.1), step_lat=0.072, step_lng=0.086)
    # 0.1deg / ~0.07-0.08 step -> 2 rows x 2 cols
    assert len(cells) == 4
    assert cells[0] == (-34.0, 150.0)
    assert all(-34.001 <= la <= -33.899 and 149.999 <= ln <= 150.101 for la, ln in cells)


def test_merge_new_adds_only_genuinely_new_branded():
    existing = [{"lat": -33.7000, "lng": 150.8000}]  # an existing supermarket
    found = [
        {"n": "Woolworths Melonba", "lat": -33.7012, "lng": 150.8051, "b": "Woolworths"},  # ~475m -> new
        {"n": "Coles (dup of existing)", "lat": -33.7001, "lng": 150.8001, "b": "Coles"},   # ~15m -> dup
        {"n": "Corner Deli", "lat": -33.60, "lng": 150.90, "b": None},                      # unbranded -> skip
    ]
    new = merge_new(existing, found, min_gap_m=250)
    assert [r["n"] for r in new] == ["Woolworths Melonba"]


def test_is_full_supermarket_excludes_nonretail_and_convenience():
    assert is_full_supermarket("Woolworths Melonba") is True
    assert is_full_supermarket("IGA Plus Liquor North Richmond") is True
    assert is_full_supermarket("ALDI Distribution Centre") is False
    assert is_full_supermarket("Coles Online Hub Store Ocado - Sydney") is False
    assert is_full_supermarket("IGA X-press East Wahroonga") is False
    assert is_full_supermarket("SPAR Express Terrigal") is False


def test_merge_new_dedupes_same_brand_imprecise_pin():
    # existing Coles mapped ~300m from where Places pins it -> must NOT be re-added
    existing = [{"lat": -33.7600, "lng": 151.2100, "b": "Coles"}]
    found = [{"n": "Coles Forestville", "lat": -33.7627, "lng": 151.2100, "b": "Coles"}]  # ~300m
    assert merge_new(existing, found, min_gap_m=250, same_brand_gap_m=500) == []


def test_merge_new_excludes_nonretail():
    existing: list[dict] = []
    found = [{"n": "ALDI Distribution Centre", "lat": -33.9, "lng": 150.9, "b": "ALDI"}]
    assert merge_new(existing, found) == []


def test_merge_new_dedupes_within_found():
    existing: list[dict] = []
    found = [
        {"n": "Woolworths A", "lat": -33.70, "lng": 150.80, "b": "Woolworths"},
        {"n": "Woolworths A (again)", "lat": -33.7001, "lng": 150.8001, "b": "Woolworths"},  # ~15m dup
    ]
    new = merge_new(existing, found, min_gap_m=250)
    assert len(new) == 1
