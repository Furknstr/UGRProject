"""S5 + S9 tests — pipeline: preprocessor → LLM → validator → guard → DB."""

import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ugr_booking.models.orm import Base, Booking, BookingEvent, ProcessingLog, SourceEmail
from ugr_booking.models.schemas import ExtractedBooking, LLMResponse
from ugr_booking.pipeline.agent import process_email
from ugr_booking.pipeline.llm_parser import FakeLLMParser
from ugr_booking.pipeline.preprocessor import load_emails, ParsedEmail

DATA_PATH = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def in_memory_session():
    """Spin up a fresh in-memory SQLite DB for each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def email_008():
    emails = load_emails(data_path=DATA_PATH)
    return next(e for e in emails if e.email_id == "EMAIL-008")


@pytest.fixture()
def fake_llm_008():
    response = LLMResponse(
        source_email_id="EMAIL-008",
        message_intent="CREATE_BOOKING",
        bookings=[
            ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA",
                pod="JEDDAH",
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
        ],
    )
    return FakeLLMParser({"EMAIL-008": response})


# ---------------------------------------------------------------------------
# S5 tests (vertical slice — preserved from before)
# ---------------------------------------------------------------------------

def test_email_008_creates_source_row(email_008, fake_llm_008, in_memory_session):
    process_email(email_008, fake_llm_008, in_memory_session)
    row = in_memory_session.query(SourceEmail).filter_by(email_id="EMAIL-008").first()
    assert row is not None
    assert row.email_id == "EMAIL-008"


def test_email_008_creates_booking_row(email_008, fake_llm_008, in_memory_session):
    process_email(email_008, fake_llm_008, in_memory_session)
    booking = in_memory_session.query(Booking).filter_by(source_email_id="EMAIL-008").first()
    assert booking is not None
    assert booking.source_email_id == "EMAIL-008"


def test_email_008_booking_has_correct_fields(email_008, fake_llm_008, in_memory_session):
    process_email(email_008, fake_llm_008, in_memory_session)
    booking = in_memory_session.query(Booking).filter_by(source_email_id="EMAIL-008").first()
    assert booking.customer_raw == "DEF Automotive"
    assert booking.pol_raw == "BARCELONA"
    assert booking.pod_raw == "JEDDAH"
    assert booking.quantity == 5
    assert booking.split_index == 0


def test_source_email_status_after_create(email_008, fake_llm_008, in_memory_session):
    process_email(email_008, fake_llm_008, in_memory_session)
    src = in_memory_session.query(SourceEmail).filter_by(email_id="EMAIL-008").first()
    assert src.processing_status == "BOOKING_CREATED"


def test_process_email_is_idempotent(email_008, fake_llm_008, in_memory_session):
    """Running the same email twice must not create duplicate booking rows."""
    process_email(email_008, fake_llm_008, in_memory_session)
    process_email(email_008, fake_llm_008, in_memory_session)
    count = in_memory_session.query(Booking).filter_by(source_email_id="EMAIL-008").count()
    assert count == 1


def test_non_booking_email_no_booking_row(in_memory_session, fake_llm_008):
    """An email with NON_BOOKING intent must not produce a Booking row."""
    emails = load_emails(data_path=DATA_PATH)
    email_006 = next(e for e in emails if e.email_id == "EMAIL-006")
    fake = FakeLLMParser({
        "EMAIL-006": LLMResponse(
            source_email_id="EMAIL-006",
            message_intent="NON_BOOKING",
        )
    })
    process_email(email_006, fake, in_memory_session)
    count = in_memory_session.query(Booking).filter_by(source_email_id="EMAIL-006").count()
    assert count == 0


# ---------------------------------------------------------------------------
# S9 — Guard blocks CREATE when validation has blocking flags
# ---------------------------------------------------------------------------

class TestGuardBlocksOnBlockingFlags:
    """A booking with blocking flags is created with status=VALIDATION_REQUIRED
    and the flags are persisted in validation_flags JSON column."""

    def test_blocking_flags_produce_validation_required(self, in_memory_session):
        """UNKNOWN_CUSTOMER + MISSING_REQUIRED → status=VALIDATION_REQUIRED in DB."""
        emails = load_emails(data_path=DATA_PATH)
        email = next(e for e in emails if e.email_id == "EMAIL-010")
        fake = FakeLLMParser({
            "EMAIL-010": LLMResponse(
                source_email_id="EMAIL-010",
                message_intent="CREATE_BOOKING",
                bookings=[
                    ExtractedBooking(
                        customer="Unknown Corp XYZ",   # not in master data
                        pol="BARCELONA",
                        pod="JEDDAH",
                        quantity=1,
                        vin_list=["ANONVIN0000000090"],
                        requested_date="25/10/2026",
                    )
                ],
            )
        })
        process_email(email, fake, in_memory_session)
        booking = in_memory_session.query(Booking).filter_by(
            source_email_id="EMAIL-010"
        ).first()
        assert booking is not None
        assert booking.processing_status == "VALIDATION_REQUIRED"
        flag_codes = [f["code"] for f in booking.validation_flags]
        assert "UNKNOWN_CUSTOMER" in flag_codes

    def test_ready_booking_status_ready_in_db(self, in_memory_session):
        """A clean booking (EMAIL-008 fixture) resolves to status=READY in DB."""
        emails = load_emails(data_path=DATA_PATH)
        email = next(e for e in emails if e.email_id == "EMAIL-008")
        fake = FakeLLMParser({
            "EMAIL-008": LLMResponse(
                source_email_id="EMAIL-008",
                message_intent="CREATE_BOOKING",
                bookings=[
                    ExtractedBooking(
                        booking_ref="BOOKINGREF-D100",
                        customer="DEF Automotive",
                        pol="BARCELONA",
                        pod="JEDDAH",
                        quantity=5,
                        vin_list=[
                            "ANONVIN0000000019", "ANONVIN0000000020",
                            "ANONVIN0000000021", "ANONVIN0000000022",
                            "ANONVIN0000000023",
                        ],
                        weight_raw="2180 KG",
                        dims_raw="5.35 x 1.90 x 1.82 m",
                        requested_date="18/10/2026",
                    )
                ],
            )
        })
        process_email(email, fake, in_memory_session)
        booking = in_memory_session.query(Booking).filter_by(
            source_email_id="EMAIL-008"
        ).first()
        assert booking.processing_status == "READY"
        assert booking.validation_flags is not None  # at least an empty list


# ---------------------------------------------------------------------------
# S9 — Injection-safe: guard cannot be coerced to produce READY
# ---------------------------------------------------------------------------

class TestInjectionSafeGuard:
    """Even if LLM output claims a suspicious email is a clean booking,
    the validator's SUSPICIOUS_CONTENT flag blocks READY status."""

    def test_suspicious_email_cannot_produce_ready(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_031 = next(e for e in emails if e.email_id == "EMAIL-031")
        # LLM output looks clean — all fields provided
        fake = FakeLLMParser({
            "EMAIL-031": LLMResponse(
                source_email_id="EMAIL-031",
                message_intent="CREATE_BOOKING",
                bookings=[
                    ExtractedBooking(
                        customer="ABC Shipping",
                        pol="GENOA",
                        pod="JEDDAH",
                        quantity=2,
                        vin_list=["ANONVIN0000000091", "ANONVIN0000000092"],
                        requested_date="05/11/2026",
                    )
                ],
            )
        })
        process_email(email_031, fake, in_memory_session)
        booking = in_memory_session.query(Booking).filter_by(
            source_email_id="EMAIL-031"
        ).first()
        assert booking is not None
        assert booking.processing_status == "VALIDATION_REQUIRED"
        flag_codes = [f["code"] for f in booking.validation_flags]
        assert "SUSPICIOUS_CONTENT" in flag_codes


# ---------------------------------------------------------------------------
# S9 — D100 chain: 008 create → 012 duplicate → 013 cancel
# ---------------------------------------------------------------------------

class TestD100Chain:
    """The golden-set D100 booking chain:
      EMAIL-008  CREATE  → booking READY
      EMAIL-012  CREATE  → POSSIBLE_DUPLICATE (fingerprint/hash match)
      EMAIL-013  CANCEL  → booking CANCELLED + BookingEvent
    """

    def _make_008(self):
        return LLMResponse(
            source_email_id="EMAIL-008",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA",
                pod="JEDDAH",
                quantity=5,
                vin_list=[
                    "ANONVIN0000000019", "ANONVIN0000000020",
                    "ANONVIN0000000021", "ANONVIN0000000022",
                    "ANONVIN0000000023",
                ],
                weight_raw="2180 KG",
                dims_raw="5.35 x 1.90 x 1.82 m",
                requested_date="18/10/2026",
            )],
        )

    def _make_013_cancel(self):
        return LLMResponse(
            source_email_id="EMAIL-013",
            message_intent="CANCEL_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5, requested_date="18/10/2026",
            )],
        )

    def test_008_creates_ready_booking(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        process_email(email_008, FakeLLMParser({"EMAIL-008": self._make_008()}), in_memory_session)
        booking = in_memory_session.query(Booking).filter_by(
            source_email_id="EMAIL-008"
        ).first()
        assert booking is not None
        assert booking.processing_status == "READY"
        assert booking.booking_ref == "BOOKINGREF-D100"

    def test_013_cancels_d100(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        email_013 = next(e for e in emails if e.email_id == "EMAIL-013")

        process_email(email_008, FakeLLMParser({"EMAIL-008": self._make_008()}), in_memory_session)
        process_email(email_013, FakeLLMParser({"EMAIL-013": self._make_013_cancel()}), in_memory_session)

        booking = in_memory_session.query(Booking).filter_by(
            source_email_id="EMAIL-008"
        ).first()
        assert booking.processing_status == "CANCELLED"

    def test_013_creates_cancel_event(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        email_013 = next(e for e in emails if e.email_id == "EMAIL-013")

        process_email(email_008, FakeLLMParser({"EMAIL-008": self._make_008()}), in_memory_session)
        process_email(email_013, FakeLLMParser({"EMAIL-013": self._make_013_cancel()}), in_memory_session)

        events = (
            in_memory_session.query(BookingEvent)
            .filter_by(event_type="CANCELLED")
            .all()
        )
        assert len(events) == 1
        assert events[0].booking_ref == "BOOKINGREF-D100"

    def test_013_cancel_is_idempotent(self, in_memory_session):
        """Cancelling a CANCELLED booking again is a no-op (no second event)."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        email_013 = next(e for e in emails if e.email_id == "EMAIL-013")

        process_email(email_008, FakeLLMParser({"EMAIL-008": self._make_008()}), in_memory_session)
        process_email(email_013, FakeLLMParser({"EMAIL-013": self._make_013_cancel()}), in_memory_session)
        process_email(email_013, FakeLLMParser({"EMAIL-013": self._make_013_cancel()}), in_memory_session)

        events = (
            in_memory_session.query(BookingEvent)
            .filter_by(event_type="CANCELLED")
            .all()
        )
        # Second cancel returns "skipped" — no second CANCELLED event
        assert len(events) == 1


# ---------------------------------------------------------------------------
# S9 — UPDATE routing
# ---------------------------------------------------------------------------

class TestUpdateBooking:
    """UPDATE_BOOKING intent finds the booking by ref and applies a delta."""

    def test_update_changes_quantity(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")

        # First: create
        create_llm = LLMResponse(
            source_email_id="EMAIL-008",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-UP1",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=3, vin_list=[
                    "ANONVIN0000000030", "ANONVIN0000000031", "ANONVIN0000000032",
                ],
                requested_date="01/11/2026",
            )],
        )
        process_email(email_008, FakeLLMParser({"EMAIL-008": create_llm}), in_memory_session)

        # Then: update with new quantity via a different email object
        from datetime import datetime
        update_email = ParsedEmail(
            email_id="EMAIL-UPD",
            file_path="data/emails/EMAIL-UPD.txt",
            received_at=datetime(2026, 10, 15, 9, 0),
            sender="orders@defauto.example",
            flags=[],
            body_clean="Please update BOOKINGREF-UP1 to 5 units.",
        )
        update_llm = LLMResponse(
            source_email_id="EMAIL-UPD",
            message_intent="UPDATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-UP1",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5, requested_date="01/11/2026",
            )],
        )
        process_email(update_email, FakeLLMParser({"EMAIL-UPD": update_llm}), in_memory_session)

        booking = in_memory_session.query(Booking).filter_by(
            source_email_id="EMAIL-008"
        ).first()
        assert booking.quantity == 5

        # Update event should exist
        events = in_memory_session.query(BookingEvent).filter_by(
            event_type="UPDATED"
        ).all()
        assert len(events) == 1

    def test_update_orphan_ref_marks_orphan(self, in_memory_session):
        """UPDATE with a ref that doesn't exist → source_email status=ORPHAN."""
        from datetime import datetime
        orphan_email = ParsedEmail(
            email_id="EMAIL-ORPH",
            file_path="data/emails/EMAIL-ORPH.txt",
            received_at=datetime(2026, 10, 20, 9, 0),
            sender="orders@defauto.example",
            flags=[],
            body_clean="Update BOOKINGREF-GHOST to 3 units.",
        )
        orphan_llm = LLMResponse(
            source_email_id="EMAIL-ORPH",
            message_intent="UPDATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-GHOST",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=3, requested_date="01/11/2026",
            )],
        )
        process_email(orphan_email, FakeLLMParser({"EMAIL-ORPH": orphan_llm}), in_memory_session)
        src = in_memory_session.query(SourceEmail).filter_by(email_id="EMAIL-ORPH").first()
        assert src.processing_status == "ORPHAN"


# ---------------------------------------------------------------------------
# S9 — Every guard decision in processing_log
# ---------------------------------------------------------------------------

class TestGuardDecisionsInProcessingLog:
    """Every call to process_email must produce at least one guard-stage log entry."""

    def test_create_logs_guard_decision(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        fake = FakeLLMParser({
            "EMAIL-008": LLMResponse(
                source_email_id="EMAIL-008",
                message_intent="CREATE_BOOKING",
                bookings=[ExtractedBooking(
                    booking_ref="BOOKINGREF-D100",
                    customer="DEF Automotive",
                    pol="BARCELONA", pod="JEDDAH",
                    quantity=5,
                    vin_list=[
                        "ANONVIN0000000019", "ANONVIN0000000020",
                        "ANONVIN0000000021", "ANONVIN0000000022",
                        "ANONVIN0000000023",
                    ],
                    weight_raw="2180 KG",
                    dims_raw="5.35 x 1.90 x 1.82 m",
                    requested_date="18/10/2026",
                )],
            )
        })
        process_email(email_008, fake, in_memory_session)
        guard_logs = (
            in_memory_session.query(ProcessingLog)
            .filter_by(email_id="EMAIL-008", stage="guard")
            .all()
        )
        assert len(guard_logs) >= 1
        assert guard_logs[0].detail is not None
        assert "guard_decision" in guard_logs[0].detail

    def test_non_booking_logs_no_action(self, in_memory_session):
        emails = load_emails(data_path=DATA_PATH)
        email_006 = next(e for e in emails if e.email_id == "EMAIL-006")
        fake = FakeLLMParser({
            "EMAIL-006": LLMResponse(
                source_email_id="EMAIL-006",
                message_intent="NON_BOOKING",
            )
        })
        process_email(email_006, fake, in_memory_session)
        guard_logs = (
            in_memory_session.query(ProcessingLog)
            .filter_by(email_id="EMAIL-006", stage="guard")
            .all()
        )
        assert len(guard_logs) >= 1
        assert guard_logs[0].detail["guard_decision"] == "NO_ACTION"

    def test_validator_stage_logged(self, in_memory_session):
        """The validator stage must appear in processing_log for every email."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        fake = FakeLLMParser({
            "EMAIL-008": LLMResponse(
                source_email_id="EMAIL-008",
                message_intent="CREATE_BOOKING",
                bookings=[ExtractedBooking(
                    customer="DEF Automotive",
                    pol="BARCELONA", pod="JEDDAH",
                    quantity=1, vin_list=["ANONVIN0000000093"],
                    requested_date="18/10/2026",
                )],
            )
        })
        process_email(email_008, fake, in_memory_session)
        validator_logs = (
            in_memory_session.query(ProcessingLog)
            .filter_by(email_id="EMAIL-008", stage="validator")
            .all()
        )
        assert len(validator_logs) == 1


# ---------------------------------------------------------------------------
# Helpers shared by security tests
# ---------------------------------------------------------------------------

def _make_booking_ref_d999(session, email_008):
    """Create a READY booking with ref BOOKINGREF-D999 owned by DEF Automotive."""
    from ugr_booking.pipeline.agent import process_email
    llm = LLMResponse(
        source_email_id="EMAIL-008",
        message_intent="CREATE_BOOKING",
        bookings=[ExtractedBooking(
            booking_ref="BOOKINGREF-D999",
            customer="DEF Automotive",
            pol="BARCELONA", pod="JEDDAH",
            quantity=2,
            vin_list=["ANONVIN0000000080", "ANONVIN0000000081"],
            requested_date="01/12/2026",
        )],
    )
    process_email(email_008, FakeLLMParser({"EMAIL-008": llm}), session)


def _synthetic_email(email_id: str, sender: str | None, body: str = "Cancel D999.",
                     suspicious: bool = False, encoding_warning: bool = False):
    """Build a synthetic ParsedEmail for security tests."""
    from datetime import datetime
    return ParsedEmail(
        email_id=email_id,
        file_path=f"data/emails/{email_id}.txt",
        received_at=datetime(2026, 10, 21, 9, 0),
        sender=sender,
        flags=[],
        body_clean=body,
        suspicious_patterns=["injection"] if suspicious else [],
        encoding_warning=encoding_warning,
    )


def _cancel_llm(source_id: str, booking_ref: str = "BOOKINGREF-D999"):
    return LLMResponse(
        source_email_id=source_id,
        message_intent="CANCEL_BOOKING",
        bookings=[ExtractedBooking(
            booking_ref=booking_ref,
            customer="DEF Automotive",
            pol="BARCELONA", pod="JEDDAH",
            quantity=2, requested_date="01/12/2026",
        )],
    )


def _update_llm(source_id: str, booking_ref: str = "BOOKINGREF-D999", quantity: int = 3):
    return LLMResponse(
        source_email_id=source_id,
        message_intent="UPDATE_BOOKING",
        bookings=[ExtractedBooking(
            booking_ref=booking_ref,
            customer="DEF Automotive",
            pol="BARCELONA", pod="JEDDAH",
            quantity=quantity, requested_date="01/12/2026",
        )],
    )


# ---------------------------------------------------------------------------
# S9 security: suspicious/encoding CANCEL and UPDATE are rejected
# ---------------------------------------------------------------------------

class TestGuardSecurityChecks:
    """Security gates on _guard_cancel and _guard_update."""

    # --- CANCEL: SUSPICIOUS_CONTENT ---

    def test_suspicious_cancel_not_applied(self, in_memory_session):
        """CANCEL from a suspicious email must not change booking status."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        cancel_email = _synthetic_email("EMAIL-SUSP-C", "orders@defauto.example",
                                        suspicious=True)
        process_email(cancel_email,
                      FakeLLMParser({"EMAIL-SUSP-C": _cancel_llm("EMAIL-SUSP-C")}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.processing_status == "READY"   # unchanged

        events = in_memory_session.query(BookingEvent).filter_by(
            event_type="CANCELLED"
        ).all()
        assert len(events) == 0

        src = in_memory_session.query(SourceEmail).filter_by(
            email_id="EMAIL-SUSP-C"
        ).first()
        assert src.processing_status == "NEEDS_REVIEW"

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-SUSP-C", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_SUSPICIOUS" in decisions

    def test_encoding_warned_cancel_not_applied(self, in_memory_session):
        """CANCEL from an encoding-corrupted email must be rejected."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        cancel_email = _synthetic_email("EMAIL-ENC-C", "orders@defauto.example",
                                        encoding_warning=True)
        process_email(cancel_email,
                      FakeLLMParser({"EMAIL-ENC-C": _cancel_llm("EMAIL-ENC-C")}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.processing_status == "READY"

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-ENC-C", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_SUSPICIOUS" in decisions

    # --- CANCEL: SENDER_MISMATCH / unknown domain ---

    def test_unknown_sender_cancel_not_applied(self, in_memory_session):
        """CANCEL with no sender (None) → domain unknown → REJECTED_SENDER_MISMATCH."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        cancel_email = _synthetic_email("EMAIL-NOSND-C", sender=None)
        process_email(cancel_email,
                      FakeLLMParser({"EMAIL-NOSND-C": _cancel_llm("EMAIL-NOSND-C")}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.processing_status == "READY"

        src = in_memory_session.query(SourceEmail).filter_by(
            email_id="EMAIL-NOSND-C"
        ).first()
        assert src.processing_status == "NEEDS_REVIEW"

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-NOSND-C", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_SENDER_MISMATCH" in decisions

    def test_wrong_domain_cancel_not_applied(self, in_memory_session):
        """CANCEL from abc shipping domain on a DEF Automotive booking → mismatch."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        # abcshipping.example → CUST-001 (ABC Shipping) ≠ CUST-003 (DEF Automotive)
        cancel_email = _synthetic_email("EMAIL-MISM-C",
                                        sender="booking@abcshipping.example")
        process_email(cancel_email,
                      FakeLLMParser({"EMAIL-MISM-C": _cancel_llm("EMAIL-MISM-C")}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.processing_status == "READY"

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-MISM-C", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_SENDER_MISMATCH" in decisions

    # --- UPDATE: same security gates ---

    def test_suspicious_update_not_applied(self, in_memory_session):
        """UPDATE from suspicious email must not modify the booking."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        upd_email = _synthetic_email("EMAIL-SUSP-U", "orders@defauto.example",
                                     suspicious=True)
        process_email(upd_email,
                      FakeLLMParser({"EMAIL-SUSP-U": _update_llm("EMAIL-SUSP-U", quantity=99)}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.quantity == 2   # unchanged

        src = in_memory_session.query(SourceEmail).filter_by(
            email_id="EMAIL-SUSP-U"
        ).first()
        assert src.processing_status == "NEEDS_REVIEW"

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-SUSP-U", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_SUSPICIOUS" in decisions

    def test_wrong_domain_update_not_applied(self, in_memory_session):
        """UPDATE from mismatched domain must not modify the booking."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        upd_email = _synthetic_email("EMAIL-MISM-U",
                                     sender="booking@abcshipping.example")
        process_email(upd_email,
                      FakeLLMParser({"EMAIL-MISM-U": _update_llm("EMAIL-MISM-U", quantity=99)}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.quantity == 2   # unchanged

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-MISM-U", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_SENDER_MISMATCH" in decisions

    # --- UPDATE on CANCELLED booking ---

    def test_update_on_cancelled_booking_rejected(self, in_memory_session):
        """UPDATE targeting a CANCELLED booking → REJECTED_CANCELLED, no event."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        _make_booking_ref_d999(in_memory_session, email_008)

        # First: cancel the booking legitimately
        cancel_email = _synthetic_email("EMAIL-PRECANC",
                                        sender="orders@defauto.example")
        process_email(cancel_email,
                      FakeLLMParser({"EMAIL-PRECANC": _cancel_llm("EMAIL-PRECANC")}),
                      in_memory_session)

        # Then: try to update the now-cancelled booking
        upd_email = _synthetic_email("EMAIL-UPD-CANC",
                                     sender="orders@defauto.example",
                                     body="Update the quantity please.")
        process_email(upd_email,
                      FakeLLMParser({"EMAIL-UPD-CANC": _update_llm("EMAIL-UPD-CANC", quantity=10)}),
                      in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D999")
        assert booking.processing_status == "CANCELLED"   # still cancelled
        assert booking.quantity == 2                       # quantity unchanged

        src = in_memory_session.query(SourceEmail).filter_by(
            email_id="EMAIL-UPD-CANC"
        ).first()
        assert src.processing_status == "NEEDS_REVIEW"

        logs = in_memory_session.query(ProcessingLog).filter_by(
            email_id="EMAIL-UPD-CANC", stage="guard"
        ).all()
        decisions = [l.detail["guard_decision"] for l in logs if l.detail]
        assert "REJECTED_CANCELLED" in decisions

        # No UPDATED event should exist for this attempt
        updated_events = in_memory_session.query(BookingEvent).filter_by(
            event_type="UPDATED", source_email_id="EMAIL-UPD-CANC"
        ).all()
        assert len(updated_events) == 0

    # --- D100 chain: still completes with security checks active ---

    def test_d100_chain_passes_security(self, in_memory_session):
        """EMAIL-013 sender (defauto.example → CUST-003) matches DEF Automotive → passes."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        email_013 = next(e for e in emails if e.email_id == "EMAIL-013")

        create_llm = LLMResponse(
            source_email_id="EMAIL-008",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5,
                vin_list=[
                    "ANONVIN0000000019", "ANONVIN0000000020",
                    "ANONVIN0000000021", "ANONVIN0000000022",
                    "ANONVIN0000000023",
                ],
                weight_raw="2180 KG",
                dims_raw="5.35 x 1.90 x 1.82 m",
                requested_date="18/10/2026",
            )],
        )
        cancel_llm = LLMResponse(
            source_email_id="EMAIL-013",
            message_intent="CANCEL_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5, requested_date="18/10/2026",
            )],
        )
        process_email(email_008, FakeLLMParser({"EMAIL-008": create_llm}), in_memory_session)
        process_email(email_013, FakeLLMParser({"EMAIL-013": cancel_llm}), in_memory_session)

        from ugr_booking.tools.booking_ops import find_by_ref
        booking = find_by_ref(in_memory_session, "BOOKINGREF-D100")
        assert booking.processing_status == "CANCELLED"


# ---------------------------------------------------------------------------
# New tests for fixes
# ---------------------------------------------------------------------------

class TestSenderMismatchUnknownDomain:
    """SENDER_CUSTOMER_MISMATCH fires when sender domain is not in domain_map."""

    def test_unknown_domain_raises_sender_mismatch(self, in_memory_session):
        """Sender from an unrecognised domain + resolved customer → SENDER_CUSTOMER_MISMATCH."""
        from datetime import datetime
        from ugr_booking.pipeline.validator import validate
        from ugr_booking.pipeline.preprocessor import ParsedEmail
        from ugr_booking.models.schemas import LLMResponse, ExtractedBooking

        email = ParsedEmail(
            email_id="EMAIL-TEST-UNKNOWNDOMAIN",
            file_path="data/emails/fake.txt",
            received_at=datetime(2026, 11, 1, 10, 0),
            sender="user@unknowndomain.example",  # not in domain_map
            flags=[],
            body_clean="Booking request.",
        )
        llm_result = LLMResponse(
            source_email_id="EMAIL-TEST-UNKNOWNDOMAIN",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                customer="DEF Automotive",   # resolves to CUST-003
                pol="BARCELONA", pod="JEDDAH",
                quantity=1, vin_list=["ANONVIN0000000098"],
                requested_date="01/12/2026",
            )],
        )
        result = validate(llm_result, email)
        flag_codes = [f.code for f in result.bookings[0].flags]
        assert "SENDER_CUSTOMER_MISMATCH" in flag_codes


class TestQuantityFromVINCountBothPresent:
    """When explicit qty matches VIN count, no QUANTITY_FROM_VIN_COUNT flag is raised."""

    def test_qty_equals_vin_count_no_flag(self, in_memory_session):
        """qty==len(vin_list) → quantity_source='explicit', no QUANTITY_FROM_VIN_COUNT, status READY."""
        from datetime import datetime
        from ugr_booking.pipeline.validator import validate
        from ugr_booking.pipeline.preprocessor import ParsedEmail
        from ugr_booking.models.schemas import LLMResponse, ExtractedBooking

        email = ParsedEmail(
            email_id="EMAIL-TEST-QTYVIN",
            file_path="data/emails/fake.txt",
            received_at=datetime(2026, 11, 1, 10, 0),
            sender="orders@defauto.example",
            flags=[],
            body_clean="Booking with explicit qty matching VINs.",
        )
        llm_result = LLMResponse(
            source_email_id="EMAIL-TEST-QTYVIN",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=3,
                vin_list=["ANONVIN0000000061", "ANONVIN0000000062", "ANONVIN0000000063"],
                requested_date="01/12/2026",
            )],
        )
        result = validate(llm_result, email)
        bv = result.bookings[0]
        flag_codes = [f.code for f in bv.flags]
        assert "QUANTITY_FROM_VIN_COUNT" not in flag_codes
        assert bv.quantity_source == "explicit"
        assert bv.status == "READY"


class TestBusinessDuplicate:
    """find_business_duplicate catches re-sent emails with different greeting."""

    def _make_email_008_response(self):
        return LLMResponse(
            source_email_id="EMAIL-008",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5,
                vin_list=[
                    "ANONVIN0000000019", "ANONVIN0000000020",
                    "ANONVIN0000000021", "ANONVIN0000000022",
                    "ANONVIN0000000023",
                ],
                weight_raw="2180 KG",
                dims_raw="5.35 x 1.90 x 1.82 m",
                requested_date="18/10/2026",
            )],
        )

    def test_012_resend_is_duplicate_no_second_booking(self, in_memory_session):
        """EMAIL-012 (resend of EMAIL-008, different greeting) → DUPLICATE, 1 Booking row only."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        email_012 = next(e for e in emails if e.email_id == "EMAIL-012")

        # Same LLM output for 012 (same booking data)
        fake_012 = LLMResponse(
            source_email_id="EMAIL-012",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5,
                vin_list=[
                    "ANONVIN0000000019", "ANONVIN0000000020",
                    "ANONVIN0000000021", "ANONVIN0000000022",
                    "ANONVIN0000000023",
                ],
                weight_raw="2180 KG",
                dims_raw="5.35 x 1.90 x 1.82 m",
                requested_date="18/10/2026",
            )],
        )

        process_email(email_008, FakeLLMParser({"EMAIL-008": self._make_email_008_response()}),
                      in_memory_session)
        process_email(email_012, FakeLLMParser({"EMAIL-012": fake_012}), in_memory_session)

        # Only one Booking row should exist
        booking_count = in_memory_session.query(Booking).count()
        assert booking_count == 1

        # EMAIL-012 source email should be marked DUPLICATE
        src_012 = in_memory_session.query(SourceEmail).filter_by(email_id="EMAIL-012").first()
        assert src_012.processing_status == "DUPLICATE"

    def test_cancelled_then_rebook_same_vins_not_duplicate(self, in_memory_session):
        """After CANCEL, a new booking with same VINs is NOT treated as duplicate."""
        emails = load_emails(data_path=DATA_PATH)
        email_008 = next(e for e in emails if e.email_id == "EMAIL-008")
        email_013 = next(e for e in emails if e.email_id == "EMAIL-013")

        # Create then cancel
        process_email(email_008, FakeLLMParser({"EMAIL-008": self._make_email_008_response()}),
                      in_memory_session)
        cancel_llm = LLMResponse(
            source_email_id="EMAIL-013",
            message_intent="CANCEL_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D100",
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5, requested_date="18/10/2026",
            )],
        )
        process_email(email_013, FakeLLMParser({"EMAIL-013": cancel_llm}), in_memory_session)

        # Now re-book with same VINs but a new email + new ref
        from datetime import datetime
        rebook_email = ParsedEmail(
            email_id="EMAIL-REBOOK",
            file_path="data/emails/fake.txt",
            received_at=datetime(2026, 11, 5, 10, 0),
            sender="export@defauto.example",
            flags=[],
            body_clean="Re-booking after cancellation.",
        )
        rebook_llm = LLMResponse(
            source_email_id="EMAIL-REBOOK",
            message_intent="CREATE_BOOKING",
            bookings=[ExtractedBooking(
                booking_ref="BOOKINGREF-D101",  # new ref
                customer="DEF Automotive",
                pol="BARCELONA", pod="JEDDAH",
                quantity=5,
                vin_list=[
                    "ANONVIN0000000019", "ANONVIN0000000020",
                    "ANONVIN0000000021", "ANONVIN0000000022",
                    "ANONVIN0000000023",
                ],
                requested_date="15/11/2026",
            )],
        )
        process_email(rebook_email, FakeLLMParser({"EMAIL-REBOOK": rebook_llm}), in_memory_session)

        # Two Booking rows: original (CANCELLED) + new rebook
        booking_count = in_memory_session.query(Booking).count()
        assert booking_count == 2

        src_rebook = in_memory_session.query(SourceEmail).filter_by(email_id="EMAIL-REBOOK").first()
        assert src_rebook.processing_status == "BOOKING_CREATED"

