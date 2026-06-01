from src.diff import compute_diffs


def _row(num: str, **extra: str) -> dict[str, str]:
    base = {
        "registration_number": num,
        "status": "Current",
        "address": "1 Foo St",
        "trading_name": "X",
        "suburb": "Sydney",
        "postcode": "2000",
        "licensee": "X Pty Ltd",
        "expiry_date": "2026-06-30",
    }
    base.update(extra)
    return base


def test_added_record_produces_added_change() -> None:
    previous: list[dict[str, str]] = []
    current = [_row("PC001")]
    changes = compute_diffs(previous, current)
    assert len(changes) == 1
    assert changes[0].change_type == "added"
    assert changes[0].registration_number == "PC001"


def test_removed_record_produces_removed_change() -> None:
    previous = [_row("PC001")]
    current: list[dict[str, str]] = []
    changes = compute_diffs(previous, current)
    assert [c.change_type for c in changes] == ["removed"]


def test_modified_field_produces_modified_change() -> None:
    previous = [_row("PC001", licensee="Old Co")]
    current = [_row("PC001", licensee="New Co")]
    changes = compute_diffs(previous, current)
    assert len(changes) == 1
    assert changes[0].change_type == "modified"
    assert changes[0].field_changed == "licensee"
    assert changes[0].old_value == "Old Co"
    assert changes[0].new_value == "New Co"


def test_idempotent_no_changes_when_identical() -> None:
    rows = [_row("PC001"), _row("PC002")]
    assert compute_diffs(rows, rows) == []


def test_scraped_at_and_raw_json_excluded_from_diff() -> None:
    previous = [_row("PC001") | {"scraped_at": "2025-01-01", "raw_json": "{}"}]
    current = [_row("PC001") | {"scraped_at": "2025-01-08", "raw_json": "{}"}]
    assert compute_diffs(previous, current) == []
