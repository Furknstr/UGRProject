"""Tests for pipeline/validator.py — S8.

Each test corresponds to one or more emails from the golden set
(plan/expected_outcomes.json).  We test:
  - blocking/warning flags
  - resolved customer_id, pol, pod
  - requested_date resolution
  - missing_fields
  - status decision (READY / VALIDATION_REQUIRED)

We use FakeLLMParser + hand-built ParsedEmail objects so the tests run
without Ollama or a real DB.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ugr_booking.models.schemas import ExtractedBooking, LLMResponse
from ugr_booking.pipeline.preprocessor import ParsedEmail
from ugr_booking.pipeline.validator import BookingValidation, validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email(
    email_id: str,
    sender: str | None = "test@abcshipping.example",
    received_at: str = "2026-10-01 08:00",
    flags: list[str] | None = None,
    encoding_warning: bool = False,
    has_attachment_ref: bool = False,
    suspicious_patterns: list[str] | None = None,
    body_clean: str = "body text",
) -> ParsedEmail:
    return ParsedEmail(
        email_id=email_id,
        file_path=f"data/emails/{email_id}.txt",
        received_at=datetime.strptime(received_at, "%Y-%m-%d %H:%M"),
        sender=sender,
        flags=flags or [],
        encoding_warning=encoding_warning,
        has_attachment_ref=has_attachment_ref,
        suspicious_patterns=suspicious_patterns or [],
        body_clean=body_clean,
    )


def _llm(email_id: str, intent: str, bookings: list[ExtractedBooking]) -> LLMResponse:
    return LLMResponse(
        source_email_id=email_id,
        message_intent=intent,  # type: ignore[arg-type]
        bookings=bookings,
    )


def _first(result) -> BookingValidation:
    """Shortcut: first booking in the result."""
    assert result.bookings, "No bookings in result"
    return result.bookings[0]


# ---------------------------------------------------------------------------
# EMAIL-002: clean READY booking (QUANTITY_FROM_VIN_COUNT)
# ---------------------------------------------------------------------------

class TestEmail002:
    """'ex SAG 10/10/2026' → ESSAG + date. 3 VINs → QUANTITY_FROM_VIN_COUNT."""

    def test_status_ready(self):
        llm = _llm("EMAIL-002", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="JEDDAH",
                quantity=None,
                vin_list=["ANONVIN0000000001", "ANONVIN0000000002", "ANONVIN0000000003"],
                requested_date="10/10/2026",
            )
        ])
        email = _email("EMAIL-002", sender="info@xyzlojistik.example",
                       received_at="2026-10-02 09:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.status == "READY"
        assert bv.customer_id == "CUST-002"
        assert bv.pol == "ESSAG"
        assert bv.pod == "SAJED"
        assert bv.quantity == 3
        assert bv.quantity_source == "vin_count"
        assert bv.requested_date == "2026-10-10"
        assert "QUANTITY_FROM_VIN_COUNT" in bv.warning_flags
        assert not bv.blocking_flags


# ---------------------------------------------------------------------------
# EMAIL-005: MISSING_REQUIRED (pol/pod/date missing)
# ---------------------------------------------------------------------------

class TestEmail005:
    """10 VINs → QUANTITY_FROM_VIN_COUNT + MISSING_REQUIRED (pol, pod, requested_date)."""

    def test_missing_required(self):
        vins = [f"ANONVIN{str(i).zfill(10)}" for i in range(10)]
        llm = _llm("EMAIL-005", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive",
                pol=None,
                pod=None,
                quantity=None,
                vin_list=vins,
                requested_date=None,
            )
        ])
        email = _email("EMAIL-005", sender="orders@defauto.example",
                       received_at="2026-10-03 10:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.customer_id == "CUST-003"
        assert "MISSING_REQUIRED" in bv.blocking_flags
        assert "QUANTITY_FROM_VIN_COUNT" in bv.warning_flags
        assert bv.status == "VALIDATION_REQUIRED"
        assert "pol" in bv.missing_fields
        assert "pod" in bv.missing_fields
        assert "requested_date" in bv.missing_fields


# ---------------------------------------------------------------------------
# EMAIL-008: golden example → READY
# ---------------------------------------------------------------------------

class TestEmail008:
    """Gold standard — all fields present, READY, no blocking flags."""

    def test_ready_no_flags(self):
        llm = _llm("EMAIL-008", "CREATE_BOOKING", [
            ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA",
                pod="JEDDAH",
                cargo_description="5 NEW PICKUP TRUCKS",
                quantity=5,
                vin_list=[
                    "ANONVIN0000000019", "ANONVIN0000000020", "ANONVIN0000000021",
                    "ANONVIN0000000022", "ANONVIN0000000023",
                ],
                weight_raw="2180 KG",
                dims_raw="5.35 x 1.90 x 1.82 m",
                requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-008", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.status == "READY"
        assert bv.customer_id == "CUST-003"
        assert bv.pol == "ESBCN"
        assert bv.pod == "SAJED"
        assert bv.quantity == 5
        assert bv.requested_date == "2026-10-18"
        assert not bv.blocking_flags


# ---------------------------------------------------------------------------
# EMAIL-009: NUMBER_NORMALIZED (European weight + 2-digit year)
# ---------------------------------------------------------------------------

class TestEmail009:
    """'12.500,50' EU format; '27/11/26' 2-digit year; NUMBER_NORMALIZED."""

    def test_number_normalized(self):
        llm = _llm("EMAIL-009", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="GHI Logistics",
                pol="PIRAEUS",
                pod="JEDDAH",
                quantity=2,
                vin_list=["ANONVIN0000000024", "ANONVIN0000000025"],
                weight_raw="12.500,50 KG",
                requested_date="27/11/26",
            )
        ])
        email = _email("EMAIL-009", sender="hello@ghi-logistics.example",
                       received_at="2026-10-06 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.status == "READY"
        assert bv.customer_id == "CUST-004"
        assert bv.requested_date == "2026-11-27"
        assert bv.weight_kg_per_unit == pytest.approx(12500.5)
        assert "NUMBER_NORMALIZED" in bv.warning_flags
        assert not bv.blocking_flags


# ---------------------------------------------------------------------------
# EMAIL-010: UNKNOWN_CUSTOMER
# ---------------------------------------------------------------------------

class TestEmail010:
    """'J.K. Transport' is not in master data → UNKNOWN_CUSTOMER."""

    def test_unknown_customer(self):
        llm = _llm("EMAIL-010", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="J.K. Transport",
                pol="KOPER",
                pod="JEDDAH",
                quantity=2,
                vin_list=["ANONVIN0000000026", "ANONVIN0000000027"],
                requested_date="20/10/2026",
            )
        ])
        email = _email("EMAIL-010", sender="jk@unknown.example",
                       received_at="2026-10-07 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "UNKNOWN_CUSTOMER" in bv.blocking_flags
        assert bv.customer_id is None
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-011: INVALID_VIN
# ---------------------------------------------------------------------------

class TestEmail011:
    """'ANONVIN000000003X' fails VIN regex → INVALID_VIN."""

    def test_invalid_vin(self):
        llm = _llm("EMAIL-011", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA",
                pod="YANBU",
                quantity=4,
                vin_list=[
                    "ANONVIN0000000028",
                    "ANONVIN0000000029",
                    "ANONVIN0000000030",
                    "ANONVIN000000003X",   # invalid
                ],
                requested_date="24/10/2026",
            )
        ])
        email = _email("EMAIL-011", sender="booking@abcshipping.example",
                       received_at="2026-10-08 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "INVALID_VIN" in bv.blocking_flags
        assert bv.customer_id == "CUST-001"
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-019: VIN_QUANTITY_MISMATCH (qty=4, vins=5)
# ---------------------------------------------------------------------------

class TestEmail019:
    """Quantity 4 but 5 VINs in the list → VIN_QUANTITY_MISMATCH."""

    def test_vin_quantity_mismatch(self):
        llm = _llm("EMAIL-019", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive",
                pol="BARCELONA",
                pod="JEDDAH",
                quantity=4,
                vin_list=[
                    "ANONVIN0000000031", "ANONVIN0000000032", "ANONVIN0000000033",
                    "ANONVIN0000000034", "ANONVIN0000000035",
                ],
                requested_date="28/10/2026",
            )
        ])
        email = _email("EMAIL-019", sender="orders@defauto.example",
                       received_at="2026-10-09 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "VIN_QUANTITY_MISMATCH" in bv.blocking_flags
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-020: INVALID_DATE (31/02/2026)
# ---------------------------------------------------------------------------

class TestEmail020:
    """31/02/2026 is impossible → INVALID_DATE."""

    def test_invalid_date(self):
        llm = _llm("EMAIL-020", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="GHI Logistics",
                pol="PIRAEUS",
                pod="JEDDAH",
                quantity=2,
                vin_list=["ANONVIN0000000036", "ANONVIN0000000037"],
                requested_date="31/02/2026",
            )
        ])
        email = _email("EMAIL-020", sender="hello@ghi-logistics.example",
                       received_at="2026-10-10 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "INVALID_DATE" in bv.blocking_flags
        assert bv.requested_date is None
        assert "requested_date" in bv.missing_fields


# ---------------------------------------------------------------------------
# EMAIL-001: AMBIGUOUS_PORT + AMBIGUOUS_DATE
# ---------------------------------------------------------------------------

class TestEmail001:
    """POD 'JEDDAH / YANBU' → AMBIGUOUS_PORT. 'SEPTEMBER SAILING' → AMBIGUOUS_DATE."""

    def test_ambiguous_port_and_date(self):
        llm = _llm("EMAIL-001", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA",
                pod="JEDDAH / YANBU",
                quantity=None,
                vin_list=["ANONVIN0000000001", "ANONVIN0000000002", "ANONVIN0000000003"],
                requested_date="SEPTEMBER SAILING",
            )
        ])
        email = _email("EMAIL-001", sender="booking@abcshipping.example",
                       received_at="2026-10-01 07:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "AMBIGUOUS_PORT" in bv.blocking_flags
        assert "AMBIGUOUS_DATE" in bv.blocking_flags
        assert "QUANTITY_FROM_VIN_COUNT" in bv.warning_flags
        assert bv.customer_id == "CUST-001"
        assert bv.pod is None
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-018: AMBIGUOUS_DATE ('next week')
# ---------------------------------------------------------------------------

class TestEmail018:
    """'next week' is relative → AMBIGUOUS_DATE. Missing pol/pod → MISSING_REQUIRED."""

    def test_ambiguous_date_relative(self):
        llm = _llm("EMAIL-018", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping Ltd.",
                pol=None,
                pod=None,
                quantity=None,
                vin_list=["ANONVIN0000000038", "ANONVIN0000000039", "ANONVIN0000000040"],
                requested_date="next week",
            )
        ])
        email = _email("EMAIL-018", sender="booking@abcshipping.example",
                       received_at="2026-10-11 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "AMBIGUOUS_DATE" in bv.blocking_flags
        assert "MISSING_REQUIRED" in bv.blocking_flags
        assert bv.customer_id == "CUST-001"
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-021: UNIT_AMBIGUOUS (weight without unit)
# ---------------------------------------------------------------------------

class TestEmail021:
    """'Weight: 7200' — no unit → weight=None + UNIT_AMBIGUOUS warning."""

    def test_unit_ambiguous(self):
        llm = _llm("EMAIL-021", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="JEDDAH",
                quantity=None,
                vin_list=["ANONVIN0000000041", "ANONVIN0000000042"],
                weight_raw="7200",
                requested_date="02/11/2026",
            )
        ])
        email = _email("EMAIL-021", sender="info@xyzlojistik.example",
                       received_at="2026-10-12 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "UNIT_AMBIGUOUS" in bv.warning_flags
        assert bv.weight_kg_per_unit is None
        assert bv.status == "READY"
        assert bv.requested_date == "2026-11-02"


# ---------------------------------------------------------------------------
# EMAIL-025: EMPTY_BODY + MISSING_REQUIRED (customer_id from domain only)
# ---------------------------------------------------------------------------

class TestEmail025:
    """Body is empty; POL/POD/qty only in subject. Domain hints CUST-001 but doesn't resolve."""

    def test_empty_body_missing_required(self):
        llm = _llm("EMAIL-025", "CREATE_BOOKING", [
            ExtractedBooking(
                customer=None,  # LLM couldn't find customer
                pol="GENOA",
                pod="JEDDAH",
                quantity=3,
                vin_list=[],
                requested_date=None,
                cargo_description=None,
            )
        ])
        email = _email(
            "EMAIL-025",
            sender="someone@abcshipping.example",
            received_at="2026-10-13 08:00",
            flags=["EMPTY_BODY"],
        )
        result = validate(llm, email)
        bv = _first(result)
        assert "MISSING_REQUIRED" in bv.blocking_flags
        assert "EMPTY_BODY" in bv.warning_flags
        assert bv.customer_id is None
        assert bv.customer_suggestion == "CUST-001"
        assert "cargo_description" in bv.missing_fields
        assert "requested_date" in bv.missing_fields


