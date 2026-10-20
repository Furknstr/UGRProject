"""Pydantic schemas for LLM structured output — S4.

The LLM is asked to return a JSON object that matches :class:`LLMResponse`.
For emails that contain multiple bookings (e.g. EMAIL-017) the ``bookings``
list will have more than one entry.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Single extracted booking
# ---------------------------------------------------------------------------

class ExtractedBooking(BaseModel):
    """One booking extracted from an email body."""

    booking_ref: str | None = None
    customer: str | None = None          # raw name as written in the email
    pol: str | None = None               # raw port string (not yet resolved)
    pod: str | None = None
    cargo_description: str | None = None
    quantity: int | None = None
    vin_list: list[str] = Field(default_factory=list)
    weight_raw: str | None = None        # raw string e.g. "2 180 KG", "12,400"
    dims_raw: str | None = None          # raw string e.g. "5.35 x 1.90 x 1.82 m"
    requested_date: str | None = None    # raw string; normalised later in S6
    vessel: str | None = None
    voyage: str | None = None
    rate_text: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None


# ---------------------------------------------------------------------------
# Top-level LLM response
# ---------------------------------------------------------------------------

MessageIntent = Literal[
    "CREATE_BOOKING",
    "UPDATE_BOOKING",
    "CANCEL_BOOKING",
    "STATUS_INQUIRY",
    "NON_BOOKING",
    "UNKNOWN",
]


class LLMResponse(BaseModel):
    """Full structured response returned by the LLM parser."""

    source_email_id: str
    message_intent: MessageIntent = "UNKNOWN"

    # For UPDATE / CANCEL / STATUS the ref is at the top level
    booking_ref: str | None = None

    # One entry per booking; empty for non-booking intents
    bookings: list[ExtractedBooking] = Field(default_factory=list)
