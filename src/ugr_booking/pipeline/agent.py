"""Pipeline agent — orchestrates preprocessor → LLM → DB for one email.

S5: skeleton that handles CREATE_BOOKING only (no validation, no guard).
Later steps (S8, S9) will add the validator and guard logic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ugr_booking.db.connection import get_session, init_db
from ugr_booking.enums import SourceEmailStatus
from ugr_booking.models.orm import Booking, ProcessingLog, SourceEmail
from ugr_booking.models.schemas import LLMResponse
from ugr_booking.pipeline.llm_parser import BaseLLMParser, OllamaLLMParser
from ugr_booking.pipeline.preprocessor import ParsedEmail, load_emails

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Persist helpers
# ---------------------------------------------------------------------------

def _upsert_source_email(session: Session, email: ParsedEmail) -> SourceEmail:
    """Insert or return existing SourceEmail row."""
    row = (
        session.query(SourceEmail)
        .filter_by(email_id=email.email_id)
        .first()
    )
    if row is None:
        row = SourceEmail(
            email_id=email.email_id,
            file_path=email.file_path,
            received_at=email.received_at,
            subject=email.subject,
            sender=email.sender,
            body_raw=email.body_raw,
            body_clean=email.body_clean,
            encoding_warning=email.encoding_warning,
            suspicious_patterns=None,
            processing_status=SourceEmailStatus.RECEIVED,
        )
        session.add(row)
        session.flush()
    return row


def _log(
    session: Session,
    email_id: str,
    stage: str,
    status: str,
    message: str | None = None,
    detail: dict | None = None,
    duration_ms: int | None = None,
) -> None:
    session.add(
        ProcessingLog(
            email_id=email_id,
            stage=stage,
            status=status,
            message=message,
            detail=detail,
            duration_ms=duration_ms,
        )
    )


# ---------------------------------------------------------------------------
# Core: process a single email
# ---------------------------------------------------------------------------

def process_email(
    email: ParsedEmail,
    llm: BaseLLMParser,
    session: Session,
) -> LLMResponse:
    """Run one email through the pipeline and persist results.

    Returns the raw :class:`LLMResponse` for inspection / testing.
    """
    t0 = datetime.now(UTC)

    # 1. Persist source email row
    src = _upsert_source_email(session, email)

    # 2. Call LLM
    try:
        llm_result = llm.parse(email.email_id, email.subject, email.body_clean)
        src.classified_intent = llm_result.message_intent
        src.processing_status = SourceEmailStatus.PARSED
        _log(session, email.email_id, "llm_parser", "success",
             message=llm_result.message_intent)
    except Exception as exc:
        src.processing_status = SourceEmailStatus.ERROR
        src.error_message = str(exc)
        _log(session, email.email_id, "llm_parser", "error", message=str(exc))
        session.commit()
        raise

    # 3. For CREATE_BOOKING: persist booking rows (no validation yet — S8)
    if llm_result.message_intent == "CREATE_BOOKING":
        for idx, b in enumerate(llm_result.bookings):
            # Check for existing row to avoid duplicate
            existing = (
                session.query(Booking)
                .filter_by(source_email_id=email.email_id, split_index=idx)
                .first()
            )
            if existing:
                continue

            booking = Booking(
                source_email_id=email.email_id,
                split_index=idx,
                booking_ref=b.booking_ref,
                customer_raw=b.customer,
                pol_raw=b.pol,
                pod_raw=b.pod,
                cargo_description=b.cargo_description,
                quantity=b.quantity,
                vin_list=b.vin_list or [],
                weight_raw=b.weight_raw,
                dims_raw=b.dims_raw,
                requested_date_raw=b.requested_date,
                vessel=b.vessel,
                voyage=b.voyage,
                rate_text=b.rate_text,
                contact_name=b.contact_name,
                contact_email=b.contact_email,
                raw_llm_output=llm_result.model_dump_json(),
                processing_status="VALIDATION_REQUIRED",
            )
            session.add(booking)

        src.processing_status = SourceEmailStatus.BOOKING_CREATED
        _log(session, email.email_id, "agent", "success",
             message=f"{len(llm_result.bookings)} booking(s) created")
    else:
        src.processing_status = SourceEmailStatus.NO_ACTION
        _log(session, email.email_id, "agent", "skipped",
             message=f"intent={llm_result.message_intent}, no booking created")

    duration_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
    _log(session, email.email_id, "pipeline", "success",
         duration_ms=duration_ms)

    session.commit()
    return llm_result


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_pipeline(
    llm: BaseLLMParser | None = None,
    db_url: str | None = None,
) -> list[LLMResponse]:
    """Process all emails end-to-end.

    Parameters
    ----------
    llm:
        Parser to use. Defaults to :class:`OllamaLLMParser`.
    db_url:
        SQLAlchemy URL. Defaults to the value in config / .env.
    """
    init_db(db_url)
    parser = llm or OllamaLLMParser()
    emails = load_emails()

    results: list[LLMResponse] = []
    session = get_session()
    try:
        for email in emails:
            log.info("Processing %s …", email.email_id)
            try:
                result = process_email(email, parser, session)
                results.append(result)
            except Exception as exc:
                log.error("Failed %s: %s", email.email_id, exc)
    finally:
        session.close()

    return results