# ---------------------------------------------------------------------------
# EMAIL-026: ENCODING_WARNING + MISSING_SENDER
# ---------------------------------------------------------------------------

class TestEmail026:
    """Encoding corruption in body → ENCODING_WARNING. No From header → MISSING_SENDER."""

    def test_encoding_warning(self):
        llm = _llm("EMAIL-026", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="JEDD\ufffdH",   # corrupted JEDDAH
                quantity=1,
                vin_list=["ANONVIN0000000043"],
                requested_date="12/11/2026",
            )
        ])
        email = _email(
            "EMAIL-026",
            sender=None,  # no From header
            received_at="2026-10-14 08:00",
            encoding_warning=True,
            flags=["MISSING_SENDER"],
        )
        result = validate(llm, email)
        bv = _first(result)
        assert "ENCODING_WARNING" in bv.blocking_flags
        assert "MISSING_SENDER" in bv.warning_flags
        assert bv.pod is None
        assert bv.requested_date == "2026-11-12"
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-029: MISSING_ATTACHMENT
# ---------------------------------------------------------------------------

class TestEmail029:
    """Body references an Excel attachment that isn't present → MISSING_ATTACHMENT."""

    def test_missing_attachment(self):
        llm = _llm("EMAIL-029", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA",
                pod="JEDDAH",
                quantity=None,
                vin_list=[],
                requested_date=None,
                cargo_description=None,
            )
        ])
        email = _email(
            "EMAIL-029",
            sender="booking@abcshipping.example",
            received_at="2026-10-15 08:00",
            has_attachment_ref=True,
        )
        result = validate(llm, email)
        bv = _first(result)
        assert "MISSING_ATTACHMENT" in bv.blocking_flags
        assert "MISSING_REQUIRED" in bv.blocking_flags
        assert "cargo_description" in bv.missing_fields
        assert "quantity" in bv.missing_fields
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-031: SUSPICIOUS_CONTENT + SENDER_CUSTOMER_MISMATCH
# ---------------------------------------------------------------------------

