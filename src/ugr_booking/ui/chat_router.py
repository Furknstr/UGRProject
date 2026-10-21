"""Chat router — natural-language query routing for the operations assistant.

Flow:
1. User input query -> LLM Router (Ollama structured JSON) with Rule-Based Fallback.
2. Port / customer queries resolved via master_data + fold (same pipeline logic).
3. Database query executed through repository.py (single source of truth).
4. Answers are produced deterministically via templates (LLM does not invent numbers).
5. Unknown / non-existent records return: 'Bu bilgi mevcut kayıtlarda bulunmuyor.'
6. Provides filter updates to synchronize with the Streamlit dashboard.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ugr_booking.config import OLLAMA_HOST, OLLAMA_MODEL
from ugr_booking.db.repository import (
    count_by_customer,
    filter_bookings,
    get_by_ref,
    get_customer_display_name,
    get_latest_n,
    get_port_display_name,
    get_validation_required,
    list_known_customers,
    list_known_ports,
    summary_stats,
)
from ugr_booking.master_data import resolve_customer, resolve_port
from ugr_booking.normalize import fold

logger = logging.getLogger(__name__)

FIXED_NOT_FOUND_ANSWER = "Bu bilgi mevcut kayıtlarda bulunmuyor."


@dataclass
class ChatResponse:
    answer: str
    intent: str
    data: list[dict] | dict | None = None
    filter_updates: dict[str, Any] = field(default_factory=dict)
    used_fallback: bool = False
    resolved_entity: str | None = None


# ---------------------------------------------------------------------------
# Template answer generators (deterministic, no hallucinated numbers)
# ---------------------------------------------------------------------------

def _format_booking_item(b) -> dict:
    return {
        "id": b.id,
        "booking_ref": b.booking_ref or f"BKG-{b.id:04d}",
        "customer": get_customer_display_name(b.customer_id),
        "pol": b.pol or "Unresolved",
        "pod": b.pod or "Unresolved",
        "status": b.processing_status,
        "quantity": b.quantity,
        "requested_date": b.requested_date or "Belirtilmemiş",
        "source_email": b.source_email_id,
    }


def _render_customer_count(customer_input: str) -> ChatResponse:
    count, cid, cname = count_by_customer(customer_input)
    if count == 0 and not cid:
        return ChatResponse(
            answer=FIXED_NOT_FOUND_ANSWER,
            intent="COUNT_BY_CUSTOMER",
            resolved_entity=customer_input,
        )

    name_str = f"{cname} ({cid})" if cid else customer_input
    answer = (
        f"**{name_str}** için sistemde kayıtlı toplam **{count}** rezervasyon bulunmaktadır."
    )
    bookings = filter_bookings(customer_id=cid)
    data = [_format_booking_item(b) for b in bookings]
    return ChatResponse(
        answer=answer,
        intent="COUNT_BY_CUSTOMER",
        data=data,
        filter_updates={"customer_id": cid or customer_input},
        resolved_entity=cid,
    )


def _render_port_filter(port_input: str, direction: str = "POL") -> ChatResponse:
    resolved = resolve_port(port_input)
    if not resolved:
        # Check if any port code matches
        port_upper = port_input.strip().upper()
        if any(p["port_code"] == port_upper for p in list_known_ports()):
            resolved = port_upper

    if not resolved:
        return ChatResponse(
            answer=FIXED_NOT_FOUND_ANSWER,
            intent="PORT_FILTER",
            resolved_entity=port_input,
        )

    port_display = get_port_display_name(resolved)
    if direction.upper() == "POD":
        bookings = filter_bookings(pod=resolved)
        label = "Varış (POD)"
        filter_key = "pod"
    else:
        bookings = filter_bookings(pol=resolved)
        label = "Yükleme (POL)"
        filter_key = "pol"

    count = len(bookings)
    if count == 0:
        answer = f"**{port_display}** {label} limanı için kayıtlı rezervasyon bulunmuyor."
    else:
        answer = (
            f"**{port_display}** {label} limanı ile ilişkili toplam **{count}** "
            f"rezervasyon listelenmiştir."
        )

    data = [_format_booking_item(b) for b in bookings]
    return ChatResponse(
        answer=answer,
        intent="PORT_FILTER",
        data=data,
        filter_updates={filter_key: resolved},
        resolved_entity=resolved,
    )


def _render_validation_required() -> ChatResponse:
    bookings = get_validation_required()
    count = len(bookings)
    answer = (
        f"Operatör doğrulaması bekleyen (**VALIDATION_REQUIRED**) toplam **{count}** "
        f"rezervasyon bulunmaktadır.\n\n"
        f"Yaygın nedenler: eksik zorunlu alanlar, belirsiz tarih formatları, liman/müşteri "
        f"eşleşme gereksinimleri veya VIN uyuşmazlıklarıdır."
    )
    data = [_format_booking_item(b) for b in bookings]
    return ChatResponse(
        answer=answer,
        intent="VALIDATION_REQUIRED",
        data=data,
        filter_updates={"status": "VALIDATION_REQUIRED"},
    )


def _render_latest_n(n: int = 5) -> ChatResponse:
    n = max(1, min(n, 50))
    bookings = get_latest_n(n)
    count = len(bookings)
    answer = f"Sistemdeki en son **{count}** rezervasyon listelenmiştir:"
    data = [_format_booking_item(b) for b in bookings]
    return ChatResponse(
        answer=answer,
        intent="LATEST_N",
        data=data,
    )


def _render_summary() -> ChatResponse:
    stats = summary_stats()
    answer = (
        f"### 📊 Sistem Operasyonel Özeti\n"
        f"- **Toplam Rezervasyon:** {stats['total_bookings']}\n"
        f"- **READY (Hazır):** {stats['ready_count']}\n"
        f"- **VALIDATION_REQUIRED (İnceleme Gerekli):** {stats['validation_required_count']}\n"
        f"- **CANCELLED (İptal):** {stats['cancelled_count']}\n"
        f"- **İnceleme Kuyruğundaki E-postalar:** {stats['review_queue_total']} "
        f"(Yetim: {stats['orphan_count']}, Kopya: {stats['duplicate_count']}, Şüpheli: {stats['suspicious_count']})"
    )
    return ChatResponse(
        answer=answer,
        intent="SUMMARY",
        data=stats,
    )


def _render_ref_search(ref: str) -> ChatResponse:
    bookings = get_by_ref(ref)
    if not bookings:
        return ChatResponse(
            answer=FIXED_NOT_FOUND_ANSWER,
            intent="SEARCH_REF",
            resolved_entity=ref,
        )
    answer = f"**{ref}** referansı ile eşleşen **{len(bookings)}** kayıt bulundu."
    data = [_format_booking_item(b) for b in bookings]
    return ChatResponse(
        answer=answer,
        intent="SEARCH_REF",
        data=data,
        resolved_entity=ref,
    )


# ---------------------------------------------------------------------------
# Rule-based fallback router (Regex & keyword matching)
# ---------------------------------------------------------------------------

def route_query_rules(query: str) -> ChatResponse:
    """Deterministic rule-based router.

    Handles the case's 4 core queries, summary, and edge cases.
    Guaranteed zero hallucination, 100% testable without LLM.
    """
    q = query.strip()
    q_fold = fold(q)

    # 1. Validation required queries
    if any(k in q_fold for k in ["dogrulama", "validation", "bekleyen", "review", "operator"]):
        return _render_validation_required()

    # 2. Latest N bookings
    m_latest = re.search(r"(?:son|latest)\s*(\d+)?\s*(?:rezervasyon|booking|kayit)?", q_fold)
    if m_latest and ("son" in q_fold or "latest" in q_fold):
        n_str = m_latest.group(1)
        n = int(n_str) if n_str else 5
        return _render_latest_n(n)

    # 3. Summary / KPI queries
    if any(k in q_fold for k in ["ozet", "summary", "genel durum", "kpi", "toplam kac", "istatistik"]):
        return _render_summary()

    # 4. Booking ref search
    m_ref = (
        re.search(r"\b(?:bookingref|referans|bkg|ref)[\s:-]+([a-zA-Z0-9-]+)", q_fold)
        or re.search(r"\b([a-zA-Z0-9-]+)\s+(?:referansl[ıi]|referans)", q_fold)
    )
    if m_ref and len(m_ref.group(1)) >= 3:
        ref_val = m_ref.group(1)
        return _render_ref_search(ref_val)

    # 5. Port queries (POL / POD)
    # Check if a known port name or code is mentioned
    for p in list_known_ports():
        p_code = p["port_code"]
        p_name = p["name"]
        aliases = p.get("aliases", [])

        # Match folded tokens
        candidates = [p_code, p_name] + aliases
        matched_candidate = None
        for cand in candidates:
            cand_folded = fold(cand)
            if cand_folded and cand_folded in q_fold:
                matched_candidate = cand
                break

        if matched_candidate:
            is_pod = any(w in q_fold for w in ["varis", "pod", "hedef", "destination", "to "])
            direction = "POD" if is_pod else "POL"
            return _render_port_filter(matched_candidate, direction=direction)

    # 6. Customer queries
    # Check if a known customer name or alias is mentioned
    for c in list_known_customers():
        c_id = c["customer_id"]
        c_name = c["name"]
        aliases = c.get("aliases", [])

        candidates = [c_id, c_name] + aliases
        for cand in candidates:
            cand_folded = fold(cand)
            if cand_folded and cand_folded in q_fold:
                return _render_customer_count(cand)

    # 7. Customer count pattern where customer name is generic/unknown
    # e.g. "X için kaç rezervasyon var?", "How many bookings for X?"
    m_cust_count = (
        re.search(r"^(.*?)\s*(?:icin|for)\s*(?:kac|how many|rezervasyon)", q, re.IGNORECASE)
        or re.search(r"(?:kac|how many|sayisi)\s*(?:olan)?\s*(.*?)\s*(?:rezervasyon|booking)?$", q, re.IGNORECASE)
        or re.search(r"^(.*?)\s*(?:rezervasyon|booking)\s*(?:sayisi|count)", q, re.IGNORECASE)
    )
    if m_cust_count:
        cand = m_cust_count.group(1).strip()
        # Clean filler words
        cand = re.sub(r"^(?:bana|lutfen|please|show|goster)\s*", "", cand, flags=re.IGNORECASE).strip()
        if len(cand) >= 2:
            return _render_customer_count(cand)

    # Default: Unknown / Not in records
    return ChatResponse(
        answer=FIXED_NOT_FOUND_ANSWER,
        intent="UNKNOWN",
        used_fallback=True,
    )


# ---------------------------------------------------------------------------
# LLM Router with automatic fallback
# ---------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """You are an intent parser for a maritime booking database.
Translate the user query into a single JSON function call.

