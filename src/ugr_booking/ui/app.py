"""UGR Lines — AI Booking Intake & Operations Dashboard (Streamlit).

Features:
- Real-time KPIs from common repository query layer (db/repository.py)
- Multi-dimensional sidebar filters (Customer, POL, POD, Status)
- Detailed Bookings Explorer with validation flags & email traceability
- Operational Review Queue for orphans, duplicates, security rejections, etc.
- Operational Analysis Charts
- Natural Language Chatbot with deterministic template answers & filter sync
"""

from __future__ import annotations

import json
from datetime import datetime
import pandas as pd
import plotly.express as px
import streamlit as st

from ugr_booking.db.repository import (
    filter_bookings,
    get_customer_display_name,
    get_port_display_name,
    list_known_customers,
    list_known_ports,
    review_queue,
    summary_stats,
)
from ugr_booking.ui.chat_router import answer_query


def apply_chat_filter(updates: dict):
    """Callback to apply chat filter updates safely before widget rendering."""
    if "customer_id" in updates:
        st.session_state["filter_customer"] = updates["customer_id"]
    if "pol" in updates:
        st.session_state["filter_pol"] = updates["pol"]
    if "pod" in updates:
        st.session_state["filter_pod"] = updates["pod"]
    if "status" in updates:
        st.session_state["filter_status"] = updates["status"]


def reset_filters():
    """Reset all sidebar filters to ALL."""
    st.session_state["filter_customer"] = "ALL"
    st.session_state["filter_pol"] = "ALL"
    st.session_state["filter_pod"] = "ALL"
    st.session_state["filter_status"] = "ALL"