class TestEmail031:
    """Injection block in body + sender domain ≠ resolved customer."""

    def test_suspicious_and_mismatch(self):
        llm = _llm("EMAIL-031", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA",
                pod="JEDDAH",
                quantity=None,
                vin_list=["ANONVIN0000000044"],
                requested_date="22/11/2026",
            )
        ])
        email = _email(
            "EMAIL-031",
            # Sender domain = xyzlojistik → CUST-002, but customer resolves to CUST-001
            sender="attacker@xyzlojistik.example",
            received_at="2026-10-16 08:00",
            suspicious_patterns=["injection_marker"],
        )
        result = validate(llm, email)
        bv = _first(result)
        assert "SUSPICIOUS_CONTENT" in bv.blocking_flags
        assert "SENDER_CUSTOMER_MISMATCH" in bv.blocking_flags
        assert bv.customer_id == "CUST-001"
        assert bv.status == "VALIDATION_REQUIRED"


# ---------------------------------------------------------------------------
# EMAIL-017: split booking (two splits from one email)
# ---------------------------------------------------------------------------

class TestEmail017:
    """Single email with two booking splits (different PODs, both missing date)."""

    def test_two_splits(self):
        llm = _llm("EMAIL-017", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="JEDDAH",
                quantity=None,
                vin_list=["ANONVIN0000000045", "ANONVIN0000000046"],
                requested_date="same sailing week",
            ),
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="YANBU",
                quantity=None,
                vin_list=["ANONVIN0000000047"],
                requested_date="same sailing week",
            ),
        ])
        email = _email("EMAIL-017", sender="info@xyzlojistik.example",
                       received_at="2026-10-17 08:00")
        result = validate(llm, email)
        assert len(result.bookings) == 2
        bv0, bv1 = result.bookings
        assert bv0.split_index == 0
        assert bv1.split_index == 1
        assert bv0.pod == "SAJED"
        assert bv1.pod == "SAYNB"
        assert "MISSING_REQUIRED" in bv0.blocking_flags
        assert "MISSING_REQUIRED" in bv1.blocking_flags
        assert "AMBIGUOUS_DATE" in bv0.blocking_flags
        assert "AMBIGUOUS_DATE" in bv1.blocking_flags
        assert "QUANTITY_FROM_VIN_COUNT" in bv0.warning_flags
        assert "QUANTITY_FROM_VIN_COUNT" in bv1.warning_flags


