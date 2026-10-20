"""S3 + S7 tests — email preprocessor."""

import pytest
from pathlib import Path
from datetime import datetime

from ugr_booking.pipeline.preprocessor import load_emails, ParsedEmail


DATA_PATH = Path(__file__).parent.parent / "data"


@pytest.fixture(scope="module")
def emails():
    return load_emails(data_path=DATA_PATH)


# ===========================================================================
# S3 — basic loading (regression guard)
# ===========================================================================

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


# ===========================================================================
# S7 — quote stripping
# ===========================================================================

class TestQuoteStripping:
    def test_015_quote_stripped_from_body_clean(self, emails):
        """EMAIL-015 has a '----- Previous message -----' block that must be stripped."""
        e = next(x for x in emails if x.email_id == "EMAIL-015")
        assert "Previous message" not in e.body_clean
        assert "----- Previous" not in e.body_clean

    def test_015_has_quote_field(self, emails):
        """EMAIL-015 had a quoted reply -> has_quote must be True."""
        e = next(x for x in emails if x.email_id == "EMAIL-015")
        assert e.has_quote is True

    def test_015_new_instruction_kept(self, emails):
        """The actual update instruction ('4 units only') must survive stripping."""
        e = next(x for x in emails if x.email_id == "EMAIL-015")
        assert "4 units only" in e.body_clean or "ANONVIN0000000031" in e.body_clean

    def test_030_original_message_stripped(self, emails):
        """EMAIL-030 has a '----- Original Message -----' block that must be stripped."""
        e = next(x for x in emails if x.email_id == "EMAIL-030")
        assert "Original Message" not in e.body_clean

    def test_030_has_quote_field(self, emails):
        """EMAIL-030 had a quoted reply -> has_quote must be True."""
        e = next(x for x in emails if x.email_id == "EMAIL-030")
        assert e.has_quote is True

    def test_030_cancel_instruction_kept(self, emails):
        """The cancel instruction must survive stripping."""
        e = next(x for x in emails if x.email_id == "EMAIL-030")
        assert "CANCEL" in e.body_clean or "CANCEL BOOKINGREF-X300" in e.body_clean

    def test_015_body_raw_still_has_quote(self, emails):
        """body_raw must be untouched — only body_clean is stripped."""
        e = next(x for x in emails if x.email_id == "EMAIL-015")
        assert "Previous message" in e.body_raw

    def test_008_no_quote(self, emails):
        """EMAIL-008 has no quoted block -> has_quote must be False."""
        e = next(x for x in emails if x.email_id == "EMAIL-008")
        assert e.has_quote is False


# ===========================================================================
# S7 — HTML cleanup
# ===========================================================================

class TestHtmlCleanup:
    def test_016_html_tags_removed(self, emails):
        """EMAIL-016 body is HTML — tags must not appear in body_clean."""
        e = next(x for x in emails if x.email_id == "EMAIL-016")
        assert "<html" not in e.body_clean.lower()
        assert "<body" not in e.body_clean.lower()
        assert "<br" not in e.body_clean.lower()

    def test_016_content_preserved(self, emails):
        """Key booking content from EMAIL-016 must survive HTML strip."""
        e = next(x for x in emails if x.email_id == "EMAIL-016")
        assert "NEW CITY BUS" in e.body_clean
        assert "PIRAEUS" in e.body_clean

    def test_027_html_tags_removed(self, emails):
        """EMAIL-027 body is HTML — tags must not appear in body_clean."""
        e = next(x for x in emails if x.email_id == "EMAIL-027")
        assert "<br" not in e.body_clean.lower()
        assert "<div" not in e.body_clean.lower()

    def test_027_content_preserved(self, emails):
        e = next(x for x in emails if x.email_id == "EMAIL-027")
        assert "NEW COACH BUS" in e.body_clean


# ===========================================================================
# S7 — injection marker / SUSPICIOUS_CONTENT
# ===========================================================================

