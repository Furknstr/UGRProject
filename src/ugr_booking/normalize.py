"""Text and value normalisation utilities.

S2: fold()
S6: parse_number(), parse_date(), parse_dims()
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date


# ---------------------------------------------------------------------------
# fold() — accent/case normalisation for matching
# ---------------------------------------------------------------------------

def fold(s: str | None) -> str:
    """Normalise a string for comparison:

    1. NFKD decomposition
    2. Strip combining (accent) marks
    3. Map dotless-i (ı → i) and dotted-I (İ → i via casefold)
    4. casefold  (handles İ→i correctly)
    5. Collapse whitespace

    Examples:
        fold("İZMİR")  == "izmir"
        fold("SÃGUNTO") == "sagunto"
        fold("JEDAH")   == "jedah"
    """
    if s is None:
        return ""
    # NFKD + strip combining marks
    nfkd = unicodedata.normalize("NFKD", s)
    no_marks = "".join(
        ch for ch in nfkd
        if unicodedata.category(ch) != "Mn"
    )
    # ı (U+0131, LATIN SMALL LETTER DOTLESS I) → i
    no_marks = no_marks.replace("\u0131", "i")
    # casefold handles İ (U+0130) after NFKD decomposition
    result = no_marks.casefold()
    # collapse whitespace
    return re.sub(r"\s+", " ", result).strip()


# ---------------------------------------------------------------------------
# _clean_raw() — shared pre-step: literal "null" → None, strip whitespace
# ---------------------------------------------------------------------------

def _clean_raw(raw: str | None) -> str | None:
    """Return None if raw is None, empty, or the literal string 'null'."""
    if raw is None:
        return None
    s = raw.strip()
    if s.lower() in ("null", "none", ""):
        return None
    return s


# ---------------------------------------------------------------------------
# parse_number() — multi-locale numeric string → float | None
# ---------------------------------------------------------------------------

_RE_SPACE_THOUSANDS = re.compile(r"^(\d{1,3}([ \u00a0]\d{3})+)$")
_RE_EU_MIXED        = re.compile(r"^(\d{1,3}(\.\d{3})+),(\d+)$")   # 7.330,50
_RE_EU_DECIMAL      = re.compile(r"^(\d+),(\d{1,2})$")              # 2,45  (no thousands)
_RE_US_THOUSANDS    = re.compile(r"^(\d{1,3}(,\d{3})+)(\.\d+)?$")  # 12,400 or 12,400.50


def parse_number(raw: str | None, *, context: str = "weight") -> float | None:
    """Parse a locale-ambiguous numeric string to float.

    Supported formats
    -----------------
    - ``"12,400"``     → 12400  (EN thousands separator, weight context)
    - ``"12,400.50"``  → 12400.5
    - ``"2.450"``      → 2.45   (EU decimal, dimension/width context)
    - ``"2,450"``      → 2.45   (EU decimal, dimension/width context with comma)
    - ``"7.330,50"``   → 7330.5 (EU thousands + EU decimal)
    - ``"7 450"``      → 7450   (space as thousands separator)
    - ``"1500"``       → 1500.0 (plain integer)
    - ``"null"``       → None

    The ``context`` parameter resolves the comma ambiguity for plain ``"N,NNN"`` values:
    - ``"weight"`` (default) → treat comma as thousands separator → 12400
    - ``"dim"``              → treat comma as decimal separator  → 2.45

    Returns
    -------
    float | None
    """
    s = _clean_raw(raw)
    if s is None:
        return None

    # Remove any trailing unit text (e.g. "12,400 kg", "2.450 m")
    s = re.sub(r"\s+[a-zA-Z%]+$", "", s).strip()

    # 1. European thousands + decimal: 7.330,50
    m = _RE_EU_MIXED.match(s)
    if m:
        integer_part = m.group(1).replace(".", "")
        decimal_part = m.group(3)
        return float(f"{integer_part}.{decimal_part}")

    # 2. Space (or NBSP) as thousands separator: 7 450
    if _RE_SPACE_THOUSANDS.match(s):
        return float(re.sub(r"[ \u00a0]", "", s))

    # 3. US thousands separator with optional decimal: 12,400  or  12,400.50
    m = _RE_US_THOUSANDS.match(s)
    if m:
        if context == "weight":
            # comma = thousands separator
            return float(s.replace(",", ""))
        # else fall through to EU decimal check below

    # 4. EU decimal only (no thousands): 2,45  →  2.45
    m = _RE_EU_DECIMAL.match(s)
    if m:
        return float(s.replace(",", "."))

    # 5. European thousands without decimal: 2.450 (dim context) or 7.330 (weight)
    #    Heuristic: if the part after the dot has exactly 3 digits, treat dot as thousands sep.
    m = re.match(r"^(\d{1,3})\.(\d{3})$", s)
    if m:
        if context == "dim":
            # e.g. 2.450 → 2.45 (drop trailing zero → but we keep it as decimal)
            # Actually "2.450" in dim context means 2.450 m = 2.45 m
            integer_part = m.group(1)
            decimal_part = m.group(2).rstrip("0") or "0"
            return float(f"{integer_part}.{decimal_part}")
        else:
            # weight context: 7.330 → 7330
            return float(m.group(1) + m.group(2))

    # 6. Plain float/int: 1500  or  1500.5
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# parse_date() — multi-format date string → ISO-8601 str | None | "INVALID_DATE"
# ---------------------------------------------------------------------------

# Ordered by specificity (most specific / longest first)
_DATE_FORMATS: list[tuple[str, str]] = [
    # DD/MM/YYYY  or  DD-MM-YYYY  or  DD.MM.YYYY  (4-digit year)
    (r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$", "dmy4"),
    # YYYY-MM-DD (ISO)
    (r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", "ymd4"),
    # DD/MM/YY  or  DD.MM.YY  (2-digit year)
    (r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})$", "dmy2"),
    # MM/DD/YY  (US, only triggered when month ≤ 12 AND day > 12)
    # — we don't auto-detect; keep same pattern but labelled differently
]

_SENTINEL = "INVALID_DATE"


def _expand_year(yy: int) -> int:
    """Convert 2-digit year: 00-49 → 2000-2049, 50-99 → 1950-1999."""
    return 2000 + yy if yy < 50 else 1900 + yy


def parse_date(raw: str | None) -> str | None:
    """Parse a date string (multiple locale formats) to ISO-8601 ``"YYYY-MM-DD"``.

    Returns
    -------
    ``"YYYY-MM-DD"``  — on success
    ``"INVALID_DATE"`` — when the string *looks* like a date but is impossible
                         (e.g. 31/02/2026)
    ``None``           — when raw is None, empty, or the literal "null"

    Examples
    --------
    >>> parse_date("27.11.26")
    '2026-11-27'
    >>> parse_date("31/02/2026")
    'INVALID_DATE'
    >>> parse_date("2026-09-14")
    '2026-09-14'
    >>> parse_date(None)
    None
    >>> parse_date("null")
    None
    """
    s = _clean_raw(raw)
    if s is None:
        return None

    for pattern, fmt in _DATE_FORMATS:
        m = re.match(pattern, s)
        if not m:
            continue
        try:
            if fmt == "dmy4":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif fmt == "ymd4":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif fmt == "dmy2":
                d, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                y = _expand_year(yy)
            else:
                continue
            # date() raises ValueError for impossible dates (e.g. Feb 31)
            return date(y, mo, d).isoformat()
        except ValueError:
            return _SENTINEL

    return _SENTINEL


# ---------------------------------------------------------------------------
# parse_dims() — dimension string → float (in metres) | None
# ---------------------------------------------------------------------------

_RE_DIMS_WITH_UNIT = re.compile(
    r"^(?P<value>[\d.,\s]+)\s*(?P<unit>cm|mm|m)\b",
    re.IGNORECASE,
)


def parse_dims(raw: str | None) -> float | None:
    """Parse a dimension string to **metres** (float).

    Supports cm and mm → m conversion.  Plain values with no unit are assumed
    to already be in metres.

    Examples
    --------
    >>> parse_dims("620 cm")
    6.2
    >>> parse_dims("6.20 m")
    6.2
    >>> parse_dims("6200 mm")
    6.2
    >>> parse_dims("null")
    None
    """
    s = _clean_raw(raw)
    if s is None:
        return None

    m = _RE_DIMS_WITH_UNIT.match(s)
    if m:
        value_str = m.group("value").strip()
        unit = m.group("unit").lower()
        value = parse_number(value_str, context="dim")
        if value is None:
            return None
        if unit == "cm":
            return round(value / 100, 10)
        if unit == "mm":
            return round(value / 1000, 10)
        return value  # metres already

    # No unit — try to parse as a plain number (assumed metres)
    return parse_number(s, context="dim")
