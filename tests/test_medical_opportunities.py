"""Tests for build_medical_opportunities.build_opportunity_rows."""
from __future__ import annotations

from build_medical_opportunities import build_opportunity_rows


def test_build_opportunity_rows_filters_and_sorts():
    centres = [
        {
            "n": "Big",
            "addr": "1 St",
            "suburb": "X",
            "doctor_count": 9,
            "category": "large",
            "pharmacy_attached": False,
            "nearest_pharmacy_m": 600,
            "weekly_hours": 72,
            "doctor_count_source": "nhsd",
        },
        {
            "n": "Small",
            "category": "small",
            "doctor_count": 2,
            "pharmacy_attached": False,
            "nearest_pharmacy_m": 600,
            "weekly_hours": 72,
        },  # excluded (not large)
        {
            "n": "Attached",
            "category": "large",
            "doctor_count": 8,
            "pharmacy_attached": True,
            "nearest_pharmacy_m": 600,
            "weekly_hours": 72,
        },  # excluded (pharmacy attached)
    ]
    rows = build_opportunity_rows(centres)
    assert len(rows) == 1 and rows[0][0] == "Big"
