from __future__ import annotations

from dataclasses import dataclass

EXCLUDED_FROM_DIFF = {"scraped_at", "raw_json"}


@dataclass(frozen=True)
class Change:
    change_type: str
    registration_number: str
    field_changed: str
    old_value: str
    new_value: str


def _index(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["registration_number"]: r for r in rows if r.get("registration_number")}


def compute_diffs(
    previous: list[dict[str, str]], current: list[dict[str, str]]
) -> list[Change]:
    prev = _index(previous)
    curr = _index(current)
    changes: list[Change] = []

    for num in sorted(curr.keys() - prev.keys()):
        c = curr[num]
        changes.append(Change("added", num, "trading_name", "",
                              f"{c.get('trading_name', '')} ({c.get('suburb', '')})"))

    for num in sorted(prev.keys() - curr.keys()):
        p = prev[num]
        changes.append(Change("removed", num, "trading_name",
                              f"{p.get('trading_name', '')} ({p.get('suburb', '')})", ""))

    for num in sorted(prev.keys() & curr.keys()):
        a, b = prev[num], curr[num]
        for field in sorted(set(a) | set(b)):
            if field in EXCLUDED_FROM_DIFF:
                continue
            av, bv = str(a.get(field, "")), str(b.get(field, ""))
            if av != bv:
                changes.append(Change("modified", num, field, av, bv))

    return changes
