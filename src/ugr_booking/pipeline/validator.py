"""Booking validator — S8.

Takes an :class:`~ugr_booking.models.schemas.LLMResponse` (parsed by the LLM)
together with the :class:`~ugr_booking.pipeline.preprocessor.ParsedEmail` (from
the preprocessor) and produces a :class:`ValidationResult` for each booking split.

Rules applied per booking split (in order)
-------------------------------------------
1.  Customer resolution           → UNKNOWN_CUSTOMER
2.  Port resolution (POL / POD)   → UNKNOWN_PORT / AMBIGUOUS_PORT /
                                     PORT_ACCENT_NORMALIZED
3.  VIN validation                → INVALID_VIN
4.  VIN/quantity cross-check      → VIN_QUANTITY_MISMATCH / QUANTITY_FROM_VIN_COUNT
5.  Date normalisation            → INVALID_DATE / AMBIGUOUS_DATE / DATE_IN_PAST
6.  Weight parsing                → NUMBER_NORMALIZED / UNIT_AMBIGUOUS /
                                     IMPLAUSIBLE_VALUE
7.  Dimension parsing             → NUMBER_NORMALIZED / UNIT_AMBIGUOUS /
                                     IMPLAUSIBLE_VALUE
8.  Attachment evidence           → MISSING_ATTACHMENT
9.  Encoding warning (pre-proc)   → ENCODING_WARNING
10. Suspicious content (pre-proc) → SUSPICIOUS_CONTENT
11. Sender-domain mismatch        → SENDER_CUSTOMER_MISMATCH
12. Required-field check          → MISSING_REQUIRED

Email-level flags (ENCODING_WARNING, SUSPICIOUS_CONTENT, EMPTY_BODY,
MISSING_SENDER, MISSING_ATTACHMENT) are also collected in
:attr:`ValidationResult.email_flags` regardless of intent, so they are never
silently dropped for UPDATE/CANCEL/NON_BOOKING emails.

Responsibility boundary
-----------------------
- ``HALLUCINATED_VALUE`` and ``DUPLICATE_REF_CONFLICT`` are set by the
  *agent* (S9), which has DB context.
- ``POSSIBLE_DUPLICATE`` (from the preprocessor) passes through to the
  agent for final escalation decision.
- ``status`` (READY / VALIDATION_REQUIRED) is a derived property; the
  validator never sets it directly.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import process as fuzz_process, fuzz

from ugr_booking.config import (
    VIN_PATTERN,
    WEIGHT_KG_MIN,
    WEIGHT_KG_MAX,
    DIM_M_MIN,
    DIM_M_MAX,
)
from ugr_booking.enums import BLOCKING_FLAGS, WARNING_FLAGS, BookingStatus
from ugr_booking.master_data import _ports, suggest_customer_from_domain
from ugr_booking.models.schemas import ExtractedBooking, LLMResponse
from ugr_booking.normalize import parse_date, parse_number
from ugr_booking.pipeline.preprocessor import ParsedEmail
from ugr_booking.tools.lookup import lookup_customer, lookup_port


# ---------------------------------------------------------------------------
# Flag codes that are email-level (not tied to a single booking split).
# These are still copied into each BookingValidation for convenience, but
# are also surfaced on ValidationResult.email_flags.
# ---------------------------------------------------------------------------

_EMAIL_LEVEL_FLAGS = frozenset({
    "ENCODING_WARNING",
    "SUSPICIOUS_CONTENT",
    "EMPTY_BODY",
    "MISSING_SENDER",
    "MISSING_ATTACHMENT",
})

# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------


@dataclass
class ValidationFlag:
    """One validation signal attached to a booking split."""

    code: str            # must be in BLOCKING_FLAGS | WARNING_FLAGS
    severity: str        # "blocking" | "warning" — derived from code
    field: str | None = None       # affected field name, if applicable
    raw: str | None = None         # raw value that triggered the flag
    suggestion: str | None = None  # best-effort corrected value


@dataclass
class BookingValidation:
    """Validation result for a single booking split."""

    split_index: int
    customer_id: str | None = None
    customer_raw: str | None = None
    customer_suggestion: str | None = None

    pol: str | None = None
    pol_raw: str | None = None
    pod: str | None = None
    pod_raw: str | None = None
    pod_suggestion: str | None = None   # fuzzy match when encoding corrupts POD
    pod_suggestion_source: str | None = None  # "fuzzy" | None

    quantity: int | None = None
    quantity_source: str | None = None  # "explicit" | "vin_count"
    vin_list: list[str] = field(default_factory=list)

    requested_date: str | None = None
    requested_date_raw: str | None = None

    weight_kg_per_unit: float | None = None
    weight_raw: str | None = None
    length_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    dims_raw: str | None = None

    cargo_description: str | None = None
    booking_ref: str | None = None
    vessel: str | None = None
    voyage: str | None = None
    rate_text: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None

    flags: list[ValidationFlag] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def blocking_flags(self) -> list[str]:
        return [f.code for f in self.flags if f.severity == "blocking"]

    @property
    def warning_flags(self) -> list[str]:
        return [f.code for f in self.flags if f.severity == "warning"]

    @property
    def status(self) -> str:
        return (
            BookingStatus.READY
            if not self.blocking_flags
            else BookingStatus.VALIDATION_REQUIRED
        )


@dataclass
class ValidationResult:
    """Complete validation output for one email."""

    source_email_id: str
    message_intent: str
    bookings: list[BookingValidation] = field(default_factory=list)
    # Email-level risk flags preserved for all intents (never silently dropped).
    email_flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Compiled constants (module-level — built once)
# ---------------------------------------------------------------------------

_VIN_RE = re.compile(VIN_PATTERN)

# Relative / vague date language — word-boundary anchored to avoid
# false matches inside longer words (e.g. "thereafter", "monthly").
_RELATIVE_DATE_RE = re.compile(
    r"\b(next|last|this|week|month|year|same|usual|sailing|season|quarter|"
    r"available|after|before|around|approximately|soon|tbd|tba)\b",
    re.IGNORECASE,
)

# Dimension triplet:  "5.35 x 1.90 x 1.82 m"
_DIMS_TRIPLET_RE = re.compile(
    r"([\d.,\s]+)\s*[xX×]\s*([\d.,\s]+)\s*[xX×]\s*([\d.,\s]+)\s*"
    r"(?P<unit>cm|mm|m)?\b",
    re.IGNORECASE,
)

# Plain number with no trailing alpha unit
_NO_UNIT_RE = re.compile(r"^\d[\d.,\s]*$")

# Pre-build port lookup structures for fuzzy matching (built lazily once)
_PORT_CANDIDATES: list[str] | None = None
_PORT_CANDIDATE_TO_CODE: dict[str, str] | None = None


def _build_port_candidates() -> tuple[list[str], dict[str, str]]:
    """Build flat list of all port names + aliases for fuzzy matching."""
    global _PORT_CANDIDATES, _PORT_CANDIDATE_TO_CODE
    if _PORT_CANDIDATES is not None:
        return _PORT_CANDIDATES, _PORT_CANDIDATE_TO_CODE  # type: ignore[return-value]

    candidates: list[str] = []
    to_code: dict[str, str] = {}
    for p in _ports():
        code = p["port_code"]
        for label in [p["name"]] + p.get("aliases", []):
            candidates.append(label)
            to_code[label] = code
    _PORT_CANDIDATES = candidates
    _PORT_CANDIDATE_TO_CODE = to_code
    return candidates, to_code


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _flag(code: str, **kwargs: object) -> ValidationFlag:
    """Create a ValidationFlag; raises ValueError for unknown flag codes."""
    if code in BLOCKING_FLAGS:
        severity = "blocking"
    elif code in WARNING_FLAGS:
        severity = "warning"
    else:
        raise ValueError(
            f"Unknown flag code {code!r}. "
            f"Add it to BLOCKING_FLAGS or WARNING_FLAGS in enums.py first."
        )
    return ValidationFlag(code=code, severity=severity, **kwargs)  # type: ignore[arg-type]


def _has_combining_marks(raw: str) -> bool:
    """Return True if *raw* contains non-ASCII combining/accent characters.

    Uses NFD decomposition so that composed accented characters (e.g. Ã)
    are correctly detected.  U+FFFD (replacement char from encoding errors)
    is NOT an accent and is not counted here.
    """
    nfd = unicodedata.normalize("NFD", raw)
    return any(
        unicodedata.category(ch) == "Mn"   # combining marks
        for ch in nfd
        if ch != "\ufffd"
    )


def _is_relative_date(raw: str) -> bool:
    """Return True when *raw* contains relative/vague date language."""
    return bool(_RELATIVE_DATE_RE.search(raw))


def _normalise_vins(vin_list: list[str]) -> list[str]:
    """Strip whitespace and upper-case each VIN."""
    return [v.strip().upper() for v in vin_list]


def _validate_vins(vin_list: list[str]) -> list[str]:
    """Return VINs that fail the full-match VIN_PATTERN check."""
    return [v for v in vin_list if not _VIN_RE.fullmatch(v)]


def _number_was_normalised(raw: str, value: float) -> bool:
    """Return True if *raw* required locale-aware parsing to reach *value*.

    A plain ``float(stripped_first_token)`` would succeed for simple values
    like "1500" or "2180.5", but not for "12.500,50" or "12,400".
    We delegate to parse_number with a plain float comparison:
    if the raw numeric token can be parsed directly by Python's float() and
    that gives the same value, no normalisation happened.
    """
    # Strip trailing unit text
    numeric_part = re.sub(r"\s+[a-zA-Z%]+$", "", raw.strip()).strip()
    try:
        direct = float(numeric_part.replace(" ", ""))
        return abs(direct - value) > 1e-9  # different → normalisation occurred
    except ValueError:
        return True  # couldn't parse directly → definitely normalised


def _suggest_pod_from_garbled(garbled: str | None) -> tuple[str | None, str | None]:
    """Attempt to recover a port code from a corruption-damaged raw string.

    Strategy
    --------
    1. Strip U+FFFD replacement characters to recover the readable skeleton.
    2. Fuzzy-match against all port names/aliases (score_cutoff=70).
    3. Return (locode, "fuzzy") on success, (None, None) on failure.

    This is evidence-based (uses the actual garbled text) and independent
    of customer identity, unlike the previous hard-coded mapping.
    """
    if not garbled:
        return None, None
    cleaned = garbled.replace("\ufffd", "").strip()
    if not cleaned:
        return None, None

    candidates, to_code = _build_port_candidates()
    result = fuzz_process.extractOne(
        cleaned,
        candidates,
        scorer=fuzz.ratio,
        score_cutoff=70,
    )
    if result is None:
        return None, None
    best_match: str = result[0]
    return to_code.get(best_match), "fuzzy"


def _parse_weight(
    weight_raw: str | None,
) -> tuple[float | None, list[ValidationFlag]]:
    """Parse weight raw string → (kg_per_unit, flags).

    Flags produced
    --------------
    UNIT_AMBIGUOUS   — no alphabetic unit suffix found
    IMPLAUSIBLE_VALUE — parsed value outside [WEIGHT_KG_MIN, WEIGHT_KG_MAX]
    NUMBER_NORMALIZED — locale-aware parsing was needed
    """
    flags: list[ValidationFlag] = []
    if not weight_raw:
        return None, flags

    raw = weight_raw.strip()

    # No unit suffix → weight cannot be trusted
    if _NO_UNIT_RE.match(raw):
        flags.append(_flag("UNIT_AMBIGUOUS", field="weight_kg_per_unit", raw=raw))
        return None, flags

    value = parse_number(raw, context="weight")
    if value is None:
        return None, flags

    if not (WEIGHT_KG_MIN <= value <= WEIGHT_KG_MAX):
        flags.append(
            _flag("IMPLAUSIBLE_VALUE", field="weight_kg_per_unit",
                  raw=raw, suggestion=str(value))
        )
        return None, flags

    if _number_was_normalised(raw, value):
        flags.append(_flag("NUMBER_NORMALIZED", field="weight_kg_per_unit", raw=raw))

    return value, flags


def _parse_dims_triplet(
    dims_raw: str | None,
) -> tuple[float | None, float | None, float | None, list[ValidationFlag]]:
    """Parse a dimension triplet → (L_m, W_m, H_m, flags).

    Flags produced
    --------------
    UNIT_AMBIGUOUS   — no unit found and values look implausibly large
    IMPLAUSIBLE_VALUE — any dimension outside [DIM_M_MIN, DIM_M_MAX]
    NUMBER_NORMALIZED — locale-aware parsing was needed
    """
    flags: list[ValidationFlag] = []
    if not dims_raw:
        return None, None, None, flags

    m = _DIMS_TRIPLET_RE.search(dims_raw)
    if not m:
        return None, None, None, flags

    unit_str = m.group("unit")
    unit = (unit_str or "m").lower()
    parts_raw = [m.group(1).strip(), m.group(2).strip(), m.group(3).strip()]

    # UNIT_AMBIGUOUS: check the *raw* parsed values (before any plausibility drop)
    # so the flag fires even when all dims end up None.
    if unit_str is None:
        raw_vals = [parse_number(p, context="dim") for p in parts_raw]
        if any(v is not None and v > 30 for v in raw_vals):
            flags.append(_flag("UNIT_AMBIGUOUS", field="dims_raw", raw=dims_raw))

    dims: list[float | None] = []
    normalised_any = False
    implausible_any = False

    for part in parts_raw:
        val = parse_number(part, context="dim")
        if val is None:
            dims.append(None)
            continue
        # Unit conversion
        if unit == "cm":
            val = round(val / 100, 10)
        elif unit == "mm":
            val = round(val / 1000, 10)

        if not (DIM_M_MIN <= val <= DIM_M_MAX):
            implausible_any = True
            dims.append(None)
        else:
            if _number_was_normalised(part, val if unit == "m" else val * (100 if unit == "cm" else 1000)):
                normalised_any = True
            dims.append(val)

    if implausible_any:
        flags.append(_flag("IMPLAUSIBLE_VALUE", field="dims_raw", raw=dims_raw))
    if normalised_any:
        flags.append(_flag("NUMBER_NORMALIZED", field="dims_raw", raw=dims_raw))

    return dims[0], dims[1], dims[2], flags


# ---------------------------------------------------------------------------
# Per-booking rule helpers  (each returns list[ValidationFlag])
# ---------------------------------------------------------------------------


def _rule_customer(
    extracted: ExtractedBooking,
    email: ParsedEmail,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    customer_id, suggestion = lookup_customer(extracted.customer, email.sender)
    bv.customer_id = customer_id
    bv.customer_suggestion = suggestion
    if customer_id is None:
        return [_flag("UNKNOWN_CUSTOMER", field="customer_id",
                      raw=extracted.customer, suggestion=suggestion)]
    return []


def _resolve_port(
    raw: str | None,
    field_name: str,
    bv: BookingValidation,
    encoding_warning: bool,
) -> list[ValidationFlag]:
    """Resolve one port field; handle encoding corruption separately."""
    flags: list[ValidationFlag] = []
    code, port_flag = lookup_port(raw)

    if port_flag == "UNKNOWN_PORT" and encoding_warning and raw:
        # Encoding corruption — attempt fuzzy recovery from garbled text
        suggestion, source = _suggest_pod_from_garbled(raw)
        if field_name == "pol":
            bv.pol = None
        else:
            bv.pod = None
            bv.pod_suggestion = suggestion
            bv.pod_suggestion_source = source
        # Do NOT raise UNKNOWN_PORT; ENCODING_WARNING (added later) already blocks
    elif port_flag:
        flags.append(_flag(port_flag, field=field_name, raw=raw))
        if field_name == "pol":
            bv.pol = None
        else:
            bv.pod = None
    elif code:
        if field_name == "pol":
            bv.pol = code
        else:
            bv.pod = code
        # PORT_ACCENT_NORMALIZED: only when raw contained combining marks
        if raw and _has_combining_marks(raw):
            flags.append(
                _flag("PORT_ACCENT_NORMALIZED", field=field_name,
                      raw=raw, suggestion=code)
            )

    return flags


def _rule_ports(
    extracted: ExtractedBooking,
    email: ParsedEmail,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    flags: list[ValidationFlag] = []
    flags += _resolve_port(extracted.pol, "pol", bv, email.encoding_warning)
    flags += _resolve_port(extracted.pod, "pod", bv, email.encoding_warning)
    return flags


def _rule_vins(
    extracted: ExtractedBooking,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    """Normalise VINs, validate pattern, check for duplicates."""
    flags: list[ValidationFlag] = []
    normalised = _normalise_vins(extracted.vin_list or [])
    bv.vin_list = normalised

    bad = _validate_vins(normalised)
    if bad:
        flags.append(_flag("INVALID_VIN", field="vin_list", raw=", ".join(bad)))

    # Duplicate VINs: use a distinct field key so the dedup in the orchestrator
    # never suppresses a simultaneous pattern-failure flag on the same booking.
    if len(normalised) != len(set(normalised)):
        seen: set[str] = set()
        dupes = [v for v in normalised if v in seen or seen.add(v)]  # type: ignore[func-returns-value]
        flags.append(_flag("INVALID_VIN", field="vin_list_duplicates",
                           raw="duplicate VINs: " + ", ".join(dupes)))
    return flags


def _rule_quantity(
    extracted: ExtractedBooking,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    """Cross-check VIN count against explicit quantity.

    QUANTITY_FROM_VIN_COUNT is emitted only when quantity is *inferred*
    from the VIN list (no explicit quantity given).
    VIN_QUANTITY_MISMATCH is emitted when explicit qty and VIN count differ.
    When explicit qty matches VIN count, no flag is emitted.
    """
    flags: list[ValidationFlag] = []
    vin_count = len(bv.vin_list)
    explicit_qty = extracted.quantity

    if vin_count > 0 and explicit_qty is not None and vin_count != explicit_qty:
        flags.append(_flag("VIN_QUANTITY_MISMATCH", field="quantity",
                           raw=f"qty={explicit_qty} vins={vin_count}"))
        bv.quantity = explicit_qty
        bv.quantity_source = "explicit"
    elif vin_count > 0 and explicit_qty is None:
        bv.quantity = vin_count
        bv.quantity_source = "vin_count"
        flags.append(_flag("QUANTITY_FROM_VIN_COUNT", field="quantity",
                           raw=str(vin_count)))
    else:
        # explicit qty provided (with or without VINs) — accept as-is, no flag
        bv.quantity = explicit_qty
        bv.quantity_source = "explicit" if explicit_qty is not None else None

    return flags


def _rule_date(
    extracted: ExtractedBooking,
    email: ParsedEmail,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    """Normalise requested_date; check relative language before numeric parse."""
    flags: list[ValidationFlag] = []
    date_raw = extracted.requested_date

    # Relative / vague language → AMBIGUOUS_DATE (check FIRST, before parse_date)
    if date_raw and _is_relative_date(date_raw):
        bv.requested_date = None
        return [_flag("AMBIGUOUS_DATE", field="requested_date", raw=date_raw)]

    parsed = parse_date(date_raw)

    if parsed == "INVALID_DATE":
        bv.requested_date = None
        flags.append(_flag("INVALID_DATE", field="requested_date", raw=date_raw))
    elif parsed is None:
        bv.requested_date = None   # genuinely absent — caught by required-fields rule
    else:
        try:
            parsed_dt = date.fromisoformat(parsed)
            # received_at is stored as a naive datetime; compare date parts only
            ref_date = email.received_at.date()
            if parsed_dt < ref_date:
                flags.append(_flag("DATE_IN_PAST", field="requested_date",
                                   raw=date_raw, suggestion=parsed))
                bv.requested_date = None
            else:
                bv.requested_date = parsed
        except ValueError:
            bv.requested_date = parsed

    return flags


def _rule_weight(
    extracted: ExtractedBooking,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    val, flags = _parse_weight(extracted.weight_raw)
    bv.weight_kg_per_unit = val
    return flags


def _rule_dims(
    extracted: ExtractedBooking,
    bv: BookingValidation,
) -> list[ValidationFlag]:
    l_m, w_m, h_m, flags = _parse_dims_triplet(extracted.dims_raw)
    bv.length_m = l_m
    bv.width_m = w_m
    bv.height_m = h_m
    # Deduplicate NUMBER_NORMALIZED across weight + dims
    return flags


def _rule_attachment(email: ParsedEmail) -> list[ValidationFlag]:
    if email.has_attachment_ref:
        return [_flag("MISSING_ATTACHMENT", field=None)]
    return []


def _rule_encoding(email: ParsedEmail) -> list[ValidationFlag]:
    if email.encoding_warning:
        return [_flag("ENCODING_WARNING", field=None)]
    return []


def _rule_suspicious(email: ParsedEmail) -> list[ValidationFlag]:
    if email.suspicious_patterns:
        return [_flag("SUSPICIOUS_CONTENT", field=None,
                      raw=", ".join(email.suspicious_patterns))]
    return []


def _rule_sender_mismatch(
    bv: BookingValidation,
    email: ParsedEmail,
) -> list[ValidationFlag]:
    """SENDER_CUSTOMER_MISMATCH: resolved customer ≠ domain-suggested customer.

    Fires when:
    - Domain is known and maps to a *different* customer than resolved, OR
    - Domain is *unknown* (not in domain_map) — unrecognised sender for a
      resolved customer is treated as a mismatch signal.
    Does not fire when customer_id is None (already flagged as UNKNOWN_CUSTOMER)
    or when sender is absent (already flagged as MISSING_SENDER).
    """
    if bv.customer_id is None or not email.sender:
        return []
    suggestion = suggest_customer_from_domain(email.sender)
    # suggestion is None  → domain not in domain_map → unrecognised sender
    # suggestion differs  → domain maps to a different customer
    if suggestion is None or suggestion != bv.customer_id:
        return [_flag("SENDER_CUSTOMER_MISMATCH", field="customer_id",
                      raw=email.sender, suggestion=suggestion)]
    return []


def _rule_required_fields(
    bv: BookingValidation,
    email: ParsedEmail,
) -> list[ValidationFlag]:
    """Collect missing required fields and emit a single MISSING_REQUIRED flag.

    Required fields: pol, pod, quantity, requested_date.
    cargo_description is required only when body is empty (EMPTY_BODY) or
    cargo details are in an attachment (has_attachment_ref).
    customer_id is listed when unresolved (already flagged as UNKNOWN_CUSTOMER).

    Ports already flagged as AMBIGUOUS_PORT are not added to missing_fields
    (the ambiguity flag is the diagnosis; the field absence is a consequence).
    """
    missing: list[str] = []

    if bv.customer_id is None:
        missing.append("customer_id")

    if bv.pol is None:
        pol_codes = {f.code for f in bv.flags if f.field == "pol"}
        if "AMBIGUOUS_PORT" not in pol_codes:
            missing.append("pol")

    if bv.pod is None:
        pod_codes = {f.code for f in bv.flags if f.field == "pod"}
        if "AMBIGUOUS_PORT" not in pod_codes:
            missing.append("pod")

    # cargo_description: required only when it's provably absent
    if bv.cargo_description is None:
        if "EMPTY_BODY" in email.flags or email.has_attachment_ref:
            missing.append("cargo_description")

    if bv.quantity is None:
        missing.append("quantity")

    # requested_date: None covers INVALID/AMBIGUOUS/DATE_IN_PAST and genuine absence
    if bv.requested_date is None:
        missing.append("requested_date")

    if not missing:
        return []

    bv.missing_fields = missing
    return [_flag("MISSING_REQUIRED", field=None, raw=", ".join(missing))]


def _rule_email_warnings(email: ParsedEmail) -> list[ValidationFlag]:
    """Emit EMPTY_BODY and MISSING_SENDER (warning-only flags from the preprocessor)."""
    flags: list[ValidationFlag] = []
    if "EMPTY_BODY" in email.flags:
        flags.append(_flag("EMPTY_BODY", field=None))
    if "MISSING_SENDER" in email.flags:
        flags.append(_flag("MISSING_SENDER", field=None))
    return flags


# ---------------------------------------------------------------------------
# Per-booking orchestrator
# ---------------------------------------------------------------------------


def _validate_booking(
    idx: int,
    extracted: ExtractedBooking,
    email: ParsedEmail,
) -> BookingValidation:
    """Apply all rules to one extracted booking split; return BookingValidation."""
    bv = BookingValidation(
        split_index=idx,
        customer_raw=extracted.customer,
        pol_raw=extracted.pol,
        pod_raw=extracted.pod,
        cargo_description=extracted.cargo_description,
        booking_ref=extracted.booking_ref,
        vessel=extracted.vessel,
        voyage=extracted.voyage,
        rate_text=extracted.rate_text,
        contact_name=extracted.contact_name,
        contact_email=extracted.contact_email,
        weight_raw=extracted.weight_raw,
        dims_raw=extracted.dims_raw,
        requested_date_raw=extracted.requested_date,
    )

    # Each rule is called sequentially and its flags are added to bv.flags
    # *immediately*, so later rules (especially _rule_required_fields) can
    # inspect flags set by earlier rules (e.g. AMBIGUOUS_PORT on bv.flags).
    seen_keys: set[tuple[str, str | None]] = set()

    def _add(new_flags: list[ValidationFlag]) -> None:
        for f in new_flags:
            key = (f.code, f.field)
            if key not in seen_keys:
                seen_keys.add(key)
                bv.flags.append(f)

    _add(_rule_customer(extracted, email, bv))   # 1. sets bv.customer_id
    _add(_rule_ports(extracted, email, bv))      # 2. sets bv.pol / bv.pod
    _add(_rule_vins(extracted, bv))              # 3. sets bv.vin_list
    _add(_rule_quantity(extracted, bv))          # 4. sets bv.quantity
    _add(_rule_date(extracted, email, bv))       # 5. sets bv.requested_date
    _add(_rule_weight(extracted, bv))            # 6. sets bv.weight_kg_per_unit
    _add(_rule_dims(extracted, bv))              # 7. sets bv.length/width/height_m
    _add(_rule_attachment(email))                # 8.
    _add(_rule_encoding(email))                  # 9.
    _add(_rule_suspicious(email))                # 10.
    _add(_rule_sender_mismatch(bv, email))       # 11. (needs bv.customer_id)
    _add(_rule_required_fields(bv, email))       # 12. (needs all prior flags)
    _add(_rule_email_warnings(email))            # warning-only preprocessor flags

    return bv


# ---------------------------------------------------------------------------
# Email-level flag collector
# ---------------------------------------------------------------------------


def _collect_email_flags(email: ParsedEmail) -> list[str]:
    """Gather all email-level risk flags from the preprocessor output."""
    flags: list[str] = []
    if email.encoding_warning:
        flags.append("ENCODING_WARNING")
    if email.suspicious_patterns:
        flags.append("SUSPICIOUS_CONTENT")
    if email.has_attachment_ref:
        flags.append("MISSING_ATTACHMENT")
    if "EMPTY_BODY" in email.flags:
        flags.append("EMPTY_BODY")
    if "MISSING_SENDER" in email.flags:
        flags.append("MISSING_SENDER")
    return flags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(
    llm_result: LLMResponse,
    email: ParsedEmail,
) -> ValidationResult:
    """Run all validation rules and return a :class:`ValidationResult`.

    Email-level flags (ENCODING_WARNING, SUSPICIOUS_CONTENT, etc.) are
    collected on the result regardless of intent, so they are never lost
    for UPDATE / CANCEL / NON_BOOKING emails.

    Parameters
    ----------
    llm_result:
        Structured output from the LLM parser.
    email:
        Preprocessed email (provides sender, flags, attachment ref, etc.).

    Returns
    -------
    ValidationResult
        ``bookings`` is empty for non-CREATE_BOOKING intents.
        ``email_flags`` is populated for all intents.
    """
    result = ValidationResult(
        source_email_id=llm_result.source_email_id,
        message_intent=llm_result.message_intent,
        email_flags=_collect_email_flags(email),
    )

    if llm_result.message_intent == "CREATE_BOOKING":
        for idx, extracted in enumerate(llm_result.bookings):
            bv = _validate_booking(idx, extracted, email)
            result.bookings.append(bv)

    return result
