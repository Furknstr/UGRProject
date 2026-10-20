"""Enumerations for the UGR Booking AI pipeline."""

from enum import StrEnum


class MessageIntent(StrEnum):
    CREATE_BOOKING = "CREATE_BOOKING"
    UPDATE_BOOKING = "UPDATE_BOOKING"
    CANCEL_BOOKING = "CANCEL_BOOKING"
    STATUS_INQUIRY = "STATUS_INQUIRY"
    NON_BOOKING = "NON_BOOKING"
    UNKNOWN = "UNKNOWN"


class ProposedAction(StrEnum):
    create_booking = "create_booking"
    update_booking = "update_booking"
    cancel_booking = "cancel_booking"
    status_lookup = "status_lookup"
    no_action = "no_action"


class BookingStatus(StrEnum):
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
    READY = "READY"
    ASSIGNED = "ASSIGNED"    # reserved, not used in MVP
    CANCELLED = "CANCELLED"


class SourceEmailStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PREPROCESSED = "PREPROCESSED"
    PARSED = "PARSED"
    PARSE_FAILED = "PARSE_FAILED"
    BOOKING_CREATED = "BOOKING_CREATED"
    DUPLICATE = "DUPLICATE"
    ORPHAN = "ORPHAN"
    NO_ACTION = "NO_ACTION"
    NEEDS_REVIEW = "NEEDS_REVIEW"   # mutation rejected by security guard; operator must review
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Flag classification — single source of truth for guard decisions
# ---------------------------------------------------------------------------

BLOCKING_FLAGS: frozenset[str] = frozenset({
    "UNKNOWN_CUSTOMER",
    "UNKNOWN_PORT",
    "AMBIGUOUS_PORT",
    "INVALID_DATE",
    "AMBIGUOUS_DATE",
    "DATE_IN_PAST",
    "VIN_QUANTITY_MISMATCH",
    "INVALID_VIN",
    "ENCODING_WARNING",
    "MISSING_ATTACHMENT",
    "SUSPICIOUS_CONTENT",
    "SENDER_CUSTOMER_MISMATCH",
    "MISSING_REQUIRED",
    "HALLUCINATED_VALUE",
    "DUPLICATE_REF_CONFLICT",
})

WARNING_FLAGS: frozenset[str] = frozenset({
    "QUANTITY_FROM_VIN_COUNT",
    "NUMBER_NORMALIZED",
    "UNIT_AMBIGUOUS",
    "IMPLAUSIBLE_VALUE",      # parsed value outside configured plausible range; field set to None
    "PORT_ACCENT_NORMALIZED",
    "CONFLICTING_VALUES",
    "EMPTY_BODY",
    "MISSING_SENDER",
})
