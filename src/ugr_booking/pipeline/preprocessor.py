"""Email preprocessor — S3 + S7.

S3: basic header parsing and chronological ordering.
S7: body cleaning (quote stripping, HTML, injection), risk flags
    (SUSPICIOUS_CONTENT, POSSIBLE_DUPLICATE).

Notes
-----
- ``body_raw``   : original file text after the separator — never modified.
- ``body_clean`` : result of the cleaning pipeline sent to the LLM.
- ``MISSING_ATTACHMENT`` is NOT set here; the preprocessor sets
  ``has_attachment_ref = True`` and the validator decides the flag.
- ``POSSIBLE_DUPLICATE`` is a hint only.  The validator re-confirms with
  resolved fields (customer, port, VINs).
"""

from __future__ import annotations

import hashlib
import html as _html_stdlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ugr_booking.config import DATA_PATH


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ParsedEmail:
    """Lightweight container for one preprocessed email."""

    email_id: str
    file_path: str
    received_at: datetime           # from manifest (authoritative)
    subject: str | None = None
    sender: str | None = None

    body_raw: str = ""              # everything after the separator line (untouched)
    body_clean: str = ""            # after S7 cleaning pipeline (sent to LLM)

    encoding_warning: bool = False  # replacement char U+FFFD detected
    body_fingerprint: str = ""      # SHA-256 of normalised body_clean (exact dup)

    # S7 semantic fields — used by the validator and guard
    has_quote: bool = False             # quoted reply block was present and stripped
    has_attachment_ref: bool = False    # body references a file / attachment
    suspicious_patterns: list[str] = field(default_factory=list)  # matched injection pattern labels

    flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal regex constants
# ---------------------------------------------------------------------------

_SEPARATOR_RE = re.compile(r"^-{8,}\s*$")
_HEADER_RE    = re.compile(r"^([A-Za-z-]+):\s*(.*)$")

# --- Quote stripping --------------------------------------------------------
_QUOTE_HEADER_RE = re.compile(
    r"\n?-{3,}\s*(Previous message|Original Message|Forwarded message)\s*-{3,}.*",
    re.IGNORECASE | re.DOTALL,
)

# --- HTML detection ---------------------------------------------------------
_HTML_TAG_RE     = re.compile(r"<[^>]+>")
_LOOKS_LIKE_HTML = re.compile(r"<(html|body|br|div|p|span|table)\b", re.IGNORECASE)

# --- Injection marker -------------------------------------------------------
_INJECTION_RE = re.compile(
    r"---\s*EXTERNAL TEXT START\s*---.*?---\s*EXTERNAL TEXT END\s*---",
    re.IGNORECASE | re.DOTALL,
)

# --- Generalised injection keyword patterns ---------------------------------
# Each tuple: (label, compiled pattern).  Checked against body_raw so the
# flag survives even after the marked block is removed from body_clean.
_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_validation",
     re.compile(r"ignore\s+(all|previous|any)\s+.*(rule|instruction|validation)", re.I | re.S)),
    ("system_note",
     re.compile(r"system\s+note\s*[:\-]", re.I)),
    ("you_are_now",
     re.compile(r"you\s+are\s+now\b", re.I)),
    ("disregard",
     re.compile(r"disregard\s+.*(above|previous)", re.I | re.S)),
    ("do_not_tell",
     re.compile(r"do\s+not\s+tell\s+the\s+user", re.I)),
    ("invent_value",
     re.compile(r"invent\s+a\s+realistic", re.I)),
    ("change_field",
     re.compile(r"change\s+(POD|POL|customer|date)\s+to\b", re.I)),
]

# --- Attachment reference ---------------------------------------------------
_ATTACHMENT_RE = re.compile(
    r"\b(attach(ed|ment)|excel|spreadsheet)\b",
    re.IGNORECASE,
)

# --- Structural duplicate fingerprint ---------------------------------------
_REF_VIN_RE = re.compile(r"\bBOOKINGREF-[A-Z0-9]+\b|\b[A-Z]{3,}VIN[A-Z0-9]+\b")


# ---------------------------------------------------------------------------
# Body cleaning helpers
# ---------------------------------------------------------------------------

