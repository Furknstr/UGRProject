"""S5 tests — vertical slice: preprocessor → LLM → DB."""

import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ugr_booking.models.orm import Base, Booking, SourceEmail
from ugr_booking.models.schemas import ExtractedBooking, LLMResponse
from ugr_booking.pipeline.agent import process_email
from ugr_booking.pipeline.llm_parser import FakeLLMParser
from ugr_booking.pipeline.preprocessor import load_emails

DATA_PATH = Path(__file__).parent.parent / "data"


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