Available functions:
1. count_by_customer(customer: str) - count bookings for a customer name/alias/id
2. filter_by_port(port: str, direction: "POL" | "POD") - filter by port
3. get_validation_required() - bookings requiring validation
4. get_latest_n(n: int) - get latest n bookings
5. get_summary() - overall operational metrics
6. get_by_ref(booking_ref: str) - search by booking reference
7. unknown() - unrecognized query or non-operational question

Return ONLY JSON in this format:
{"function": "<function_name>", "parameters": { ... }}"""


def route_query_llm(query: str) -> ChatResponse:
    """Attempt routing using Ollama structured output, fallback to rules on failure."""
    try:
        import httpx

        url = f"{OLLAMA_HOST.rstrip('/')}/api/generate"
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": f"{LLM_SYSTEM_PROMPT}\n\nUser query: \"{query}\"\nJSON:",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "num_predict": 100},
        }

        with httpx.Client(timeout=3.5) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                body = resp.json()
                raw_text = body.get("response", "{}")
                parsed = json.loads(raw_text)

                fn_name = parsed.get("function")
                params = parsed.get("parameters", {})

                if fn_name == "count_by_customer":
                    cust = params.get("customer", "")
                    if cust:
                        return _render_customer_count(cust)

                elif fn_name == "filter_by_port":
                    port = params.get("port", "")
                    direction = params.get("direction", "POL")
                    if port:
                        return _render_port_filter(port, direction)

                elif fn_name == "get_validation_required":
                    return _render_validation_required()

                elif fn_name == "get_latest_n":
                    n = int(params.get("n", 5))
                    return _render_latest_n(n)

                elif fn_name == "get_summary":
                    return _render_summary()

                elif fn_name == "get_by_ref":
                    ref = params.get("booking_ref", "")
                    if ref:
                        return _render_ref_search(ref)

                elif fn_name == "unknown":
                    return ChatResponse(
                        answer=FIXED_NOT_FOUND_ANSWER,
                        intent="UNKNOWN",
                    )
    except Exception as exc:
        logger.debug("LLM router unavailable or timed out: %s. Using rule-based fallback.", exc)

    # Fallback to deterministic rules
    resp = route_query_rules(query)
    resp.used_fallback = True
    return resp


def answer_query(query: str, prefer_llm: bool = True) -> ChatResponse:
    """Main entrypoint for chatbot queries."""
    if prefer_llm:
        return route_query_llm(query)
    return route_query_rules(query)