def _strip_html(text: str) -> str:
    """Remove HTML tags; use stdlib html.unescape() for all entities."""
    text = _html_stdlib.unescape(text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_quotes(text: str) -> tuple[str, bool]:
    """Remove quoted-reply blocks; return (cleaned, had_quote)."""
    cleaned, n = _QUOTE_HEADER_RE.subn("", text)
    return cleaned.strip(), n > 0


def _strip_injection(text: str) -> tuple[str, bool]:
    """Remove marked injection blocks; return (cleaned, had_marker)."""
    cleaned, n = _INJECTION_RE.subn("", text)
    return cleaned.strip(), n > 0


def _body_fingerprint(text: str) -> str:
    """SHA-256 of whitespace-normalised body — exact duplicate detection."""
    normalised = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _structural_fingerprint(text: str) -> str:
    """SHA-256 of sorted booking-ref + VIN tokens — near-duplicate detection.

    Returns an empty string when no refs/VINs are found (e.g. NON_BOOKING
    emails), so those emails are never falsely flagged as duplicates.
    """
    tokens = sorted(set(t.upper() for t in _REF_VIN_RE.findall(text)))
    if not tokens:
        return ""
    return hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest()


def _detect_injection(text: str) -> list[str]:
    """Return labels of all injection patterns matched in *text*."""
    return [label for label, pat in _INJECTION_PATTERNS if pat.search(text)]


# ---------------------------------------------------------------------------
# Per-file parser
# ---------------------------------------------------------------------------

def _parse_file(path: Path, email_id: str, received_at: datetime) -> ParsedEmail:
    """Parse a single email file into a :class:`ParsedEmail`."""
    text = path.read_text(encoding="utf-8", errors="replace")

    # Split on the first separator line
    lines = text.splitlines()
    sep_idx: int | None = None
    for i, line in enumerate(lines):
        if _SEPARATOR_RE.match(line):
            sep_idx = i
            break

    header_lines = lines[:sep_idx] if sep_idx is not None else lines
    body_lines   = lines[sep_idx + 1:] if sep_idx is not None else []

    # Parse headers
    headers: dict[str, str] = {}
    for line in header_lines:
        m = _HEADER_RE.match(line)
        if m:
            headers[m.group(1).lower()] = m.group(2).strip()

    body_raw = "\n".join(body_lines)

    # ---- S7: body cleaning pipeline ----------------------------------------
    cleaned = body_raw

    # 1. HTML cleanup (before injection/quote stripping)
    if _LOOKS_LIKE_HTML.search(cleaned):
        cleaned = _strip_html(cleaned)

    # 2. Quote stripping
    cleaned, had_quote = _strip_quotes(cleaned)

    # 3. Injection marker removal
    cleaned, had_marker = _strip_injection(cleaned)

    # ---- Risk signals (checked against body_raw so signals survive removal) --
    encoding_warning   = "\ufffd" in text
    has_attachment_ref = bool(_ATTACHMENT_RE.search(body_raw))

    # Injection: explicit marker OR keyword match in original text
    matched_patterns = _detect_injection(body_raw)
    if had_marker and not matched_patterns:
        matched_patterns = ["injection_marker"]

    # ---- Flags ---------------------------------------------------------------
    flags: list[str] = []

    if encoding_warning:
        flags.append("ENCODING_WARNING")

    sender = headers.get("from") or None
    if sender is None:
        flags.append("MISSING_SENDER")

    if matched_patterns:
        flags.append("SUSPICIOUS_CONTENT")

    fingerprint = _body_fingerprint(cleaned)

    return ParsedEmail(
        email_id=email_id,
        file_path=str(path),
        received_at=received_at,
        subject=headers.get("subject") or None,
        sender=sender,
        body_raw=body_raw,
        body_clean=cleaned,
        encoding_warning=encoding_warning,
        body_fingerprint=fingerprint,
        has_quote=had_quote,
        has_attachment_ref=has_attachment_ref,
        suspicious_patterns=matched_patterns,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_emails(
    data_path: Path | None = None,
    manifest_file: str = "manifest.json",
) -> list[ParsedEmail]:
    """Load, clean, and return all emails sorted by ``received_at``.

    Duplicate detection is applied after all emails are loaded so that
    chronological order is established first.  Two fingerprints are compared:

    1. **Exact body hash** — byte-for-byte identical body_clean.
    2. **Structural hash** — same booking refs + VIN set, different prose
       (catches re-sends like EMAIL-012).

    The first occurrence keeps its flags unchanged; later matches receive
    ``POSSIBLE_DUPLICATE``.  This flag is a *hint* — the validator re-confirms
    with resolved fields before making a final decision.

    Parameters
    ----------
    data_path:
        Root data directory.  Defaults to :data:`ugr_booking.config.DATA_PATH`.
    manifest_file:
        Name of the JSON manifest relative to *data_path*.

    Returns
    -------
    list[ParsedEmail]
        All emails in ascending chronological order.
    """
    root = Path(data_path) if data_path else DATA_PATH
    manifest_path = root / manifest_file

    with open(manifest_path, encoding="utf-8") as fh:
        manifest: list[dict] = json.load(fh)

    emails: list[ParsedEmail] = []
    for entry in manifest:
        email_id    = entry["email_id"]
        file_path   = root / entry["file"]
        received_at = datetime.strptime(entry["received_at"], "%Y-%m-%d %H:%M")

        parsed = _parse_file(file_path, email_id, received_at)
        emails.append(parsed)

    emails.sort(key=lambda e: e.received_at)

    # ---- Duplicate detection -----------------------------------------------
    seen_exact: dict[str, str] = {}       # exact hash  -> first email_id
    seen_structural: dict[str, str] = {}  # struct hash -> first email_id

    for email in emails:
        exact_fp  = email.body_fingerprint
        struct_fp = _structural_fingerprint(email.body_clean)

        is_dup = (
            (exact_fp  and exact_fp  in seen_exact) or
            (struct_fp and struct_fp in seen_structural)
        )

        if is_dup:
            if "POSSIBLE_DUPLICATE" not in email.flags:
                email.flags.append("POSSIBLE_DUPLICATE")
        else:
            if exact_fp:
                seen_exact[exact_fp] = email.email_id
            if struct_fp:
                seen_structural[struct_fp] = email.email_id

    return emails
