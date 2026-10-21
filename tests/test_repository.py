"""Tests for repository query layer."""

import pytest
from ugr_booking.db.repository import (
    count_by_customer,
    filter_bookings,
    get_customer_display_name,
    get_latest_n,
    get_port_display_name,
    get_validation_required,
    review_queue,
    summary_stats,
)


def test_summary_stats():
    stats = summary_stats()
    assert stats["total_bookings"] == 26
    assert stats["ready_count"] == 9
    assert stats["validation_required_count"] == 16
    assert stats["cancelled_count"] == 1
    assert stats["total_emails"] == 34
    assert stats["orphan_count"] >= 4
    assert stats["duplicate_count"] >= 1


def test_count_by_customer():
    # Exact customer_id
    count, cid, name = count_by_customer("CUST-001")
    assert count == 7
    assert cid == "CUST-001"
    assert "ABC Shipping" in name

    # Alias / name folding
    count2, cid2, name2 = count_by_customer("DEF Auto")
    assert count2 == 5
    assert cid2 == "CUST-003"
    assert "DEF Automotive" in name2

    # Non-existent customer
    count3, cid3, name3 = count_by_customer("Completely Fake Customer 999")
    assert count3 == 0
    assert cid3 is None
    assert name3 is None


def test_filter_bookings():
    # Filter by status
    ready = filter_bookings(status="READY")
    assert len(ready) == 9

    val_req = filter_bookings(status="VALIDATION_REQUIRED")
    assert len(val_req) == 16

    cancelled = filter_bookings(status="CANCELLED")
    assert len(cancelled) == 1

    # Port folding: 'Izmir' and 'İZMİR' must return same results
    bookings_izmir1 = filter_bookings(pol="Izmir")
    bookings_izmir2 = filter_bookings(pol="İZMİR")
    assert len(bookings_izmir1) == len(bookings_izmir2)
    assert len(bookings_izmir1) > 0


def test_get_validation_required():
    val = get_validation_required()
    assert len(val) == 16
    for b in val:
        assert b.processing_status == "VALIDATION_REQUIRED"
        assert b.validation_flags is not None
        assert b.source_email is not None  # relationship accessible


def test_get_latest_n():
    latest = get_latest_n(3)
    assert len(latest) == 3
    # Check descending order by id
    assert latest[0].id > latest[1].id > latest[2].id


def test_review_queue():
    orphans = review_queue(category="orphan")
    orphan_ids = {o.email_id for o in orphans}
    assert "EMAIL-003" in orphan_ids
    assert "EMAIL-007" in orphan_ids
    assert "EMAIL-023" in orphan_ids
    assert "EMAIL-030" in orphan_ids

    duplicates = review_queue(category="duplicate")
    dup_ids = {d.email_id for d in duplicates}
    assert "EMAIL-012" in dup_ids

    all_reviews = review_queue()
    assert len(all_reviews) >= len(orphans) + len(duplicates)


def test_display_helpers():
    assert "ABC Shipping" in get_customer_display_name("CUST-001")
    assert "TRIZM" in get_port_display_name("TRIZM")
    assert get_customer_display_name(None) == "Unknown / Unresolved"
    assert get_port_display_name(None) == "Unknown / Unresolved"
