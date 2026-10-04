"""Final GLA tier: flagged estimates for supermarkets that footprints can't measure.

Runs LAST, after gla_supermarkets.py (OSM) and gla_msfootprints.py (Microsoft). Anything still
unmeasured is almost always an in-shopping-centre anchor — satellite/OSM only see the whole mall
building, so the store itself can't be isolated. The user accepts a clearly-flagged estimate for
these (and only these).

We use a per-brand FULL-LINE ANCHOR size, NOT the measured brand median: the measured set is pulled
down by small Metro/express formats and footprint under-tracing, whereas an unmeasured in-centre
store is virtually always a full-line anchor. Each gets sqm + asrc='estimate' + est_inmall=True so
the map can label it honestly ("estimate — in-centre anchor").
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

POI = Path("poi_data.json")

# Full-line anchor GLA by brand (m2) — typical in-centre store, not the small-format median.
ANCHOR = {
    "Woolworths": 3800,
    "Coles": 3700,
    "ALDI": 1700,
    "IGA": 1300,
    "Costco": 13900,
    "Harris Farm": 1400,
}
DEFAULT = 1500


def main():
    pois = json.loads(POI.read_text(encoding="utf-8"))
    sm = [p for p in pois if p.get("c") == "supermarket"]
    filled = 0
    for p in sm:
        if p.get("asrc") == "measured":
            p.pop("est_inmall", None)
            continue
        p["sqm"] = ANCHOR.get(p.get("b"), DEFAULT)
        p["asrc"] = "estimate"
        p["est_inmall"] = True
        filled += 1
    POI.write_text(json.dumps(pois, ensure_ascii=False, indent=1), encoding="utf-8")
    meas = sum(1 for p in sm if p.get("asrc") == "measured")
    print(f"supermarkets: {len(sm)}  measured: {meas}  in-centre estimate: {filled}")
    print("estimate brands: " + ", ".join(f"{k}={v}" for k, v in
          Counter(p.get("b") or "?" for p in sm if p.get("asrc") == "estimate").most_common()))
    print(f"EVERY supermarket now has a GLA ({meas} measured + {filled} flagged estimate = {len(sm)})")


if __name__ == "__main__":
    main()
