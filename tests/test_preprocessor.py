"""S3 tests — basic preprocessor."""

import pytest
from pathlib import Path
from datetime import datetime

from ugr_booking.pipeline.preprocessor import load_emails, ParsedEmail


DATA_PATH = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="module")
def emails():
    return load_emails(data_path=DATA_PATH)


def test_all_34_emails_loaded(emails):
    assert len(emails) == 34


def test_emails_sorted_chronologically(emails):
    dates = [e.received_at for e in emails]
    assert dates == sorted(dates), "Emails are not in chronological order"


def test_first_email_is_006(emails):
    """EMAIL-006 has the earliest received_at (2026-08-20 08:55)."""
    assert emails[0].email_id == "EMAIL-006"


def test_email_026_no_crash_and_no_sender(emails):
    """EMAIL-026 has no From header — sender must be None, not raise."""
    email_026 = next(e for e in emails if e.email_id == "EMAIL-026")
    assert email_026.sender is None


def test_email_026_encoding_warning(emails):
    """EMAIL-026 subject contains a replacement character → encoding_warning."""
    email_026 = next(e for e in emails if e.email_id == "EMAIL-026")
    assert email_026.encoding_warning is True
    assert "ENCODING_WARNING" in email_026.flags


def test_email_008_parsed_correctly(emails):
    e = next(e for e in emails if e.email_id == "EMAIL-008")
    assert e.sender == "export@defauto.example"
    assert e.subject == "BOOKINGREF-D100 - 5 pickup trucks BCN-JED"
    assert e.received_at == datetime(2026, 10, 3, 9, 25)
    assert "New booking request" in e.body_raw


def test_body_does_not_include_separator(emails):
    """The `--------` separator line must not appear in any body_raw."""
    for e in emails:
        assert "--------" not in e.body_raw.splitlines()[0] if e.body_raw.strip() else True


def test_email_025_empty_body(emails):
    """EMAIL-025 has an empty body after the separator."""
    e = next(e for e in emails if e.email_id == "EMAIL-025")
    assert e.body_raw.strip() == ""
