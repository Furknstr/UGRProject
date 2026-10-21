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
                    "Merhaba! Ben UGR Rezervasyon Operasyon Asistanıyım. "
                    "Rezervasyonlar, müşteriler, limanlar veya doğrulama bekleyen kayıtlar hakkında soru sorabilirsiniz."
                ),
            }
        ]

    # ---------------------------------------------------------------------------
    # Header
    # ---------------------------------------------------------------------------

    header_col1, header_col2 = st.columns([4, 1])
    with header_col1:
        st.title("🚢 UGR Lines — AI Booking Intake Assistant")
        st.caption("Kontrollü AI Boru Hattı, Güvenli Doğrulama ve Operasyonel Takip Paneli")
    with header_col2:
        st.write("")
        if st.button("🔄 Verileri Yenile", width="stretch"):
            st.rerun()

    # ---------------------------------------------------------------------------
    # Sidebar: Filters
    # ---------------------------------------------------------------------------

    with st.sidebar:
        st.header("🔍 Operasyonel Filtreler")

        # Customer options
        customers = list_known_customers()
        cust_options = ["ALL"] + [c["customer_id"] for c in customers]
        cust_format = lambda x: "Tümü (All Customers)" if x == "ALL" else get_customer_display_name(x)

        st.selectbox(
            "Müşteri",
            options=cust_options,
            format_func=cust_format,
            key="filter_customer",
        )

        # Status options
        status_options = ["ALL", "READY", "VALIDATION_REQUIRED", "CANCELLED"]
        status_format = {
            "ALL": "Tümü (All Statuses)",
            "READY": "✅ READY (Hazır)",
            "VALIDATION_REQUIRED": "⚠️ VALIDATION_REQUIRED (İnceleme)",
            "CANCELLED": "❌ CANCELLED (İptal)",
        }
        st.selectbox(
            "İşlem Durumu",
            options=status_options,
            format_func=lambda x: status_format.get(x, x),
            key="filter_status",
        )

        # Ports
        ports = list_known_ports()
        port_codes = ["ALL"] + sorted([p["port_code"] for p in ports])
        port_format = lambda x: "Tümü (All Ports)" if x == "ALL" else get_port_display_name(x)

        st.selectbox(
            "Çıkış Limanı (POL)",
            options=port_codes,
            format_func=port_format,
            key="filter_pol",
        )

        st.selectbox(
            "Varış Limanı (POD)",
            options=port_codes,
            format_func=port_format,
            key="filter_pod",
        )

        st.write("")
        if st.button("Filtreleri Sıfırla", on_click=reset_filters, width="stretch"):
            st.rerun()

        st.divider()
        st.markdown(
            """
            **Sistem Kuralları:**
            - **Guard:** Model asla doğrudan DB yazamaz.
            - **Doğrulama:** Eksik/şüpheli kayıtlar `VALIDATION_REQUIRED` olur.
            - **İzlenebilirlik:** Her rezervasyon orijinal e-postasına bağlıdır.
            """
        )

    # ---------------------------------------------------------------------------
    # Global Metrics (KPI Cards)
    # ---------------------------------------------------------------------------

    stats = summary_stats()

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    with kpi1:
        st.metric("Toplam Rezervasyon", stats["total_bookings"], help="Yalnızca doğrulanmış rezervasyon satırları")
    with kpi2:
        st.metric("READY (Hazır)", stats["ready_count"], delta=f"{stats['ready_count']} hazır", delta_color="normal")
    with kpi3:
        st.metric("VALIDATION REQUIRED", stats["validation_required_count"], delta=f"{stats['validation_required_count']} inceleme", delta_color="inverse")
    with kpi4:
        st.metric("CANCELLED", stats["cancelled_count"], delta=f"{stats['cancelled_count']} iptal", delta_color="off")
    with kpi5:
        st.metric("İnceleme Kuyruğu", stats["review_queue_total"], help="Yetim, kopya veya şüpheli e-postalar", delta=f"{stats['orphan_count']} yetim", delta_color="inverse")

    st.write("")

    # ---------------------------------------------------------------------------
    # Main Tabs: Bookings, Review Queue, Charts, Chatbot
    # ---------------------------------------------------------------------------

    tab_bookings, tab_review, tab_charts, tab_chat = st.tabs(
        ["📋 Rezervasyon Listesi", "🚨 İnceleme Kuyruğu", "📊 Operasyonel Grafikler", "💬 Operasyonel Chatbot"]
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

        st.subheader(f"Rezervasyonlar ({len(filtered_list)} kayıt gösteriliyor)")

        if not filtered_list:
            st.info("Seçili filtrelere uygun rezervasyon kaydı bulunamadı.")
        else:
            # Build DataFrame for table view
            table_rows = []
            for b in filtered_list:
                table_rows.append({
                    "ID": b.id,
                    "Ref": b.booking_ref or f"BKG-{b.id:04d}",
                    "Müşteri": get_customer_display_name(b.customer_id),
                    "POL": b.pol or "⚠️ Eksik",
                    "POD": b.pod or "⚠️ Eksik",
                    "Miktar": b.quantity or 0,
                    "Tarih": b.requested_date or "⚠️ Belirtilmemiş",
                    "Durum": b.processing_status,
                    "Kaynak E-posta": b.source_email_id,
                    "Bayrak Sayısı": len(b.validation_flags or []),
                })
            df_bookings = pd.DataFrame(table_rows)

            # Interactive Table
            st.dataframe(
                df_bookings,
                width="stretch",
                hide_index=True,
                column_config={
                    "Durum": st.column_config.TextColumn(
                        "Durum",
                        help="İşlem durumu",
                    ),
                },
            )

            st.divider()

            # Detailed Booking Inspector with safe formatting (no key to prevent state conflict)
            st.subheader("🔍 Rezervasyon Detay İnceleyicisi")

            booking_map = {b.id: b for b in filtered_list}
            options_ids = list(booking_map.keys())

            def _format_booking_label(bid: int) -> str:
                b = booking_map.get(bid)
                if not b:
                    return f"ID: {bid}"
                ref = b.booking_ref or f"BKG-{b.id:04d}"
                cust = get_customer_display_name(b.customer_id)
                return f"ID: {bid} | Ref: {ref} | Müşteri: {cust}"

            selected_id = st.selectbox(
                "İncelemek için rezervasyon seçin:",
                options=options_ids,
                format_func=_format_booking_label,
            )

            curr_b = booking_map.get(selected_id)
            if curr_b:
                dcol1, dcol2, dcol3 = st.columns([1, 1, 1])
                with dcol1:
                    st.markdown("**Temel Bilgiler**")
                    st.write(f"- **Booking Ref:** `{curr_b.booking_ref}`")
                    st.write(f"- **Müşteri (Çözülen):** {get_customer_display_name(curr_b.customer_id)}")
                    st.write(f"- **Müşteri (Ham Metin):** `{curr_b.customer_raw}`")
                    st.write(f"- **Yük Açıklaması:** {curr_b.cargo_description or '-'}")
                    st.write(f"- **Miktar:** {curr_b.quantity} (Kaynak: {curr_b.quantity_source or 'explicit'})")
                with dcol2:
                    st.markdown("**Lojistik ve Boyutlar**")
                    st.write(f"- **POL:** {get_port_display_name(curr_b.pol)} (Ham: `{curr_b.pol_raw}`)")
                    st.write(f"- **POD:** {get_port_display_name(curr_b.pod)} (Ham: `{curr_b.pod_raw}`)")
                    st.write(f"- **Talep Tarihi:** `{curr_b.requested_date}` (Ham: `{curr_b.requested_date_raw}`)")
                    st.write(f"- **Ağırlık:** {curr_b.weight_kg_per_unit} kg (Ham: `{curr_b.weight_raw}`)")
                    st.write(f"- **Boyutlar (U/G/Y):** {curr_b.length_m} x {curr_b.width_m} x {curr_b.height_m} m")
                with dcol3:
                    st.markdown("**İşlem Durumu & Kaynak**")
                    st.write(f"- **Durum:** `{curr_b.processing_status}`")
                    st.write(f"- **Gemi / Sefer:** {curr_b.vessel or '-'} / {curr_b.voyage or '-'}")
                    st.write(f"- **İletişim:** {curr_b.contact_name or '-'} ({curr_b.contact_email or '-'})")
                    st.write(f"- **Kaynak E-posta ID:** `{curr_b.source_email_id}`")
                    st.write(f"- **Kayıt Tarihi:** {curr_b.created_at}")

                # Validation Flags list
                st.markdown("##### ⚠️ Doğrulama Bayrakları (Validation Flags)")
                flags = curr_b.validation_flags or []
                if not flags:
                    st.success("Tüm doğrulamalar başarılı! Hiçbir engelleyici veya uyarı bayrağı bulunmuyor (READY).")
                else:
                    flag_rows = []
                    for fl in flags:
                        flag_rows.append({
                            "Önem Derecesi": "🚫 BLOCKING" if fl.get("severity") == "BLOCKING" else "⚠️ WARNING",
                            "Alan": fl.get("field"),
                            "Kural Kodu": fl.get("code"),
                            "Ham Değer": str(fl.get("raw")),
                            "Öneri / Açıklama": fl.get("suggestion") or "-",
                        })
                    st.dataframe(pd.DataFrame(flag_rows), width="stretch", hide_index=True)

                # Traceability section: Source Email View
                with st.expander("📬 Orijinal E-posta ve İzlenebilirlik"):
                    if curr_b.source_email:
                        src = curr_b.source_email
                        st.write(f"**Konu:** {src.subject}")
                        st.write(f"**Gönderici:** {src.sender} | **Alınma Tarihi:** {src.received_at}")
                        st.write(f"**İntent:** `{src.classified_intent}` | **Durum:** `{src.processing_status}`")
                        st.text_area("Temizlenmiş E-posta Metni (Clean Body)", src.body_clean or src.body_raw or "", height=150)
                    else:
                        st.write("Kaynak e-posta bilgisi bulunamadı.")

    # ---------------------------------------------------------------------------
    # Tab 2: Review Queue (İnceleme Kuyruğu)
    # ---------------------------------------------------------------------------

    with tab_review:
        st.subheader("🚨 Operatör İnceleme Kuyruğu (Review Queue)")
        st.caption("Doğrudan rezervasyona dönüşemeyen, insan müdahalesi veya onay bekleyen kaynak e-postalar.")

        rev_category = st.radio(
            "Kategori Filtresi",
            options=["Tümü", "Yetimler (ORPHAN)", "Kopyalar (DUPLICATE)", "Güvenlik Reddi (NEEDS_REVIEW)", "Şüpheli İçerik", "Ek Dosya Referansı", "Boş Gövde"],
            horizontal=True,
        )

        cat_map = {
            "Tümü": None,
            "Yetimler (ORPHAN)": "orphan",
            "Kopyalar (DUPLICATE)": "duplicate",
            "Güvenlik Reddi (NEEDS_REVIEW)": "needs_review",
            "Şüpheli İçerik": "suspicious",
            "Ek Dosya Referansı": "attachment",
            "Boş Gövde": "empty_body",
        }

        queue_emails = review_queue(category=cat_map.get(rev_category))
        st.write(f"**Kuyruktaki Kayıt Sayısı:** {len(queue_emails)}")

        if not queue_emails:
            st.success("Seçili kategoride inceleme bekleyen e-posta bulunmuyor.")
        else:
            q_data = []
            for e in queue_emails:
                q_data.append({
                    "E-posta ID": e.email_id,
                    "Gönderici": e.sender or "⚠️ Yok",
                    "Konu": e.subject or "-",
                    "Alınma Zamanı": e.received_at,
                    "Durum": e.processing_status,
                    "İntent": e.classified_intent or "-",
                    "Kopya ID": e.duplicate_of or "-",
                    "Şüpheli Örüntü": str(e.suspicious_patterns or []),
                })
            st.dataframe(pd.DataFrame(q_data), width="stretch", hide_index=True)

            email_map = {e.email_id: e for e in queue_emails}
            email_ids = list(email_map.keys())

            if email_ids:
                st.markdown("##### ✉️ E-posta İçeriği İncele")
                sel_email_id = st.selectbox(
                    "İncelemek için e-posta seçin:",
                    options=email_ids,
                )
                chosen_email = email_map.get(sel_email_id)
                if chosen_email:
                    ecol1, ecol2 = st.columns([1, 1])
                    with ecol1:
                        st.write(f"**E-posta ID:** `{chosen_email.email_id}`")
                        st.write(f"**Gönderici:** {chosen_email.sender}")
                        st.write(f"**Konu:** {chosen_email.subject}")
                        st.write(f"**Durum:** `{chosen_email.processing_status}`")
                    with ecol2:
                        st.write(f"**Ek Dosya Referansı Var mı:** {'Evet 📎' if chosen_email.has_attachment_ref else 'Hayır'}")
                        st.write(f"**Alıntı Var mı:** {'Evet' if chosen_email.has_quote else 'Hayır'}")
                        st.write(f"**Şüpheli Örüntüler:** `{chosen_email.suspicious_patterns}`")
                    st.text_area("E-posta Gövdesi", chosen_email.body_clean or chosen_email.body_raw or "", height=180)

    # ---------------------------------------------------------------------------
    # Tab 3: Operational Charts
    # ---------------------------------------------------------------------------

    with tab_charts:
        st.subheader("📊 Operasyonel Analiz Grafikleri")

        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.markdown("##### Rezervasyon Durum Dağılımı")
            status_df = pd.DataFrame([
                {"Durum": k, "Adet": v} for k, v in stats["status_counts"].items()
            ])
            fig_status = px.pie(
                status_df,
                names="Durum",
                values="Adet",
                hole=0.45,
                color="Durum",
                color_discrete_map={
                    "READY": "#198754",
                    "VALIDATION_REQUIRED": "#ffc107",
                    "CANCELLED": "#dc3545",
                },
            )
            st.plotly_chart(fig_status, width="stretch")

        with chart_col2:
            st.markdown("##### Müşteri Başına Rezervasyon Dağılımı")
            cust_df = pd.DataFrame([
                {"Müşteri": get_customer_display_name(k) if k != "Unresolved" else "Bilinmeyen / Eksik", "Adet": v}
                for k, v in stats["by_customer"].items()
            ])
            fig_cust = px.bar(
                cust_df,
                x="Müşteri",
                y="Adet",
                color="Müşteri",
                text_auto=True,
            )
            fig_cust.update_layout(showlegend=False)
            st.plotly_chart(fig_cust, width="stretch")

        chart_col3, chart_col4 = st.columns(2)
        with chart_col3:
            st.markdown("##### POL (Çıkış Limanı) Dağılımı")
            pol_df = pd.DataFrame([
                {"Liman": get_port_display_name(k), "Adet": v} for k, v in stats["by_pol"].items()
            ])
            fig_pol = px.bar(pol_df, x="Liman", y="Adet", text_auto=True)
            st.plotly_chart(fig_pol, width="stretch")

        with chart_col4:
            st.markdown("##### POD (Varış Limanı) Dağılımı")
            pod_df = pd.DataFrame([
                {"Liman": get_port_display_name(k), "Adet": v} for k, v in stats["by_pod"].items()
            ])
            fig_pod = px.bar(pod_df, x="Liman", y="Adet", text_auto=True)
            st.plotly_chart(fig_pod, width="stretch")

    # ---------------------------------------------------------------------------
    # Tab 4: Natural Language Chatbot
    # ---------------------------------------------------------------------------

    with tab_chat:
        st.subheader("💬 Operasyonel Chatbot")
        st.caption("Veritabanı kayıtları üzerinden doğal dilde operasyonel sorular sorun.")

        st.markdown("**Örnek Sorular:**")
        scol1, scol2, scol3, scol4 = st.columns(4)
        quick_query = None
        if scol1.button("ABC Shipping rezervasyonları", width="stretch"):
            quick_query = "ABC Shipping için kaç rezervasyon var?"
        if scol2.button("Izmir çıkışlı rezervasyonlar", width="stretch"):
            quick_query = "Izmir çıkışlı rezervasyonları göster"
        if scol3.button("Doğrulama bekleyenler", width="stretch"):
            quick_query = "Hangi rezervasyonlar doğrulama bekliyor?"
        if scol4.button("Son 5 rezervasyon", width="stretch"):
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
                        f"🎯 Dashboard Filtrelerini Güncelle ({updates})",
                        key=f"sync_{msg.get('id', id(msg))}",
                        on_click=apply_chat_filter,
                        args=(updates,),
                    )

        # Chat input
        user_input = st.chat_input("Örn: ABC Shipping için kaç rezervasyon var? Veya Izmir çıkışlı olanlar...")
        active_prompt = quick_query or user_input

        if active_prompt:
            st.session_state["chat_messages"].append({"role": "user", "content": active_prompt})
            with st.chat_message("user"):
                st.markdown(active_prompt)

            with st.spinner("Sorgulanıyor..."):
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
                        f"🎯 Dashboard Filtrelerini Güncelle ({msg_record['filter_updates']})",
                        key=f"sync_now_{msg_record['id']}",
                        on_click=apply_chat_filter,
                        args=(msg_record["filter_updates"],),
                    )


if __name__ == "__main__":
    main()
