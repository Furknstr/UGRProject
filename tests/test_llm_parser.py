"""S4 tests — LLM parser (FakeLLMParser only; no Ollama needed)."""

import pytest

from ugr_booking.models.schemas import ExtractedBooking, LLMResponse
from ugr_booking.pipeline.llm_parser import FakeLLMParser


# ---------------------------------------------------------------------------
# FakeLLMParser behaviour
# ---------------------------------------------------------------------------

def test_fake_returns_configured_response(fake_llm):
    result = fake_llm.parse("EMAIL-008", "subject", "body text")
    assert isinstance(result, LLMResponse)
    assert result.source_email_id == "EMAIL-008"
    assert result.message_intent == "CREATE_BOOKING"


def test_fake_returns_unknown_for_unmapped_email(fake_llm):
    result = fake_llm.parse("EMAIL-999", "subject", "body")
    assert result.message_intent == "UNKNOWN"
    assert result.bookings == []


def test_fake_booking_008_fields(fake_llm):
    result = fake_llm.parse("EMAIL-008", None, "")
    assert len(result.bookings) == 1
    b = result.bookings[0]
    assert b.customer == "DEF Automotive"
    assert b.pol == "BARCELONA"
    assert b.pod == "JEDDAH"
    assert b.quantity == 5
    assert len(b.vin_list) == 5
    assert b.booking_ref == "BOOKINGREF-D100"
    assert b.requested_date == "18/10/2026"
    assert b.weight_raw == "2180 KG"
    assert b.dims_raw == "5.35 x 1.90 x 1.82 m"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_llm_response_rejects_invalid_intent():
    with pytest.raises(Exception):
        LLMResponse(
            source_email_id="EMAIL-001",
            message_intent="INVALID_INTENT",  # type: ignore[arg-type]
        )


def test_llm_response_defaults():
    r = LLMResponse(source_email_id="EMAIL-001")
    assert r.message_intent == "UNKNOWN"
    assert r.bookings == []
    assert r.booking_ref is None


def test_extracted_booking_all_nullable():
    """All optional fields default to None / empty list — no required fields."""
    b = ExtractedBooking()
    assert b.customer is None
    assert b.vin_list == []


def test_fake_set_response_at_runtime():
    fake = FakeLLMParser()
    new_resp = LLMResponse(
        source_email_id="EMAIL-017",
        message_intent="CREATE_BOOKING",
        bookings=[ExtractedBooking(customer="XYZ Lojistik")],
    )
    fake.set_response("EMAIL-017", new_resp)
    result = fake.parse("EMAIL-017", None, "")
    assert result.message_intent == "CREATE_BOOKING"
    assert result.bookings[0].customer == "XYZ Lojistik"
