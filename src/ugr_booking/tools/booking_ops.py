"""Booking operations — S9.

Low-level DB operations used by the agent guard.  Each function takes a
SQLAlchemy session and returns a structured result so the agent can log
the guard decision without mixing persistence logic with business logic.

Operations
----------
create_booking   — insert a Booking row from a validated BookingValidation
update_booking   — apply a delta to an existing Booking (by booking_ref)
cancel_booking   — mark an existing Booking as CANCELLED
find_by_ref      — look up a Booking by normalised booking_ref
find_duplicate   — detect fingerprint-based duplicate for a source email
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ugr_booking.models.orm import Booking, BookingEvent, SourceEmail


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_ref(raw: str | None) -> str | None:
    """Normalise a booking reference: upper-case, strip spaces and hyphens."""
    if not raw:
        return None
    return re.sub(r"[\s\-]+", "", raw).upper()


def _booking_fingerprint(source_email_id: str, split_index: int) -> str:
    """Stable fingerprint for a (email, split) pair used for dedup checks."""
    return hashlib.sha256(
        f"{source_email_id}:{split_index}".encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Result types (plain dataclasses — no ORM dependency in callers)
# ---------------------------------------------------------------------------

@dataclass
class OpResult:
    """Outcome of a single booking operation."""

    success: bool
    action: str           # "created" | "updated" | "cancelled" | "skipped" | "error"
    booking_id: int | None = None
    booking_ref: str | None = None
    reason: str | None = None   # human-readable, written to processing_log


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------

def find_by_ref(session: Session, booking_ref: str) -> Booking | None:
    """Return the first Booking matching the normalised *booking_ref*, or None."""
    norm = _norm_ref(booking_ref)
    if not norm:
        return None
    return (
        session.query(Booking)
        .filter(Booking.booking_ref_norm == norm)
        .first()
    )


def find_duplicate(session: Session, email: SourceEmail) -> SourceEmail | None:
    """Return an earlier SourceEmail with the same content_hash, or None.

    Uses the SHA-256 content fingerprint computed by the preprocessor.
    The calling agent marks the new email as DUPLICATE if a match is found.
    """
    if not email.content_hash:
        return None
    return (
        session.query(SourceEmail)
        .filter(
            SourceEmail.content_hash == email.content_hash,
            SourceEmail.email_id != email.email_id,
        )
        .order_by(SourceEmail.created_at)
        .first()
    )


def create_booking(
    session: Session,
    source_email_id: str,
    bv,              # BookingValidation (imported lazily to avoid circular)
    raw_llm_json: str,
) -> OpResult:
    """Insert a new Booking row from a validated BookingValidation.

    Idempotent: if the (source_email_id, split_index) row already exists,
    returns a skipped result without modifying the DB.

    Parameters
    ----------
    session:
        Active SQLAlchemy session.
    source_email_id:
        The email_id this booking comes from.
    bv:
        A :class:`~ugr_booking.pipeline.validator.BookingValidation` instance.
    raw_llm_json:
        The serialised LLMResponse for audit.
    """
    existing = (
        session.query(Booking)
        .filter_by(source_email_id=source_email_id, split_index=bv.split_index)
        .first()
    )
    if existing:
        return OpResult(
            success=True,
            action="skipped",
            booking_id=existing.id,
            booking_ref=existing.booking_ref,
            reason="duplicate (email_id, split_index) — idempotent skip",
        )

    norm_ref = _norm_ref(bv.booking_ref)
    # Guard: DUPLICATE_REF_CONFLICT — same booking_ref already exists elsewhere
    dup_ref: Booking | None = None
    if norm_ref:
        dup_ref = (
            session.query(Booking)
            .filter(
                Booking.booking_ref_norm == norm_ref,
                Booking.source_email_id != source_email_id,
            )
            .first()
        )

    flags_json = [
        {
            "code": f.code,
            "severity": f.severity,
            "field": f.field,
            "raw": f.raw,
            "suggestion": f.suggestion,
        }
        for f in bv.flags
    ]

    # Add DUPLICATE_REF_CONFLICT flag when needed
    if dup_ref:
        flags_json.append({
            "code": "DUPLICATE_REF_CONFLICT",
            "severity": "blocking",
            "field": "booking_ref",
            "raw": bv.booking_ref,
            "suggestion": str(dup_ref.source_email_id),
        })

    # Determine persisted status
    blocking = [f for f in flags_json if f["severity"] == "blocking"]
    persisted_status = "VALIDATION_REQUIRED" if blocking else "READY"

    booking = Booking(
        source_email_id=source_email_id,
        split_index=bv.split_index,
        booking_ref=bv.booking_ref,
        booking_ref_norm=norm_ref,
        customer_id=bv.customer_id,
        customer_raw=bv.customer_raw,
        pol=bv.pol,
        pol_raw=bv.pol_raw,
        pod=bv.pod,
        pod_raw=bv.pod_raw,
        cargo_description=bv.cargo_description,
        quantity=bv.quantity,
        quantity_source=bv.quantity_source,
        vin_list=bv.vin_list,
        weight_kg_per_unit=bv.weight_kg_per_unit,
        weight_raw=bv.weight_raw,
        length_m=bv.length_m,
        width_m=bv.width_m,
        height_m=bv.height_m,
        dims_raw=bv.dims_raw,
        requested_date=bv.requested_date,
        requested_date_raw=bv.requested_date_raw,
        vessel=bv.vessel,
        voyage=bv.voyage,
        rate_text=bv.rate_text,
        contact_name=bv.contact_name,
        contact_email=bv.contact_email,
        validation_flags=flags_json,
        processing_status=persisted_status,
        fingerprint=_booking_fingerprint(source_email_id, bv.split_index),
        raw_llm_output=raw_llm_json,
    )
    session.add(booking)
    session.flush()  # populate booking.id

    # Audit event
    session.add(BookingEvent(
        booking_id=booking.id,
        booking_ref=bv.booking_ref,
        event_type="CREATED",
        source_email_id=source_email_id,
        delta=None,
        notes=f"status={persisted_status}, flags={len(flags_json)}",
    ))

    return OpResult(
        success=True,
        action="created",
        booking_id=booking.id,
        booking_ref=bv.booking_ref,
        reason=f"status={persisted_status}",
    )


def update_booking(
    session: Session,
    booking_ref: str,
    delta: dict,
    source_email_id: str,
) -> OpResult:
    """Apply *delta* fields to an existing Booking identified by *booking_ref*.

    Only non-None values in *delta* overwrite existing fields.  The original
    values are captured in the BookingEvent for audit.

    Parameters
    ----------
    delta:
        Mapping of field name → new value.  Only whitelisted fields are applied.
    """
    booking = find_by_ref(session, booking_ref)
    if booking is None:
        return OpResult(
            success=False,
            action="error",
            reason=f"ORPHAN_REF: booking_ref {booking_ref!r} not found in DB",
        )

    if booking.processing_status == "CANCELLED":
        return OpResult(
            success=False,
            action="rejected",
            booking_id=booking.id,
            booking_ref=booking.booking_ref,
            reason="booking is CANCELLED — updates are not allowed",
        )

    _UPDATABLE = {
        "quantity", "requested_date", "requested_date_raw",
        "vessel", "voyage", "rate_text",
        "pol", "pol_raw", "pod", "pod_raw",
        "weight_kg_per_unit", "weight_raw",
        "length_m", "width_m", "height_m", "dims_raw",
        "cargo_description", "contact_name", "contact_email",
    }

    old_vals: dict = {}
    for field_name, new_val in delta.items():
        if field_name not in _UPDATABLE:
            continue
        if new_val is None:
            continue
        old_vals[field_name] = getattr(booking, field_name, None)
        setattr(booking, field_name, new_val)

    booking.updated_at = datetime.now(UTC).replace(tzinfo=None)

    session.add(BookingEvent(
        booking_id=booking.id,
        booking_ref=booking.booking_ref,
        event_type="UPDATED",
        source_email_id=source_email_id,
        delta={"before": old_vals, "after": delta},
        notes=f"fields changed: {list(old_vals.keys())}",
    ))

    return OpResult(
        success=True,
        action="updated",
        booking_id=booking.id,
        booking_ref=booking.booking_ref,
        reason=f"fields updated: {list(old_vals.keys())}",
    )


def cancel_booking(
    session: Session,
    booking_ref: str,
    source_email_id: str,
    notes: str | None = None,
) -> OpResult:
    """Mark a Booking as CANCELLED and write a CANCELLED BookingEvent.

    Returns an error result (not an exception) if the booking_ref is unknown
    (orphan cancel — the agent logs it but does not raise).
    """
    booking = find_by_ref(session, booking_ref)
    if booking is None:
        return OpResult(
            success=False,
            action="error",
            reason=f"ORPHAN_REF: booking_ref {booking_ref!r} not found — cannot cancel",
        )

    if booking.processing_status == "CANCELLED":
        return OpResult(
            success=True,
            action="skipped",
            booking_id=booking.id,
            booking_ref=booking.booking_ref,
            reason="already CANCELLED — idempotent skip",
        )

    booking.processing_status = "CANCELLED"
    booking.updated_at = datetime.now(UTC).replace(tzinfo=None)

    session.add(BookingEvent(
        booking_id=booking.id,
        booking_ref=booking.booking_ref,
        event_type="CANCELLED",
        source_email_id=source_email_id,
        delta=None,
        notes=notes or "cancelled via email instruction",
    ))

    return OpResult(
        success=True,
        action="cancelled",
        booking_id=booking.id,
        booking_ref=booking.booking_ref,
        reason="CANCELLED",
    )
