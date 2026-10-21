"""Repository layer — single source of truth for queries.

Both the Streamlit dashboard and the natural-language chatbot use this module.
No duplicate query logic in UI or routing components.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from ugr_booking.db.connection import get_session
from ugr_booking.master_data import (
    get_customer_by_id,
    get_port_by_code,
    resolve_customer,
    resolve_port,
    _customers,
    _ports,
)
from ugr_booking.models.orm import Booking, BookingEvent, SourceEmail
from ugr_booking.normalize import fold


@contextmanager
def managed_session(session: Session | None = None) -> Generator[Session, None, None]:
    """Provide a transactional session if none provided; do not close if caller-owned."""
    if session is not None:
        yield session
    else:
        sess = get_session()
        try:
            yield sess
        finally:
            sess.close()


# ---------------------------------------------------------------------------
# Metadata & helper lookups
# ---------------------------------------------------------------------------

def get_customer_display_name(customer_id: str | None) -> str:
    """Return friendly customer name, or customer_id, or 'Unknown'."""
    if not customer_id:
        return "Unknown / Unresolved"
    cust = get_customer_by_id(customer_id)
    if cust and cust.get("name"):
        return f"{cust['name']} ({customer_id})"
    return customer_id


def get_port_display_name(port_code: str | None) -> str:
    """Return friendly port name + code, or code, or 'Unknown'."""
    if not port_code:
        return "Unknown / Unresolved"
    port = get_port_by_code(port_code)
    if port and port.get("name"):
        return f"{port['name']} ({port_code})"
    return port_code


def list_known_customers() -> list[dict]:
    """Return master data customers list."""
    return _customers()


def list_known_ports() -> list[dict]:
    """Return master data ports list."""
    return _ports()


# ---------------------------------------------------------------------------
# Summary KPIs
# ---------------------------------------------------------------------------

def summary_stats(session: Session | None = None) -> dict[str, Any]:
    """Return operational summary stats for dashboard KPIs and chatbot.

    Note: As per specification, 'total_bookings' only counts rows in the `bookings`
    table (excluding non-bookings, status inquiries, and orphan emails).
    """
    with managed_session(session) as s:
        # Total bookings
        total_bookings: int = s.scalar(select(func.count(Booking.id))) or 0

        # Booking breakdown by status
        status_rows = s.execute(
            select(Booking.processing_status, func.count(Booking.id))
            .group_by(Booking.processing_status)
        ).all()
        status_counts: dict[str, int] = {status: count for status, count in status_rows}

        ready_count = status_counts.get("READY", 0)
        validation_required_count = status_counts.get("VALIDATION_REQUIRED", 0)
        cancelled_count = status_counts.get("CANCELLED", 0)

        # Customer distribution
        cust_rows = s.execute(
            select(Booking.customer_id, func.count(Booking.id))
            .group_by(Booking.customer_id)
        ).all()
        by_customer: dict[str, int] = {
            (cid if cid else "Unresolved"): count for cid, count in cust_rows
        }

        # POL distribution
        pol_rows = s.execute(
            select(Booking.pol, func.count(Booking.id))
            .group_by(Booking.pol)
        ).all()
        by_pol: dict[str, int] = {
            (pol if pol else "Unresolved"): count for pol, count in pol_rows
        }

        # POD distribution
        pod_rows = s.execute(
            select(Booking.pod, func.count(Booking.id))
            .group_by(Booking.pod)
        ).all()
        by_pod: dict[str, int] = {
            (pod if pod else "Unresolved"): count for pod, count in pod_rows
        }

        # Source emails breakdown
        total_emails: int = s.scalar(select(func.count(SourceEmail.id))) or 0
        email_status_rows = s.execute(
            select(SourceEmail.processing_status, func.count(SourceEmail.id))
            .group_by(SourceEmail.processing_status)
        ).all()
        email_status_counts: dict[str, int] = {st: count for st, count in email_status_rows}

        # Review queue total count
        orphan_count = email_status_counts.get("ORPHAN", 0)
        duplicate_count = email_status_counts.get("DUPLICATE", 0)
        needs_review_count = email_status_counts.get("NEEDS_REVIEW", 0)
        parse_failed_count = email_status_counts.get("PARSE_FAILED", 0)
        error_count = email_status_counts.get("ERROR", 0)

        suspicious_count: int = s.scalar(
            select(func.count(SourceEmail.id)).where(
                SourceEmail.suspicious_patterns.is_not(None),
                func.json_array_length(SourceEmail.suspicious_patterns) > 0,
            )
        ) or 0

        review_queue_total = s.scalar(
            select(func.count(SourceEmail.id)).where(
                or_(
                    SourceEmail.processing_status.in_(["ORPHAN", "DUPLICATE", "NEEDS_REVIEW", "PARSE_FAILED", "ERROR"]),
                    func.json_array_length(SourceEmail.suspicious_patterns) > 0,
                )
            )
        ) or 0

        return {
            "total_bookings": total_bookings,
            "ready_count": ready_count,
            "validation_required_count": validation_required_count,
            "cancelled_count": cancelled_count,
            "status_counts": status_counts,
            "by_customer": by_customer,
            "by_pol": by_pol,
            "by_pod": by_pod,
            "total_emails": total_emails,
            "email_status_counts": email_status_counts,
            "orphan_count": orphan_count,
            "duplicate_count": duplicate_count,
            "needs_review_count": needs_review_count,
            "suspicious_count": suspicious_count,
            "review_queue_total": review_queue_total,
        }


# ---------------------------------------------------------------------------
# Count by Customer
# ---------------------------------------------------------------------------

def count_by_customer(
    customer_input: str,
    session: Session | None = None,
) -> tuple[int, str | None, str | None]:
    """Count bookings for a customer by raw name, alias, or customer_id.

    Returns:
        tuple of (count, resolved_customer_id, resolved_customer_name)
        If customer is not recognized in master data or database:
        (0, None, None)
    """
    clean_input = customer_input.strip()
    if not clean_input:
        return 0, None, None

    # Check if input is already a valid customer_id (e.g. CUST-001)
    target_id: str | None = None
    target_name: str | None = None

    for c in _customers():
        if c["customer_id"].upper() == clean_input.upper():
            target_id = c["customer_id"]
            target_name = c["name"]
            break

    # Otherwise try resolving via master_data.resolve_customer
    if not target_id:
        resolved = resolve_customer(clean_input)
        if resolved:
            target_id = resolved
            cust = get_customer_by_id(resolved)
            target_name = cust["name"] if cust else resolved

    with managed_session(session) as s:
        if target_id:
            count: int = s.scalar(
                select(func.count(Booking.id)).where(Booking.customer_id == target_id)
            ) or 0
            return count, target_id, target_name

        # Fallback: check if raw text matched customer_raw in bookings
        folded = fold(clean_input)
        count = s.scalar(
            select(func.count(Booking.id)).where(
                or_(
                    func.lower(Booking.customer_raw) == clean_input.lower(),
                    func.lower(Booking.customer_id) == clean_input.lower(),
                )
            )
        ) or 0
        if count > 0:
            return count, clean_input, clean_input

        # Unknown customer
        return 0, None, None


# ---------------------------------------------------------------------------
# Filter Bookings
# ---------------------------------------------------------------------------

def filter_bookings(
    customer_id: str | None = None,
    pol: str | None = None,
    pod: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    session: Session | None = None,
) -> list[Booking]:
    """Filter bookings with optional dimensions.

    Resolves customer and ports if passed as names or raw strings.
    Eagerly loads source_email relationship to avoid DetachedInstanceError.
    """
    with managed_session(session) as s:
        stmt = (
            select(Booking)
            .options(joinedload(Booking.source_email))
            .order_by(Booking.id.asc())
        )

        # Resolve customer
        if customer_id and customer_id != "ALL":
            cid = customer_id
            if not cid.startswith("CUST-"):
                res = resolve_customer(cid)
                if res:
                    cid = res
            stmt = stmt.where(
                or_(
                    Booking.customer_id == cid,
                    Booking.customer_raw == customer_id,
                )
            )

        # Resolve POL
        if pol and pol != "ALL":
            resolved_pol = resolve_port(pol) or pol.upper()
            stmt = stmt.where(
                or_(
                    Booking.pol == resolved_pol,
                    Booking.pol_raw == pol,
                )
            )

        # Resolve POD
        if pod and pod != "ALL":
            resolved_pod = resolve_port(pod) or pod.upper()
            stmt = stmt.where(
                or_(
                    Booking.pod == resolved_pod,
                    Booking.pod_raw == pod,
                )
            )

        # Status
        if status and status != "ALL":
            stmt = stmt.where(Booking.processing_status == status.upper())

        # Dates (YYYY-MM-DD)
        if date_from:
            stmt = stmt.where(Booking.requested_date >= date_from)
        if date_to:
            stmt = stmt.where(Booking.requested_date <= date_to)

        results = s.scalars(stmt).unique().all()
        return list(results)


# ---------------------------------------------------------------------------
# Specialized queries
# ---------------------------------------------------------------------------

def get_validation_required(session: Session | None = None) -> list[Booking]:
    """Return all bookings with status VALIDATION_REQUIRED."""
    return filter_bookings(status="VALIDATION_REQUIRED", session=session)


def get_latest_n(n: int = 5, session: Session | None = None) -> list[Booking]:
    """Return latest n bookings by id descending."""
    with managed_session(session) as s:
        stmt = (
            select(Booking)
            .options(joinedload(Booking.source_email))
            .order_by(Booking.id.desc())
            .limit(n)
        )
        return list(s.scalars(stmt).unique().all())


def get_by_ref(booking_ref: str, session: Session | None = None) -> list[Booking]:
    """Find bookings matching reference (raw or normalized)."""
    norm = fold(booking_ref).replace("-", "").replace(" ", "")
    with managed_session(session) as s:
        stmt = (
            select(Booking)
            .options(joinedload(Booking.source_email))
            .where(
                or_(
                    Booking.booking_ref.ilike(f"%{booking_ref}%"),
                    Booking.booking_ref_norm.ilike(f"%{norm}%"),
                )
            )
        )
        return list(s.scalars(stmt).unique().all())


# ---------------------------------------------------------------------------
# Review Queue (source_emails)
# ---------------------------------------------------------------------------

def review_queue(
    category: str | None = None,
    session: Session | None = None,
) -> list[SourceEmail]:
    """Return source emails that require operational review.

    Categories:
    - 'orphan': processing_status == 'ORPHAN'
    - 'duplicate': processing_status == 'DUPLICATE'
    - 'needs_review': processing_status == 'NEEDS_REVIEW'
    - 'suspicious': has suspicious patterns
    - 'attachment': has attachment reference
    - 'empty_body': empty or whitespace body
    - 'error': processing_status in ('PARSE_FAILED', 'ERROR')
    - None: all above combined
    """
    with managed_session(session) as s:
        stmt = (
            select(SourceEmail)
            .options(joinedload(SourceEmail.bookings))
            .order_by(SourceEmail.id.asc())
        )

        cat = category.lower() if category else None

        if cat == "orphan":
            stmt = stmt.where(SourceEmail.processing_status == "ORPHAN")
        elif cat == "duplicate":
            stmt = stmt.where(SourceEmail.processing_status == "DUPLICATE")
        elif cat == "needs_review":
            stmt = stmt.where(SourceEmail.processing_status == "NEEDS_REVIEW")
        elif cat == "suspicious":
            stmt = stmt.where(
                SourceEmail.suspicious_patterns.is_not(None),
                func.json_array_length(SourceEmail.suspicious_patterns) > 0,
            )
        elif cat == "attachment":
            stmt = stmt.where(SourceEmail.has_attachment_ref.is_(True))
        elif cat == "empty_body":
            stmt = stmt.where(
                or_(
                    SourceEmail.body_clean.is_(None),
                    func.trim(SourceEmail.body_clean) == "",
                )
            )
        elif cat == "error":
            stmt = stmt.where(SourceEmail.processing_status.in_(["PARSE_FAILED", "ERROR"]))
        else:
            # All review conditions
            stmt = stmt.where(
                or_(
                    SourceEmail.processing_status.in_(["ORPHAN", "DUPLICATE", "NEEDS_REVIEW", "PARSE_FAILED", "ERROR"]),
                    SourceEmail.has_attachment_ref.is_(True),
                    func.json_array_length(SourceEmail.suspicious_patterns) > 0,
                    SourceEmail.body_clean.is_(None),
                    func.trim(SourceEmail.body_clean) == "",
                )
            )

        return list(s.scalars(stmt).unique().all())
