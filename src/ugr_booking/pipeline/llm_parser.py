"""LLM parser — S4.

Sends a preprocessed email body to Ollama and returns a validated
:class:`~ugr_booking.models.schemas.LLMResponse`.

A ``FakeLLMParser`` is also provided here so that all other tests can
run without a live Ollama server.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod

from pydantic import ValidationError

from ugr_booking.config import OLLAMA_HOST, OLLAMA_MODEL
from ugr_booking.models.schemas import LLMResponse

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a freight booking data extractor for a RoRo shipping company.
Your job is to read an incoming customer email and extract structured
booking information from it.

RULES:
- Return ONLY valid JSON — no markdown, no prose, no code fences.
- Do not invent values. If a field is not mentioned, set it to null.
- For vin_list, return an empty list [] if no VINs are present.
- requested_date must be the raw date string exactly as written (e.g. "18/10/2026").
- weight_raw and dims_raw must be the raw strings as written (e.g. "2180 KG", "5.35 x 1.90 x 1.82 m").
- If the email contains multiple independent bookings, include each in the
  bookings array as a separate object.
- message_intent must be one of:
    CREATE_BOOKING | UPDATE_BOOKING | CANCEL_BOOKING | STATUS_INQUIRY |
    NON_BOOKING | UNKNOWN

JSON schema to follow exactly:
{
  "source_email_id": "<email_id>",
  "message_intent": "<intent>",
  "booking_ref": "<ref or null>",
  "bookings": [
    {
      "booking_ref": "<ref or null>",
      "customer": "<name or null>",
      "pol": "<port or null>",
      "pod": "<port or null>",
      "cargo_description": "<description or null>",
      "quantity": <integer or null>,
      "vin_list": ["<vin>", ...],
      "weight_raw": "<raw string or null>",
      "dims_raw": "<raw string or null>",
      "requested_date": "<raw date string or null>",
      "vessel": "<vessel or null>",
      "voyage": "<voyage or null>",
      "rate_text": "<rate or null>",
      "contact_name": "<name or null>",
      "contact_email": "<email or null>"
    }
  ]
}
"""


def _build_user_message(email_id: str, subject: str | None, body: str) -> str:
    return (
        f"Email-ID: {email_id}\n"
        f"Subject: {subject or '(none)'}\n"
        f"---\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseLLMParser(ABC):
    @abstractmethod
    def parse(
        self,
        email_id: str,
        subject: str | None,
        body: str,
    ) -> LLMResponse:
        ...


# ---------------------------------------------------------------------------
# Real Ollama parser
# ---------------------------------------------------------------------------

class OllamaLLMParser(BaseLLMParser):
    """Calls the local Ollama server to extract booking data."""

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
    ) -> None:
        import ollama  # lazy import so tests don't need the server

        self._model = model or OLLAMA_MODEL
        self._client = ollama.Client(host=host or OLLAMA_HOST)

    def parse(
        self,
        email_id: str,
        subject: str | None,
        body: str,
    ) -> LLMResponse:
        user_msg = _build_user_message(email_id, subject, body)

        response = self._client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            format="json",
            options={"temperature": 0},
        )

        raw_json: str = response.message.content  # type: ignore[union-attr]
        log.debug("LLM raw output for %s: %s", email_id, raw_json)

        try:
            data = json.loads(raw_json)
            # Ensure source_email_id is always correct
            data["source_email_id"] = email_id
            return LLMResponse.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            log.error("LLM parse failed for %s: %s", email_id, exc)
            # Return a minimal response so the pipeline can continue
            return LLMResponse(source_email_id=email_id, message_intent="UNKNOWN")


# ---------------------------------------------------------------------------
# Fake parser — used in all unit / integration tests
# ---------------------------------------------------------------------------

class FakeLLMParser(BaseLLMParser):
    """Returns a pre-configured :class:`LLMResponse` without Ollama.

    Usage in tests::

        fake = FakeLLMParser({
            "EMAIL-008": LLMResponse(
                source_email_id="EMAIL-008",
                message_intent="CREATE_BOOKING",
                bookings=[ExtractedBooking(customer="DEF Automotive", ...)],
            )
        })
    """

    def __init__(self, responses: dict[str, LLMResponse] | None = None) -> None:
        self._responses: dict[str, LLMResponse] = responses or {}

    def set_response(self, email_id: str, response: LLMResponse) -> None:
        self._responses[email_id] = response

    def parse(
        self,
        email_id: str,
        subject: str | None,  # noqa: ARG002
        body: str,            # noqa: ARG002
    ) -> LLMResponse:
        if email_id in self._responses:
            return self._responses[email_id]
        # Default: unknown intent, no bookings
        return LLMResponse(source_email_id=email_id, message_intent="UNKNOWN")