# ---------------------------------------------------------------------------
# Non-booking intents produce no booking rows
# ---------------------------------------------------------------------------

class TestNonBooking:
    """NON_BOOKING and STATUS_INQUIRY emails produce empty bookings list."""

    @pytest.mark.parametrize("intent", ["NON_BOOKING", "STATUS_INQUIRY"])
    def test_no_bookings(self, intent):
        llm = LLMResponse(
            source_email_id="EMAIL-004",
            message_intent=intent,  # type: ignore[arg-type]
            bookings=[],
        )
        email = _email("EMAIL-004")
        result = validate(llm, email)
        assert result.bookings == []


# ---------------------------------------------------------------------------
# Port accent normalisation (EMAIL-022: JEDAH → SAJED)
# ---------------------------------------------------------------------------

class TestPortAccentNorm:
    """'JEDAH' (alias) resolves cleanly to SAJED — PORT_ACCENT_NORMALIZED warning."""

    def test_jedah_alias(self):
        llm = _llm("EMAIL-022", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA",
                pod="JEDAH",  # alias in master data
                quantity=1,
                vin_list=["ANONVIN0000000048"],
                requested_date="05/11/2026",
            )
        ])
        email = _email("EMAIL-022", sender="booking@abcshipping.example",
                       received_at="2026-10-18 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.pod == "SAJED"
        assert bv.status == "READY"


# ---------------------------------------------------------------------------
# EMAIL-027: NUMBER_NORMALIZED (12,400 KG weight + dims)
# ---------------------------------------------------------------------------

class TestEmail027:
    """'12,400 KG' comma = thousands sep → 12400. 2 VINs → QUANTITY_FROM_VIN_COUNT."""

    def test_weight_normalized(self):
        llm = _llm("EMAIL-027", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="GHI Logistics",
                pol="PIRAEUS",
                pod="YANBU",
                quantity=None,
                vin_list=["ANONVIN0000000049", "ANONVIN0000000050"],
                weight_raw="12,400 KG",
                requested_date="15/11/2026",
            )
        ])
        email = _email("EMAIL-027", sender="hello@ghi-logistics.example",
                       received_at="2026-10-19 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.status == "READY"
        assert bv.weight_kg_per_unit == pytest.approx(12400)
        assert "NUMBER_NORMALIZED" in bv.warning_flags
        assert "QUANTITY_FROM_VIN_COUNT" in bv.warning_flags
        assert not bv.blocking_flags


# ---------------------------------------------------------------------------
# Fuzzy POD suggestion from garbled raw (replaces hard-coded mapping)
# ---------------------------------------------------------------------------

class TestFuzzyPodSuggestion:
    """Garbled 'Jedd\ufffdh' should fuzzy-match to SAJED (evidence-based, not hard-coded)."""

    def test_garbled_jeddah_suggests_sajed(self):
        llm = _llm("EMAIL-FUZZ", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="Jedd\ufffdh",   # JEDDAH with replacement char
                quantity=1,
                vin_list=["ANONVIN0000000099"],
                requested_date="12/11/2026",
            )
        ])
        email = _email(
            "EMAIL-FUZZ",
            sender=None,
            received_at="2026-10-14 08:00",
            encoding_warning=True,
            flags=["MISSING_SENDER"],
        )
        result = validate(llm, email)
        bv = _first(result)
        assert bv.pod is None
        assert bv.pod_suggestion == "SAJED"
        assert bv.pod_suggestion_source == "fuzzy"
        assert "ENCODING_WARNING" in bv.blocking_flags

    def test_completely_garbled_no_suggestion(self):
        """Entirely replaced chars → no suggestion (not a hard-coded fallback)."""
        llm = _llm("EMAIL-FUZZ2", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="XYZ Lojistik",
                pol="SAGUNTO",
                pod="\ufffd\ufffd\ufffd\ufffd",
                quantity=1,
                vin_list=["ANONVIN0000000098"],
                requested_date="12/11/2026",
            )
        ])
        email = _email(
            "EMAIL-FUZZ2",
            sender=None,
            received_at="2026-10-14 08:00",
            encoding_warning=True,
            flags=["MISSING_SENDER"],
        )
        result = validate(llm, email)
        bv = _first(result)
        assert bv.pod is None
        assert bv.pod_suggestion is None
        assert bv.pod_suggestion_source is None