def main():
    # ---------------------------------------------------------------------------
    # Page configuration & CSS styling
    # ---------------------------------------------------------------------------
    try:
        st.set_page_config(
            page_title="UGR Lines — AI Booking Intake",
            page_icon="🚢",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    except Exception:
        # Ignore if already set in current runtime
        pass

    # Custom sleek styling
    st.markdown(
        """
        <style>
        .metric-card {
            background-color: #f8f9fa;
            border-radius: 8px;
            padding: 16px;
            border-left: 5px solid #0d6efd;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .badge-ready {
            background-color: #d1e7dd;
            color: #0f5132;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.85rem;
        }
        .badge-val {
            background-color: #fff3cd;
            color: #664d03;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.85rem;
        }
        .badge-cancel {
            background-color: #f8d7da;
            color: #842029;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.85rem;
        }
        .stTabs [data-baseweb="tab-list"] {
            gap: 12px;
        }
        .stTabs [data-baseweb="tab"] {
            padding: 8px 16px;
            font-weight: 500;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ---------------------------------------------------------------------------
    # Session state initialization for filters & chat
    # ---------------------------------------------------------------------------

    if "filter_customer" not in st.session_state:
        st.session_state["filter_customer"] = "ALL"
    if "filter_pol" not in st.session_state:
        st.session_state["filter_pol"] = "ALL"
    if "filter_pod" not in st.session_state:
        st.session_state["filter_pod"] = "ALL"
    if "filter_status" not in st.session_state:
        st.session_state["filter_status"] = "ALL"

    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = [
            {
                "role": "assistant",
                "content": (
                    "Hello! I am the UGR Booking Operations Assistant. "
                    "You can ask me questions about bookings, customers, ports, or records requiring validation."
                ),
            }
        ]

    # ---------------------------------------------------------------------------
    # Header
    # ---------------------------------------------------------------------------

    header_col1, header_col2 = st.columns([4, 1])
    with header_col1:
        st.title("🚢 UGR Lines — AI Booking Intake Assistant")
        st.caption("Controlled AI Pipeline, Secure Validation & Operations Dashboard")
    with header_col2:
        st.write("")
        if st.button("🔄 Refresh Data", width="stretch"):
            st.rerun()

    # ---------------------------------------------------------------------------
    # Sidebar: Filters
    # ---------------------------------------------------------------------------

    with st.sidebar:
        st.header("🔍 Operational Filters")

        # Customer options
        customers = list_known_customers()
        cust_options = ["ALL"] + [c["customer_id"] for c in customers]
        cust_format = lambda x: "All Customers" if x == "ALL" else get_customer_display_name(x)

        st.selectbox(
            "Customer",
            options=cust_options,
            format_func=cust_format,
            key="filter_customer",
        )

        # Status options
        status_options = ["ALL", "READY", "VALIDATION_REQUIRED", "CANCELLED"]
        status_format = {
            "ALL": "All Statuses",
            "READY": "✅ READY (Approved)",
            "VALIDATION_REQUIRED": "⚠️ VALIDATION_REQUIRED (Pending)",
            "CANCELLED": "❌ CANCELLED",
        }
        st.selectbox(
            "Processing Status",
            options=status_options,
            format_func=lambda x: status_format.get(x, x),
            key="filter_status",
        )

        # Ports
        ports = list_known_ports()
        port_codes = ["ALL"] + sorted([p["port_code"] for p in ports])
        port_format = lambda x: "All Ports" if x == "ALL" else get_port_display_name(x)

        st.selectbox(
            "Port of Loading (POL)",
            options=port_codes,
            format_func=port_format,
            key="filter_pol",
        )

        st.selectbox(
            "Port of Discharge (POD)",
            options=port_codes,
            format_func=port_format,
            key="filter_pod",
        )

        st.write("")
        if st.button("Reset Filters", on_click=reset_filters, width="stretch"):
            st.rerun()

    # ---------------------------------------------------------------------------
    # Global Metrics (KPI Cards)
    # ---------------------------------------------------------------------------

    stats = summary_stats()

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    with kpi1:
        st.metric("Total Bookings", stats["total_bookings"], help="Legitimate booking records in database")
    with kpi2:
        st.metric("READY", stats["ready_count"], delta=f"{stats['ready_count']} ready", delta_color="normal")
    with kpi3:
        st.metric("VALIDATION REQUIRED", stats["validation_required_count"], delta=f"{stats['validation_required_count']} pending", delta_color="inverse")
    with kpi4:
        st.metric("CANCELLED", stats["cancelled_count"], delta=f"{stats['cancelled_count']} cancelled", delta_color="off")
    with kpi5:
        st.metric("Review Queue", stats["review_queue_total"], help="Orphan, duplicate or suspicious emails", delta=f"{stats['orphan_count']} orphans", delta_color="inverse")

    st.write("")

    # ---------------------------------------------------------------------------
    # Main Tabs: Bookings, Review Queue, Charts, Chatbot
    # ---------------------------------------------------------------------------

    tab_bookings, tab_review, tab_charts, tab_chat = st.tabs(
        ["📋 Bookings Explorer", "🚨 Review Queue", "📊 Operational Analytics", "💬 Operations Assistant"]
    )

    # ---------------------------------------------------------------------------
    # Tab 1: Bookings List & Inspection
    # ---------------------------------------------------------------------------

    with tab_bookings:
        # Query filtered bookings from repository
        filtered_list = filter_bookings(
            customer_id=st.session_state["filter_customer"],
            pol=st.session_state["filter_pol"],
            pod=st.session_state["filter_pod"],
            status=st.session_state["filter_status"],
        )

        st.subheader(f"Bookings ({len(filtered_list)} records displayed)")

        if not filtered_list:
            st.info("No booking records match the selected filters.")
        else:
            # Build DataFrame for table view
            table_rows = []
            for b in filtered_list:
                table_rows.append({
                    "ID": b.id,
                    "Ref": b.booking_ref or f"BKG-{b.id:04d}",
                    "Customer": get_customer_display_name(b.customer_id),
                    "POL": b.pol or "⚠️ Missing",
                    "POD": b.pod or "⚠️ Missing",
                    "Quantity": b.quantity or 0,
                    "Requested Date": b.requested_date or "⚠️ Unspecified",
                    "Status": b.processing_status,
                    "Source Email": b.source_email_id,
                    "Flags": len(b.validation_flags or []),
                })
            df_bookings = pd.DataFrame(table_rows)

            # Interactive Table
            st.dataframe(
                df_bookings,
                width="stretch",
                hide_index=True,
                column_config={
                    "Status": st.column_config.TextColumn(
                        "Status",
                        help="Processing status",
                    ),
                },
            )

            st.divider()

            # Detailed Booking Inspector with safe formatting (no key to prevent state conflict)
            st.subheader("🔍 Booking Details Inspector")

            booking_map = {b.id: b for b in filtered_list}
            options_ids = list(booking_map.keys())

            def _format_booking_label(bid: int) -> str:
               b = booking_map.get(bid)
               if not b:
                   return f"ID: {bid}"
               ref = b.booking_ref or f"BKG-{b.id:04d}"
               cust = get_customer_display_name(b.customer_id)
               return f"ID: {bid} | Ref: {ref} | Customer: {cust}"

            selected_id = st.selectbox(
                "Select a booking to inspect:",
                options=options_ids,
                format_func=_format_booking_label,
            )

            curr_b = booking_map.get(selected_id)
            if curr_b:
                dcol1, dcol2, dcol3 = st.columns([1, 1, 1])
                with dcol1:
                    st.markdown("**Core Information**")
                    st.write(f"- **Booking Ref:** `{curr_b.booking_ref}`")
                    st.write(f"- **Customer (Resolved):** {get_customer_display_name(curr_b.customer_id)}")
                    st.write(f"- **Customer (Raw Text):** `{curr_b.customer_raw}`")
                    st.write(f"- **Cargo Description:** {curr_b.cargo_description or '-'}")
                    st.write(f"- **Quantity:** {curr_b.quantity} (Source: {curr_b.quantity_source or 'explicit'})")
                with dcol2:
                    st.markdown("**Logistics & Dimensions**")
                    st.write(f"- **POL:** {get_port_display_name(curr_b.pol)} (Raw: `{curr_b.pol_raw}`)")
                    st.write(f"- **POD:** {get_port_display_name(curr_b.pod)} (Raw: `{curr_b.pod_raw}`)")
                    st.write(f"- **Requested Date:** `{curr_b.requested_date}` (Raw: `{curr_b.requested_date_raw}`)")
                    st.write(f"- **Unit Weight:** {curr_b.weight_kg_per_unit} kg (Raw: `{curr_b.weight_raw}`)")
                    st.write(f"- **Dimensions (L/W/H):** {curr_b.length_m} x {curr_b.width_m} x {curr_b.height_m} m")
                with dcol3:
                    st.markdown("**Status & Provenance**")
                    st.write(f"- **Status:** `{curr_b.processing_status}`")
                    st.write(f"- **Vessel / Voyage:** {curr_b.vessel or '-'} / {curr_b.voyage or '-'}")
                    st.write(f"- **Contact:** {curr_b.contact_name or '-'} ({curr_b.contact_email or '-'})")
                    st.write(f"- **Source Email ID:** `{curr_b.source_email_id}`")
                    st.write(f"- **Created At:** {curr_b.created_at}")

                # Validation Flags list
                st.markdown("##### ⚠️ Validation Flags")
                flags = curr_b.validation_flags or []
                if not flags:
                    st.success("All validation checks passed! No blocking or warning flags present (READY).")
                else:
                    flag_rows = []
                    for fl in flags:
                        sev = str(fl.get("severity", "")).upper()
                        flag_rows.append({
                            "Severity": "🚫 BLOCKING" if sev == "BLOCKING" else "⚠️ WARNING",
                            "Field": fl.get("field"),
                            "Rule Code": fl.get("code"),
                            "Raw Value": str(fl.get("raw")),
                            "Suggestion / Detail": fl.get("suggestion") or "-",
                        })
                    st.dataframe(pd.DataFrame(flag_rows), width="stretch", hide_index=True)

                # Traceability section: Source Email View
                with st.expander("📬 Original Source Email & Traceability"):
                    if curr_b.source_email:
                        src = curr_b.source_email
                        st.write(f"**Subject:** {src.subject}")
                        st.write(f"**Sender:** {src.sender} | **Received At:** {src.received_at}")
                        st.write(f"**Intent:** `{src.classified_intent}` | **Status:** `{src.processing_status}`")
                        st.text_area("Cleaned Email Body", src.body_clean or src.body_raw or "", height=150)
                    else:
                        st.write("Source email record not found.")

    # ---------------------------------------------------------------------------
    # Tab 2: Review Queue (İnceleme Kuyruğu)
    # ---------------------------------------------------------------------------

    with tab_review:
        st.subheader("🚨 Operations Review Queue")
        st.caption("Emails that could not be automatically converted into a booking, requiring human review or action.")

        rev_category = st.radio(
            "Category Filter",
            options=["All", "Orphans (ORPHAN)", "Duplicates (DUPLICATE)", "Security Rejections (NEEDS_REVIEW)", "Suspicious Content", "Attachment References", "Empty Body"],
            horizontal=True,
        )

        cat_map = {
            "All": None,
            "Orphans (ORPHAN)": "orphan",
            "Duplicates (DUPLICATE)": "duplicate",
            "Security Rejections (NEEDS_REVIEW)": "needs_review",
            "Suspicious Content": "suspicious",
            "Attachment References": "attachment",
            "Empty Body": "empty_body",
        }

        queue_emails = review_queue(category=cat_map.get(rev_category))
        st.write(f"**Records in Queue:** {len(queue_emails)}")

        if not queue_emails:
            st.success("No emails pending review in the selected category.")
        else:
            q_data = []
            for e in queue_emails:
                q_data.append({
                    "Email ID": e.email_id,
                    "Sender": e.sender or "⚠️ None",
                    "Subject": e.subject or "-",
                    "Received At": e.received_at,
                    "Status": e.processing_status,
                    "Intent": e.classified_intent or "-",
                    "Duplicate Of": e.duplicate_of or "-",
                    "Suspicious Patterns": str(e.suspicious_patterns or []),
                })
            st.dataframe(pd.DataFrame(q_data), width="stretch", hide_index=True)

            email_map = {e.email_id: e for e in queue_emails}
            email_ids = list(email_map.keys())

            if email_ids:
                st.markdown("##### ✉️ Inspect Email Content")
                sel_email_id = st.selectbox(
                    "Select an email to inspect:",
                    options=email_ids,
                )
                chosen_email = email_map.get(sel_email_id)
                if chosen_email:
                    ecol1, ecol2 = st.columns([1, 1])
                    with ecol1:
                        st.write(f"**Email ID:** `{chosen_email.email_id}`")
                        st.write(f"**Sender:** {chosen_email.sender}")
                        st.write(f"**Subject:** {chosen_email.subject}")
                        st.write(f"**Status:** `{chosen_email.processing_status}`")
                    with ecol2:
                        st.write(f"**Has Attachment Reference:** {'Yes 📎' if chosen_email.has_attachment_ref else 'No'}")
                        st.write(f"**Has Quoted Text:** {'Yes' if chosen_email.has_quote else 'No'}")
                        st.write(f"**Suspicious Patterns:** `{chosen_email.suspicious_patterns}`")
                    st.text_area("Email Body", chosen_email.body_clean or chosen_email.body_raw or "", height=180)

    # ---------------------------------------------------------------------------
    # Tab 3: Operational Charts
    # ---------------------------------------------------------------------------

    with tab_charts:
        st.subheader("📊 Operational Analytics Charts")
        st.caption(f"Charts dynamically update based on sidebar filters ({len(filtered_list)} records).")

        if not filtered_list:
            st.info("No records match the selected filters to render charts.")
        else:
            def _count(values) -> pd.DataFrame:
                counts: dict[str, int] = {}
                for v in values:
                    counts[v] = counts.get(v, 0) + 1
                return pd.DataFrame(
                    [{"Label": k, "Count": n} for k, n in counts.items()]
                )

            chart_col1, chart_col2 = st.columns(2)

            with chart_col1:
                st.markdown("##### Booking Status Breakdown")
                status_df = _count(b.processing_status for b in filtered_list)
                fig_status = px.pie(
                    status_df,
                    names="Label",
                    values="Count",
                    hole=0.45,
                    color="Label",
                    color_discrete_map={
                        "READY": "#198754",
                        "VALIDATION_REQUIRED": "#ffc107",
                        "CANCELLED": "#dc3545",
                    },
                )
                st.plotly_chart(fig_status, width="stretch")

            with chart_col2:
                st.markdown("##### Bookings per Customer")
                cust_df = _count(
                    get_customer_display_name(b.customer_id) if b.customer_id else "Unknown / Missing"
                    for b in filtered_list
                )
                fig_cust = px.bar(cust_df, x="Label", y="Count", color="Label", text_auto=True)
                fig_cust.update_layout(showlegend=False, xaxis_title="Customer", yaxis_title="Count")
                st.plotly_chart(fig_cust, width="stretch")

            chart_col3, chart_col4 = st.columns(2)
            with chart_col3:
                st.markdown("##### POL (Port of Loading) Distribution")
                pol_df = _count(
                    get_port_display_name(b.pol) if b.pol else "Unknown / Missing"
                    for b in filtered_list
                )
                fig_pol = px.bar(pol_df, x="Label", y="Count", text_auto=True)
                fig_pol.update_layout(xaxis_title="Port", yaxis_title="Count")
                st.plotly_chart(fig_pol, width="stretch")

            with chart_col4:
                st.markdown("##### POD (Port of Discharge) Distribution")
                pod_df = _count(
                    get_port_display_name(b.pod) if b.pod else "Unknown / Missing"
                    for b in filtered_list
                )
                fig_pod = px.bar(pod_df, x="Label", y="Count", text_auto=True)
                fig_pod.update_layout(xaxis_title="Port", yaxis_title="Count")
                st.plotly_chart(fig_pod, width="stretch")

    # ---------------------------------------------------------------------------
    # Tab 4: Natural Language Chatbot
    # ---------------------------------------------------------------------------

    with tab_chat:
        st.subheader("💬 Operations Assistant")
        st.caption("Ask natural-language questions about database records.")

        st.markdown("**Sample Questions:**")
        scol1, scol2, scol3, scol4 = st.columns(4)
        quick_query = None
        if scol1.button("ABC Shipping bookings", width="stretch"):
            quick_query = "ABC Shipping için kaç rezervasyon var?"
        if scol2.button("Departing from Izmir", width="stretch"):
            quick_query = "Izmir çıkışlı rezervasyonları göster"
        if scol3.button("Pending validation", width="stretch"):
            quick_query = "Hangi rezervasyonlar doğrulama bekliyor?"
        if scol4.button("Latest 5 bookings", width="stretch"):
            quick_query = "Son 5 rezervasyonu listele"

        # Display chat history
        for msg in st.session_state["chat_messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if "table_data" in msg and msg["table_data"]:
                    st.dataframe(pd.DataFrame(msg["table_data"]), width="stretch", hide_index=True)
                if "filter_updates" in msg and msg["filter_updates"]:
                    updates = msg["filter_updates"]
                    st.button(
                        f"🎯 Sync Dashboard Filters ({updates})",
                        key=f"sync_{msg.get('id', id(msg))}",
                        on_click=apply_chat_filter,
                        args=(updates,),
                    )

        # Chat input
        user_input = st.chat_input("e.g. How many bookings for ABC Shipping? Or bookings departing from Izmir...")
        active_prompt = quick_query or user_input

        if active_prompt:
            st.session_state["chat_messages"].append({"role": "user", "content": active_prompt})
            with st.chat_message("user"):
                st.markdown(active_prompt)

            with st.spinner("Querying database..."):
                response = answer_query(active_prompt)

            msg_record = {
                "role": "assistant",
                "content": response.answer,
                "id": datetime.now().timestamp(),
            }

            if response.data and isinstance(response.data, list):
                msg_record["table_data"] = response.data
            if response.filter_updates:
                msg_record["filter_updates"] = response.filter_updates

            st.session_state["chat_messages"].append(msg_record)

            with st.chat_message("assistant"):
                st.markdown(response.answer)
                if "table_data" in msg_record:
                    st.dataframe(pd.DataFrame(msg_record["table_data"]), width="stretch", hide_index=True)
                if "filter_updates" in msg_record:
                    st.button(
                        f"🎯 Sync Dashboard Filters ({msg_record['filter_updates']})",
                        key=f"sync_now_{msg_record['id']}",
                        on_click=apply_chat_filter,
                        args=(msg_record["filter_updates"],),
                    )


if __name__ == "__main__":
    main()
