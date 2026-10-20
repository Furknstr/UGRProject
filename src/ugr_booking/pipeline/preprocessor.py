"""Email preprocessor — S3 (basic header parsing + chronological ordering).

Reads every email listed in data/manifest.json, parses the fixed header
block (Email-ID, From, To, Date, Subject) that precedes the
`--------` separator, and returns a list of :class:`ParsedEmail` objects
sorted by ``received_at``.

S7 will add quote-stripping, HTML cleanup, encoding detection, etc.
"""

from __future__ import annotations

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
    received_at: datetime                 # from manifest (authoritative)
    subject: str | None = None
    sender: str | None = None             

    body_raw: str = ""                    # everything after the separator line
    body_clean: str = ""                  # S7 will populate this further

    encoding_warning: bool = False        # set when replacement chars detected
    flags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SEPARATOR_RE = re.compile(r"^-{8,}\s*$")
_HEADER_RE = re.compile(r"^([A-Za-z-]+):\s*(.*)$")


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

    # Encoding warning: replacement character U+FFFD present anywhere
    encoding_warning = "\ufffd" in text

    flags: list[str] = []
    if encoding_warning:
        flags.append("ENCODING_WARNING")

    sender = headers.get("from") or None  # None when header is absent (026)

    return ParsedEmail(
        email_id=email_id,
        file_path=str(path),
        received_at=received_at,
        subject=headers.get("subject") or None,
        sender=sender,
        body_raw=body_raw,
        body_clean=body_raw,   # S7 will clean further
        encoding_warning=encoding_warning,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_emails(
    data_path: Path | None = None,
    manifest_file: str = "manifest.json",
) -> list[ParsedEmail]:
    """Load and return all emails sorted by ``received_at``.

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
        email_id  = entry["email_id"]
        file_path = root / entry["file"]
        received_at = datetime.strptime(entry["received_at"], "%Y-%m-%d %H:%M")

        parsed = _parse_file(file_path, email_id, received_at)
        emails.append(parsed)

    # Sort by the manifest-authoritative received_at (stable sort)
    emails.sort(key=lambda e: e.received_at)
    return emails