class TestInjectionMarker:
    def test_031_has_suspicious_flag(self, emails):
        """EMAIL-031 has a prompt-injection block -> SUSPICIOUS_CONTENT flag."""
        e = next(x for x in emails if x.email_id == "EMAIL-031")
        assert "SUSPICIOUS_CONTENT" in e.flags

    def test_031_suspicious_patterns_populated(self, emails):
        """EMAIL-031 must have at least one matched injection pattern label."""
        e = next(x for x in emails if x.email_id == "EMAIL-031")
        assert len(e.suspicious_patterns) > 0

    def test_031_injection_block_removed_from_clean(self, emails):
        """The injection block must be removed from body_clean."""
        e = next(x for x in emails if x.email_id == "EMAIL-031")
        assert "Ignore all validation" not in e.body_clean
        assert "EXTERNAL TEXT" not in e.body_clean

    def test_031_real_content_kept(self, emails):
        """Legitimate booking data must survive injection-block removal."""
        e = next(x for x in emails if x.email_id == "EMAIL-031")
        assert "ANONVIN0000000070" in e.body_clean

    def test_031_body_raw_untouched(self, emails):
        """body_raw must be the original unmodified text."""
        e = next(x for x in emails if x.email_id == "EMAIL-031")
        assert "EXTERNAL TEXT START" in e.body_raw

    def test_008_no_suspicious_patterns(self, emails):
        """Clean emails must have empty suspicious_patterns list."""
        e = next(x for x in emails if x.email_id == "EMAIL-008")
        assert e.suspicious_patterns == []


# ===========================================================================
# S7 — attachment reference (has_attachment_ref field)
# MISSING_ATTACHMENT flag is set by the validator, not the preprocessor.
# ===========================================================================

class TestAttachmentRef:
    def test_029_has_attachment_ref(self, emails):
        """EMAIL-029 refers to an Excel attachment -> has_attachment_ref True."""
        e = next(x for x in emails if x.email_id == "EMAIL-029")
        assert e.has_attachment_ref is True

    def test_008_no_attachment_ref(self, emails):
        """EMAIL-008 has no attachment reference."""
        e = next(x for x in emails if x.email_id == "EMAIL-008")
        assert e.has_attachment_ref is False

    def test_missing_attachment_not_in_preprocessor_flags(self, emails):
        """MISSING_ATTACHMENT is the validator's job — preprocessor must not set it."""
        for e in emails:
            assert "MISSING_ATTACHMENT" not in e.flags, (
                f"{e.email_id} unexpectedly has MISSING_ATTACHMENT from preprocessor"
            )


# ===========================================================================
# S7 — body fingerprint / POSSIBLE_DUPLICATE
# ===========================================================================

class TestDuplicateFingerprint:
    def test_012_has_possible_duplicate_flag(self, emails):
        """EMAIL-012 is a re-send of EMAIL-008 → POSSIBLE_DUPLICATE."""
        e = next(x for x in emails if x.email_id == "EMAIL-012")
        assert "POSSIBLE_DUPLICATE" in e.flags

    def test_008_does_not_have_duplicate_flag(self, emails):
        """EMAIL-008 is the original — must NOT carry the duplicate flag."""
        e = next(x for x in emails if x.email_id == "EMAIL-008")
        assert "POSSIBLE_DUPLICATE" not in e.flags

    def test_all_emails_have_fingerprint(self, emails):
        """Every non-empty email must have a non-empty body_fingerprint."""
        for e in emails:
            if e.body_clean.strip():
                assert e.body_fingerprint, f"{e.email_id} has empty fingerprint"


# ===========================================================================
# S7 — MISSING_SENDER flag
# ===========================================================================

class TestMissingSenderFlag:
    def test_026_has_missing_sender_flag(self, emails):
        """EMAIL-026 has no From header → MISSING_SENDER flag."""
        e = next(x for x in emails if x.email_id == "EMAIL-026")
        assert "MISSING_SENDER" in e.flags

    def test_008_no_missing_sender_flag(self, emails):
        """EMAIL-008 has a valid sender → no MISSING_SENDER flag."""
        e = next(x for x in emails if x.email_id == "EMAIL-008")
        assert "MISSING_SENDER" not in e.flags
