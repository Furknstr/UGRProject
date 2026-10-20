"""Shared pytest fixtures."""

import pytest

from ugr_booking.models.schemas import ExtractedBooking, LLMResponse
from ugr_booking.pipeline.llm_parser import FakeLLMParser


@pytest.fixture()
def fake_llm() -> FakeLLMParser:
    """Return a FakeLLMParser pre-loaded with a response for EMAIL-008."""
    booking_008 = ExtractedBooking(
        booking_ref="BOOKINGREF-D100",
        customer="DEF Automotive",
        pol="BARCELONA",
        pod="JEDDAH",
        cargo_description="5 NEW PICKUP TRUCKS",
        quantity=5,
        vin_list=[
            "ANONVIN0000000019",
            "ANONVIN0000000020",
            "ANONVIN0000000021",
            "ANONVIN0000000022",
            "ANONVIN0000000023",
        ],
        weight_raw="2180 KG",
        dims_raw="5.35 x 1.90 x 1.82 m",
        requested_date="18/10/2026",
    )
    response_008 = LLMResponse(
        source_email_id="EMAIL-008",
        message_intent="CREATE_BOOKING",
        bookings=[booking_008],
    )
    return FakeLLMParser({"EMAIL-008": response_008})
