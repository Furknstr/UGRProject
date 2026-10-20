"""Lookup helpers for the validator — S8.

These are thin wrappers around :mod:`ugr_booking.master_data` that also
handle the *ambiguous* case (e.g. "JEDDAH / YANBU" → AMBIGUOUS_PORT).
"""

from __future__ import annotations

import re

from ugr_booking.master_data import (
    resolve_customer,
    resolve_port,
    suggest_customer_from_domain,
)


# ---------------------------------------------------------------------------
# Port lookup
# ---------------------------------------------------------------------------

_SLASH_SPLIT_RE = re.compile(r"\s*/\s*")


def lookup_port(raw: str | None) -> tuple[str | None, str]:
    """Resolve a raw port string to a LOCODE.

    Returns
    -------
    (locode, flag)
        - locode is the resolved LOCODE or ``None``
        - flag is one of:
            - ``""``               — resolved cleanly
            - ``"AMBIGUOUS_PORT"`` — slash-separated list (cannot auto-pick)
            - ``"UNKNOWN_PORT"``   — not in master data
    """
    if not raw:
        return None, "UNKNOWN_PORT"

    # Check for slash-separated alternatives first
    parts = _SLASH_SPLIT_RE.split(raw.strip())
    if len(parts) > 1:
        # Ambiguous — do not attempt resolution
        return None, "AMBIGUOUS_PORT"

    locode = resolve_port(raw)
    if locode is None:
        return None, "UNKNOWN_PORT"
    return locode, ""


# ---------------------------------------------------------------------------
# Customer lookup
# ---------------------------------------------------------------------------

def lookup_customer(
    raw: str | None,
    sender: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a raw customer name to a customer_id.

    Parameters
    ----------
    raw:
        Raw customer string extracted by the LLM.
    sender:
        Sender email address (used only for a *suggestion*, not resolution).

    Returns
    -------
    (customer_id, suggestion)
        - ``customer_id`` — resolved ID, or ``None`` if unknown
        - ``suggestion``  — domain-based suggestion (hint only), or ``None``
    """
    customer_id = resolve_customer(raw)
    suggestion = suggest_customer_from_domain(sender) if customer_id is None else None
    return customer_id, suggestion