# ---------------------------------------------------------------------------
# IMPLAUSIBLE_VALUE — silent None becomes a visible warning
# ---------------------------------------------------------------------------

class TestImplausibleValue:
    """Values outside config range must produce IMPLAUSIBLE_VALUE, not silent None."""

    def test_weight_too_low(self):
        """1 KG is below WEIGHT_KG_MIN → IMPLAUSIBLE_VALUE; field set to None."""
        llm = _llm("EMAIL-IMP", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=1, vin_list=["ANONVIN0000000060"],
                weight_raw="1 KG", requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-IMP", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.weight_kg_per_unit is None
        assert "IMPLAUSIBLE_VALUE" in bv.warning_flags
        assert bv.status == "READY"   # warning only, not blocking

    def test_weight_too_high(self):
        """200,000 KG exceeds WEIGHT_KG_MAX → IMPLAUSIBLE_VALUE."""
        llm = _llm("EMAIL-IMP2", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=1, vin_list=["ANONVIN0000000061"],
                weight_raw="200000 KG", requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-IMP2", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.weight_kg_per_unit is None
        assert "IMPLAUSIBLE_VALUE" in bv.warning_flags

    def test_normal_weight_no_implausible(self):
        """2180 KG is within range → no IMPLAUSIBLE_VALUE."""
        llm = _llm("EMAIL-OK2", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=1, vin_list=["ANONVIN0000000062"],
                weight_raw="2180 KG", requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-OK2", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.weight_kg_per_unit == pytest.approx(2180.0)
        assert "IMPLAUSIBLE_VALUE" not in bv.warning_flags


# ---------------------------------------------------------------------------
# VIN normalisation and deduplication
# ---------------------------------------------------------------------------

class TestVinNormalisation:
    """VINs are upper-cased and stripped; duplicates within one booking → INVALID_VIN."""

    def test_lowercase_vin_normalised_and_valid(self):
        """Lowercase VIN is uppercased and passes the pattern check."""
        llm = _llm("EMAIL-VIN", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=1, vin_list=["anonvin0000000063"],
                requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-VIN", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.vin_list == ["ANONVIN0000000063"]
        assert "INVALID_VIN" not in bv.blocking_flags

    def test_duplicate_vin_produces_invalid_vin(self):
        """Two identical VINs in the same booking → INVALID_VIN (duplicate)."""
        vin = "ANONVIN0000000064"
        llm = _llm("EMAIL-DUP", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=2, vin_list=[vin, vin],
                requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-DUP", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "INVALID_VIN" in bv.blocking_flags


# ---------------------------------------------------------------------------
# QUANTITY_FROM_VIN_COUNT semantics fix
# ---------------------------------------------------------------------------

class TestQuantityFromVinCount:
    """QUANTITY_FROM_VIN_COUNT is emitted only when quantity is *inferred* from VINs."""

    def test_consistent_vins_no_warning(self):
        """qty=3 and 3 VINs → quantity_source='explicit', no QUANTITY_FROM_VIN_COUNT."""
        llm = _llm("EMAIL-QTY", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=3,
                vin_list=["ANONVIN0000000065", "ANONVIN0000000066", "ANONVIN0000000067"],
                requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-QTY", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.quantity == 3
        assert bv.quantity_source == "explicit"
        assert "QUANTITY_FROM_VIN_COUNT" not in bv.warning_flags
        assert bv.status == "READY"

    def test_no_explicit_qty_inferred_from_vins(self):
        """qty=None with 2 VINs → inferred, QUANTITY_FROM_VIN_COUNT emitted."""
        llm = _llm("EMAIL-QTYINF", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive", pol="BARCELONA", pod="JEDDAH",
                quantity=None,
                vin_list=["ANONVIN0000000068", "ANONVIN0000000069"],
                requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-QTYINF", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert bv.quantity == 2
        assert bv.quantity_source == "vin_count"
        assert "QUANTITY_FROM_VIN_COUNT" in bv.warning_flags


# ---------------------------------------------------------------------------
# email_flags preserved across all intents
# ---------------------------------------------------------------------------

class TestEmailFlagsPreservedForAllIntents:
    """Risk flags must appear in email_flags even when no booking rows are created."""

    @pytest.mark.parametrize("intent", ["UPDATE_BOOKING", "CANCEL_BOOKING", "NON_BOOKING"])
    def test_risk_flags_in_email_flags(self, intent):
        llm = LLMResponse(
            source_email_id="EMAIL-FLAG",
            message_intent=intent,  # type: ignore[arg-type]
            bookings=[],
        )
        email = _email(
            "EMAIL-FLAG",
            suspicious_patterns=["injection_marker"],
            encoding_warning=True,
        )
        result = validate(llm, email)
        assert result.bookings == []
        assert "SUSPICIOUS_CONTENT" in result.email_flags
        assert "ENCODING_WARNING" in result.email_flags


# ---------------------------------------------------------------------------
# _flag() hardens against typos — unknown code → ValueError
# ---------------------------------------------------------------------------

class TestFlagHardening:
    """_flag() must raise ValueError for codes not in BLOCKING_FLAGS | WARNING_FLAGS."""

    def test_unknown_code_raises(self):
        from ugr_booking.pipeline.validator import _flag
        with pytest.raises(ValueError, match="Unknown flag code"):
            _flag("TYPO_FLAG_XYZ")

    def test_known_blocking_code_ok(self):
        from ugr_booking.pipeline.validator import _flag
        f = _flag("UNKNOWN_CUSTOMER")
        assert f.severity == "blocking"

    def test_known_warning_code_ok(self):
        from ugr_booking.pipeline.validator import _flag
        f = _flag("NUMBER_NORMALIZED")
        assert f.severity == "warning"


# ---------------------------------------------------------------------------
# Relative-date regex word boundaries
# ---------------------------------------------------------------------------

class TestRelativeDateWordBoundary:
    """Word-boundary anchors prevent false matches inside longer words."""

    def test_thereafter_not_ambiguous(self):
        """'thereafter' should not match 'after' as a whole word."""
        from ugr_booking.pipeline.validator import _is_relative_date
        assert not _is_relative_date("thereafter")

    def test_clearly_relative_expressions(self):
        from ugr_booking.pipeline.validator import _is_relative_date
        assert _is_relative_date("next week sailing")
        assert _is_relative_date("same as usual")
        assert _is_relative_date("TBD")

    def test_plain_dates_not_relative(self):
        from ugr_booking.pipeline.validator import _is_relative_date
        assert not _is_relative_date("18/10/2026")
        assert not _is_relative_date("2026-11-27")


# ---------------------------------------------------------------------------
# AMBIGUOUS_PORT regression — missing_fields must NOT include the port when
# the diagnosis is ambiguity, not absence.
# ---------------------------------------------------------------------------

class TestAmbiguousPortNotInMissingFields:
    """AMBIGUOUS_PORT on POL/POD → flag added but field NOT in missing_fields.

    This pins the bug where the list-literal orchestrator evaluated all rules
    before any flags landed in bv.flags, so _rule_required_fields always saw
    an empty bv.flags and incorrectly added ambiguous ports to missing_fields.
    """

    def test_ambiguous_pol_not_in_missing_fields(self):
        """POL 'GENOA / SAGUNTO' → AMBIGUOUS_PORT; 'pol' must NOT be in missing_fields."""
        llm = _llm("EMAIL-AMBPOL", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA / SAGUNTO",   # slash → ambiguous
                pod="JEDDAH",
                quantity=2,
                vin_list=["ANONVIN0000000070", "ANONVIN0000000071"],
                requested_date="01/12/2026",
            )
        ])
        email = _email("EMAIL-AMBPOL", sender="booking@abcshipping.example",
                       received_at="2026-10-20 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "AMBIGUOUS_PORT" in bv.blocking_flags
        assert bv.pol is None
        # The key assertion — ambiguous port must not appear as a missing field
        assert "pol" not in bv.missing_fields

    def test_ambiguous_pod_not_in_missing_fields(self):
        """POD 'JEDDAH / YANBU' → AMBIGUOUS_PORT; 'pod' must NOT be in missing_fields."""
        llm = _llm("EMAIL-001-REG", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="ABC Shipping",
                pol="GENOA",
                pod="JEDDAH / YANBU",
                quantity=None,
                vin_list=["ANONVIN0000000001", "ANONVIN0000000002", "ANONVIN0000000003"],
                requested_date="SEPTEMBER SAILING",
            )
        ])
        email = _email("EMAIL-001-REG", sender="booking@abcshipping.example",
                       received_at="2026-10-01 07:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "AMBIGUOUS_PORT" in bv.blocking_flags
        assert bv.pod is None
        assert "pod" not in bv.missing_fields


# ---------------------------------------------------------------------------
# Dims UNIT_AMBIGUOUS fires on raw values (before plausibility drop)
# ---------------------------------------------------------------------------

class TestDimsUnitAmbiguousBeforePlausibility:
    """535 x 190 x 182 with no unit → UNIT_AMBIGUOUS must fire even though the
    values fall outside DIM_M_MAX (and would otherwise be silently dropped)."""

    def test_large_unitless_dims_produce_unit_ambiguous(self):
        llm = _llm("EMAIL-DIMAMB", "CREATE_BOOKING", [
            ExtractedBooking(
                customer="DEF Automotive",
                pol="BARCELONA",
                pod="JEDDAH",
                quantity=1,
                vin_list=["ANONVIN0000000072"],
                dims_raw="535 x 190 x 182",   # no unit, values look like cm
                requested_date="18/10/2026",
            )
        ])
        email = _email("EMAIL-DIMAMB", sender="orders@defauto.example",
                       received_at="2026-10-05 08:00")
        result = validate(llm, email)
        bv = _first(result)
        assert "UNIT_AMBIGUOUS" in bv.warning_flags
        # All dims should be None (they are implausibly large as metres)
        assert bv.length_m is None
        assert bv.width_m is None
        assert bv.height_m is None

