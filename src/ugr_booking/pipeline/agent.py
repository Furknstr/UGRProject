"""Pipeline agent — S9.

Orchestrates the full pipeline for one email:
  preprocessor → LLM → validator → guard → DB

Guard decisions (logged to processing_log.detail)
-------------------------------------------------
CREATE_BOOKING
  • READY      → create_booking (all fields resolved, no blocking flags)
  • VALIDATION_REQUIRED → create_booking (row inserted with status=VALIDATION_REQUIRED)
  • POSSIBLE_DUPLICATE  → skip new booking row; mark source_email as DUPLICATE
  • DUPLICATE_REF_CONFLICT → insert with blocking flag; status=VALIDATION_REQUIRED

UPDATE_BOOKING
  • booking_ref found → update_booking (delta from LLM output)
  • booking_ref missing → log ORPHAN_REF, status=ORPHAN

CANCEL_BOOKING
  • booking_ref found → cancel_booking
  • booking_ref missing → log ORPHAN_REF, status=ORPHAN

NON_BOOKING / STATUS_INQUIRY / UNKNOWN
  • No DB mutation; status=NO_ACTION

Every guard decision is written to processing_log as a JSON detail dict.

Responsibility boundary
-----------------------
- The *validator* (S8) is pure: it has no DB access.
- The *guard* (this module) is where DB-dependent flags
  (DUPLICATE_REF_CONFLICT, HALLUCINATED_VALUE) are added.
- booking_ops.py contains the raw CRUD; the guard contains the logic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ugr_booking.db.connection import get_session, init_db
from ugr_booking.enums import SourceEmailStatus
from ugr_booking.models.orm import Booking, SourceEmail
from ugr_booking.models.schemas import LLMResponse
from ugr_booking.pipeline.llm_parser import BaseLLMParser, OllamaLLMParser
from ugr_booking.pipeline.preprocessor import ParsedEmail, load_emails
from ugr_booking.pipeline.validator import validate
from ugr_booking.tools.booking_ops import (
    OpResult,
    cancel_booking,
    create_booking,
    find_by_ref,
    find_duplicate,
    update_booking,
)
from ugr_booking.tools.lookup import suggest_customer_from_domain

if TYPE_CHECKING:
    from ugr_booking.pipeline.validator import ValidationResult

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
            subject=getattr(email, "subject", None),
            sender=email.sender,
            body_raw=getattr(email, "body_raw", None),
            body_clean=email.body_clean,
            encoding_warning=email.encoding_warning,
            suspicious_patterns=email.suspicious_patterns or None,
            content_hash=getattr(email, "content_hash", None),
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
    from ugr_booking.models.orm import ProcessingLog
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
# Guard — per-intent decision logic
# ---------------------------------------------------------------------------

def _guard_create(
    session: Session,
    email: ParsedEmail,
    src: SourceEmail,
    llm_result: LLMResponse,
    validation: "ValidationResult",
) -> None:
    """Guard logic for CREATE_BOOKING intent."""
    raw_json = llm_result.model_dump_json()

    for bv in validation.bookings:
        # --- Duplicate detection (fingerprint / content hash) ---
        dup_src = find_duplicate(session, src)
        if dup_src is not None:
            _log(session, email.email_id, "guard", "skipped",
                 message="POSSIBLE_DUPLICATE",
                 detail={
                     "proposed_action": "no_action",
                     "guard_decision": "POSSIBLE_DUPLICATE",
                     "reason": f"content_hash matches {dup_src.email_id}",
                     "split_index": bv.split_index,
                 })
            src.processing_status = SourceEmailStatus.DUPLICATE
            src.duplicate_of = dup_src.email_id
            return  # skip all splits for this email

        # --- Create (or idempotent skip) ---
        result: OpResult = create_booking(session, email.email_id, bv, raw_json)

        guard_decision = "READY" if bv.status == "READY" else "VALIDATION_REQUIRED"

        _log(session, email.email_id, "guard", "success",
             message=result.action,
             detail={
                 "proposed_action": "create_booking",
                 "guard_decision": guard_decision,
                 "reason": result.reason,
                 "split_index": bv.split_index,
                 "blocking_flags": bv.blocking_flags,
                 "warning_flags": bv.warning_flags,
                 "booking_id": result.booking_id,
             })

    src.processing_status = SourceEmailStatus.BOOKING_CREATED


# ---------------------------------------------------------------------------
# Security pre-check shared by _guard_update and _guard_cancel
# ---------------------------------------------------------------------------

def _security_check(
    session: Session,
    email: "ParsedEmail",
    src: "SourceEmail",
    validation: "ValidationResult",
    booking_ref: str,
    proposed_action: str,
) -> str | None:
    """Run security gates before any mutating operation.

    Returns a ``guard_decision`` rejection string if the operation should be
    blocked, or ``None`` if the operation may proceed.

    Gates (in order)
    ----------------
    1. SUSPICIOUS_CONTENT or ENCODING_WARNING in email_flags
       → ``"REJECTED_SUSPICIOUS"``
    2. The target booking's customer_id does not match the domain-derived
       customer, or the sender domain is unknown
       → ``"REJECTED_SENDER_MISMATCH"``
    """
    # Gate 1 — email-level risk flags
    risky = {"SUSPICIOUS_CONTENT", "ENCODING_WARNING"}
    if risky.intersection(validation.email_flags):
        return "REJECTED_SUSPICIOUS"

    # Gate 2 — sender-domain vs booking customer
    suggested = suggest_customer_from_domain(email.sender) if email.sender else None
    booking = find_by_ref(session, booking_ref)
    if booking is not None:
        # None sender → domain unknown → reject
        # Known domain but different customer → reject
        if suggested is None or (booking.customer_id and suggested != booking.customer_id):
            return "REJECTED_SENDER_MISMATCH"

    return None  # all gates passed


def _guard_update(
    session: Session,
    email: ParsedEmail,
    src: SourceEmail,
    llm_result: LLMResponse,
    validation: "ValidationResult",
) -> None:
    """Guard logic for UPDATE_BOOKING intent.

    Security gates run before any DB write:
    - SUSPICIOUS_CONTENT / ENCODING_WARNING → rejected
    - Sender domain mismatch → rejected
    - Target booking is CANCELLED → rejected

    If booking_ref is absent or not found, the email is marked ORPHAN.
    """
    booking_raw = llm_result.bookings[0] if llm_result.bookings else None
    booking_ref = getattr(booking_raw, "booking_ref", None) if booking_raw else None

    if not booking_ref:
        _log(session, email.email_id, "guard", "error",
             message="ORPHAN_REF",
             detail={
                 "proposed_action": "update_booking",
                 "guard_decision": "ORPHAN_REF",
                 "reason": "no booking_ref in LLM output",
             })
        src.processing_status = SourceEmailStatus.ORPHAN
        return

    # Security pre-check
    rejection = _security_check(session, email, src, validation, booking_ref, "update_booking")
    if rejection:
        reason = (
            "email contains suspicious or encoding-corrupted content"
            if rejection == "REJECTED_SUSPICIOUS"
            else "sender domain does not match booking customer"
        )
        _log(session, email.email_id, "guard", "error",
             message=rejection,
             detail={
                 "proposed_action": "update_booking",
                 "guard_decision": rejection,
                 "reason": reason,
                 "booking_ref": booking_ref,
             })
        src.processing_status = SourceEmailStatus.NEEDS_REVIEW
        return

    # Build delta from non-None fields extracted by LLM
    delta = {
        k: v for k, v in {
            "quantity": booking_raw.quantity,
            "requested_date": booking_raw.requested_date,
            "requested_date_raw": booking_raw.requested_date,
            "vessel": booking_raw.vessel,
            "voyage": booking_raw.voyage,
            "rate_text": booking_raw.rate_text,
            "pol_raw": booking_raw.pol,
            "pod_raw": booking_raw.pod,
            "weight_raw": booking_raw.weight_raw,
            "cargo_description": booking_raw.cargo_description,
            "contact_name": booking_raw.contact_name,
            "contact_email": booking_raw.contact_email,
        }.items()
        if v is not None
    }

    result = update_booking(session, booking_ref, delta, email.email_id)

    if result.success:
        _log(session, email.email_id, "guard", "success",
             message=result.action,
             detail={
                 "proposed_action": "update_booking",
                 "guard_decision": "UPDATED",
                 "reason": result.reason,
                 "booking_ref": booking_ref,
             })
        src.processing_status = SourceEmailStatus.NO_ACTION
    elif result.action == "rejected":
        _log(session, email.email_id, "guard", "error",
             message=result.action,
             detail={
                 "proposed_action": "update_booking",
                 "guard_decision": "REJECTED_CANCELLED",
                 "reason": result.reason,
                 "booking_ref": booking_ref,
             })
        src.processing_status = SourceEmailStatus.NEEDS_REVIEW
    else:
        _log(session, email.email_id, "guard", "error",
             message=result.action,
             detail={
                 "proposed_action": "update_booking",
                 "guard_decision": "ORPHAN_REF",
                 "reason": result.reason,
                 "booking_ref": booking_ref,
             })
        src.processing_status = SourceEmailStatus.ORPHAN


def _guard_cancel(
    session: Session,
    email: ParsedEmail,
    src: SourceEmail,
    llm_result: LLMResponse,
    validation: "ValidationResult",
) -> None:
    """Guard logic for CANCEL_BOOKING intent.

    Security gates run before any DB write:
    - SUSPICIOUS_CONTENT / ENCODING_WARNING → rejected
    - Sender domain mismatch → rejected
    """
    booking_raw = llm_result.bookings[0] if llm_result.bookings else None
    booking_ref = getattr(booking_raw, "booking_ref", None) if booking_raw else None

    if not booking_ref:
        _log(session, email.email_id, "guard", "error",
             message="ORPHAN_REF",
             detail={
                 "proposed_action": "cancel_booking",
                 "guard_decision": "ORPHAN_REF",
                 "reason": "no booking_ref in LLM output",
             })
        src.processing_status = SourceEmailStatus.ORPHAN
        return

    # Security pre-check
    rejection = _security_check(session, email, src, validation, booking_ref, "cancel_booking")
    if rejection:
        reason = (
            "email contains suspicious or encoding-corrupted content"
            if rejection == "REJECTED_SUSPICIOUS"
            else "sender domain does not match booking customer"
        )
        _log(session, email.email_id, "guard", "error",
             message=rejection,
             detail={
                 "proposed_action": "cancel_booking",
                 "guard_decision": rejection,
                 "reason": reason,
                 "booking_ref": booking_ref,
             })
        src.processing_status = SourceEmailStatus.NEEDS_REVIEW
        return

    result = cancel_booking(session, booking_ref, email.email_id)

    if result.success:
        _log(session, email.email_id, "guard", "success",
             message=result.action,
             detail={
                 "proposed_action": "cancel_booking",
                 "guard_decision": "CANCELLED",
                 "reason": result.reason,
                 "booking_ref": booking_ref,
             })
    else:
        _log(session, email.email_id, "guard", "error",
             message=result.action,
             detail={
                 "proposed_action": "cancel_booking",
                 "guard_decision": "ORPHAN_REF",
                 "reason": result.reason,
                 "booking_ref": booking_ref,
             })
        src.processing_status = SourceEmailStatus.ORPHAN
        return

    src.processing_status = SourceEmailStatus.NO_ACTION


# ---------------------------------------------------------------------------
# Core: process a single email
# ---------------------------------------------------------------------------

def process_email(
    email: ParsedEmail,
    llm: BaseLLMParser,
    session: Session,
) -> LLMResponse:
    """Run one email through the full pipeline and persist results.

    Pipeline stages
    ---------------
    1. Persist SourceEmail (upsert).
    2. Call LLM parser.
    3. Run validator (pure, no DB).
    4. Run guard (DB-aware decisions).
    5. Commit.

    Returns the raw :class:`LLMResponse` for inspection / testing.
    """
    t0 = datetime.now(UTC)

    # 1. Persist source email row
    src = _upsert_source_email(session, email)

    # 2. Call LLM
    try:
        llm_result = llm.parse(email.email_id, getattr(email, "subject", ""), email.body_clean)
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

    # 3. Validate (pure — no DB access)
    validation = validate(llm_result, email)
    _log(session, email.email_id, "validator", "success",
         message=f"intent={llm_result.message_intent} splits={len(validation.bookings)}",
         detail={"email_flags": validation.email_flags})

    # 4. Guard — intent-based routing
    intent = llm_result.message_intent

    if intent == "CREATE_BOOKING":
        _guard_create(session, email, src, llm_result, validation)

    elif intent == "UPDATE_BOOKING":
        _guard_update(session, email, src, llm_result, validation)

    elif intent == "CANCEL_BOOKING":
        _guard_cancel(session, email, src, llm_result, validation)

    else:
        # NON_BOOKING, STATUS_INQUIRY, UNKNOWN — no DB mutation
        src.processing_status = SourceEmailStatus.NO_ACTION
        _log(session, email.email_id, "guard", "skipped",
             message=f"intent={intent}, no action",
             detail={"proposed_action": "no_action",
                     "guard_decision": "NO_ACTION"})

    # 5. Timing log + commit
    duration_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
    _log(session, email.email_id, "pipeline", "success", duration_ms=duration_ms)

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
                session.rollback()
    finally:
        session.close()

    return results
