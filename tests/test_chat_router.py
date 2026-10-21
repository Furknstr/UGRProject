"""Tests for chatbot query router (S13 & S15)."""

import pytest
from ugr_booking.db.repository import filter_bookings, summary_stats
from ugr_booking.ui.chat_router import (
    FIXED_NOT_FOUND_ANSWER,
    answer_query,
    route_query_rules,
)


def test_question_customer_count():
    """Case Question 1: Customer booking count."""
    resp = route_query_rules("ABC Shipping için kaç rezervasyon var?")
    assert resp.intent == "COUNT_BY_CUSTOMER"
    assert "7" in resp.answer
    assert resp.filter_updates.get("customer_id") == "CUST-001"

    # Chatbot count == dashboard count
    dash_count = summary_stats()["by_customer"].get("CUST-001")
    assert len(resp.data) == dash_count == 7


def test_question_port_filter():
    """Case Question 2: Port filter."""
    resp1 = route_query_rules("Izmir çıkışlı rezervasyonları göster")
    resp2 = route_query_rules("İZMİR kalkışlı rezervasyonlar")

    assert resp1.intent == "PORT_FILTER"
    assert resp2.intent == "PORT_FILTER"
    assert resp1.resolved_entity == "TRIZM"
    assert resp2.resolved_entity == "TRIZM"
    assert len(resp1.data) == len(resp2.data)

    # Chatbot count == dashboard count
    db_bookings = filter_bookings(pol="TRIZM")
    assert len(resp1.data) == len(db_bookings)


def test_question_validation_required():
    """Case Question 3: Bookings requiring validation."""
    resp = route_query_rules("Hangi rezervasyonlar doğrulama bekliyor?")
    assert resp.intent == "VALIDATION_REQUIRED"
    assert resp.filter_updates.get("status") == "VALIDATION_REQUIRED"

    # Chatbot count == dashboard count
    dash_val_count = summary_stats()["validation_required_count"]
    assert len(resp.data) == dash_val_count == 16
    assert "16" in resp.answer


def test_question_latest_n():
    """Case Question 4: Latest N bookings."""
    resp = route_query_rules("Son 5 rezervasyonu listele")
    assert resp.intent == "LATEST_N"
    assert len(resp.data) == 5

    resp3 = route_query_rules("Son 3 rezervasyon")
    assert len(resp3.data) == 3


def test_unknown_customer_fixed_answer():
    """Requirement: Unknown customer / missing record gets fixed answer."""
    resp = route_query_rules("Bilinmeyen Global Ltd için kaç rezervasyon var?")
    assert resp.answer == FIXED_NOT_FOUND_ANSWER

    resp2 = route_query_rules("NonExistentCorp bookings")
    assert resp2.answer == FIXED_NOT_FOUND_ANSWER


def test_summary_query():
    """Summary / overview queries."""
    resp = route_query_rules("Sistem genel durumu nedir?")
    assert resp.intent == "SUMMARY"
    stats = summary_stats()
    assert str(stats["total_bookings"]) in resp.answer
    assert str(stats["ready_count"]) in resp.answer


def test_ref_search_query():
    """Search by reference."""
    resp = route_query_rules("D100 referanslı rezervasyon")
    assert resp.intent == "SEARCH_REF"
    assert len(resp.data) > 0


def test_answer_query_fallback():
    """answer_query works safely and falls back cleanly if LLM is not responding."""
    resp = answer_query("Son 3 kayıt", prefer_llm=False)
    assert resp.intent == "LATEST_N"
    assert len(resp.data) == 3
