"""Smoke test: generate_map.py emits a working medical-centres layer.

generate_map.py is intentionally kept out of the repo (it holds a hardcoded local
key), so this test skips wherever the file is absent — e.g. in CI.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def test_generated_map_has_medical_layer():
    if not Path("generate_map.py").exists():
        pytest.skip("generate_map.py not present (kept local, out of the public repo)")
    r = subprocess.run(
        [sys.executable, "generate_map.py"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    html = Path("pharmacy_map.html").read_text(encoding="utf-8")
    assert 'id="medicalToggle"' in html
    assert "var MEDICAL" in html
    assert "medicalIcon" in html
