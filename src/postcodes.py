from __future__ import annotations


def nsw_postcodes() -> list[str]:
    return [f"{n:04d}" for n in range(2000, 3000)]
