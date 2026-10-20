"""SQLAlchemy ORM models — four tables for the UGR Booking pipeline."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# source_emails — one row per incoming email
# ---------------------------------------------------------------------------

class SourceEmail(Base):
    __tablename__ = "source_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512))
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    subject: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(String(256))  # null for email-026

    body_raw: Mapped[str | None] = mapped_column(Text)
    body_clean: Mapped[str | None] = mapped_column(Text)
    quote_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # SHA-256 hex

    has_quote: Mapped[bool] = mapped_column(Boolean, default=False)
    has_attachment_ref: Mapped[bool] = mapped_column(Boolean, default=False)
    encoding_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    suspicious_patterns: Mapped[list | None] = mapped_column(JSON)  # list[str]

    classified_intent: Mapped[str | None] = mapped_column(String(32))
    proposed_action: Mapped[str | None] = mapped_column(String(32))
    processing_status: Mapped[str] = mapped_column(String(32), default="RECEIVED")

    duplicate_of: Mapped[str | None] = mapped_column(String(64))   # email_id
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )

    bookings: Mapped[list["Booking"]] = relationship(
        "Booking", back_populates="source_email", cascade="all, delete-orphan"
    )
    events: Mapped[list["BookingEvent"]] = relationship(
        "BookingEvent", back_populates="source_email"
    )
    logs: Mapped[list["ProcessingLog"]] = relationship(
        "ProcessingLog", back_populates="source_email_rel"
    )


# ---------------------------------------------------------------------------
# bookings — only real bookings (no orphans, no NON_BOOKING)
# ---------------------------------------------------------------------------

class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("source_email_id", "split_index", name="uq_email_split"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_ref: Mapped[str | None] = mapped_column(String(128))
    booking_ref_norm: Mapped[str | None] = mapped_column(String(128))

    source_email_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("source_emails.email_id"), nullable=False
    )
    split_index: Mapped[int] = mapped_column(Integer, default=0)

    customer_id: Mapped[str | None] = mapped_column(String(32))
    customer_raw: Mapped[str | None] = mapped_column(String(256))

    pol: Mapped[str | None] = mapped_column(String(16))   # resolved port code
    pol_raw: Mapped[str | None] = mapped_column(String(128))
    pod: Mapped[str | None] = mapped_column(String(16))
    pod_raw: Mapped[str | None] = mapped_column(String(128))

    cargo_description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(Integer)
    quantity_source: Mapped[str | None] = mapped_column(String(32))  # explicit | vin_count
    vin_list: Mapped[list | None] = mapped_column(JSON)              # list[str]

    weight_kg_per_unit: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    width_m: Mapped[float | None] = mapped_column(Float)
    height_m: Mapped[float | None] = mapped_column(Float)

    weight_raw: Mapped[str | None] = mapped_column(String(128))
    dims_raw: Mapped[str | None] = mapped_column(String(256))

    requested_date: Mapped[str | None] = mapped_column(String(16))  # YYYY-MM-DD
    requested_date_raw: Mapped[str | None] = mapped_column(String(128))

    vessel: Mapped[str | None] = mapped_column(String(128))
    voyage: Mapped[str | None] = mapped_column(String(64))
    rate_text: Mapped[str | None] = mapped_column(Text)
    contact_name: Mapped[str | None] = mapped_column(String(128))
    contact_email: Mapped[str | None] = mapped_column(String(256))

    processing_status: Mapped[str] = mapped_column(
        String(32), default="VALIDATION_REQUIRED"
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64))

    # JSON list of {field, code, severity, raw, suggestion}
    validation_flags: Mapped[list | None] = mapped_column(JSON)

    raw_llm_output: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    source_email: Mapped["SourceEmail"] = relationship(
        "SourceEmail", back_populates="bookings"
    )
    events: Mapped[list["BookingEvent"]] = relationship(
        "BookingEvent", back_populates="booking"
    )


# ---------------------------------------------------------------------------
# booking_events — audit trail
# ---------------------------------------------------------------------------

class BookingEvent(Base):
    __tablename__ = "booking_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("bookings.id"), nullable=True
    )
    booking_ref: Mapped[str | None] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(32))  # CREATED|UPDATED|CANCELLED|…
    source_email_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("source_emails.email_id"), nullable=True
    )
    delta: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )

    booking: Mapped["Booking | None"] = relationship(
        "Booking", back_populates="events"
    )
    source_email: Mapped["SourceEmail | None"] = relationship(
        "SourceEmail", back_populates="events"
    )


# ---------------------------------------------------------------------------
# processing_log — technical per-stage log
# ---------------------------------------------------------------------------

class ProcessingLog(Base):
    __tablename__ = "processing_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("source_emails.email_id"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(32))   # preprocessor|llm_parser|validator|agent
    status: Mapped[str] = mapped_column(String(16))  # success|error|skipped
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON) # proposed_action, guard_decision, reason
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), default=datetime.utcnow
    )

    source_email_rel: Mapped["SourceEmail | None"] = relationship(
        "SourceEmail", back_populates="logs"
    )
