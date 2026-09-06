from datetime import UTC, datetime, timedelta

import pytest

from pricewatch.scheduler import TrackedProductSchedule, due_product_ids, next_scan_at


def test_next_scan_defaults_to_four_minutes() -> None:
    last_scan = datetime(2026, 9, 5, 5, 0, tzinfo=UTC)
    assert next_scan_at(last_scan) == last_scan + timedelta(minutes=4)


def test_next_scan_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_scan_at(datetime(2026, 9, 5, 5, 0))


def test_due_products_are_globally_deduplicated() -> None:
    now = datetime(2026, 9, 5, 5, 10, tzinfo=UTC)
    schedules = [
        TrackedProductSchedule(product_id="p1", next_scan_at=now - timedelta(seconds=1)),
        TrackedProductSchedule(product_id="p1", next_scan_at=now - timedelta(minutes=1)),
        TrackedProductSchedule(product_id="p2", next_scan_at=now + timedelta(minutes=1)),
        TrackedProductSchedule(product_id="p3", next_scan_at=now),
    ]

    assert due_product_ids(schedules, now) == ("p1", "p3")
