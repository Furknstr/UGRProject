"""eval_real_llm.py — diff DB results against the golden set.

Usage
-----
    uv run python scripts/eval_real_llm.py
    uv run python scripts/eval_real_llm.py --output report.md
    uv run python scripts/eval_real_llm.py --golden plan/expected_outcomes.json

Exit codes
----------
0   All emails match the golden set (intent + source_status + booking count +
    blocking_flags + missing_fields for every split).
1   One or more deviations found (see the diff table in the report).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ugr_booking.config import DATABASE_URL
from ugr_booking.models.orm import Base, Booking, SourceEmail


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BookingDiff:
    split_index: int
    field: str
    expected: object
    actual: object


@dataclass
class EmailDiff:
    email_id: str
    deviations: list[BookingDiff] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.deviations) == 0


# ---------------------------------------------------------------------------
# Golden set loading
# ---------------------------------------------------------------------------

def _load_golden(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["emails"]          # { "EMAIL-001": {...}, ... }


# ---------------------------------------------------------------------------
# DB reading
# ---------------------------------------------------------------------------

def _load_db(db_url: str) -> tuple[dict[str, SourceEmail], dict[str, list[Booking]]]:
    """Return (source_emails by email_id, bookings grouped by email_id)."""
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        sources = {r.email_id: r for r in session.query(SourceEmail).all()}
        bookings: dict[str, list[Booking]] = {}
        for b in session.query(Booking).all():
            bookings.setdefault(b.source_email_id, []).append(b)
        for lst in bookings.values():
            lst.sort(key=lambda b: b.split_index)
    finally:
        session.close()
    return sources, bookings


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

def _norm_flags(flags: list | None) -> set[str]:
    return set(flags or [])


def _compare_email(
    email_id: str,
    golden: dict,
    source: SourceEmail | None,
    db_bookings: list[Booking],
) -> EmailDiff:
    diff = EmailDiff(email_id=email_id)

    # 1. Check if DB has this email at all
    if source is None:
        diff.deviations.append(BookingDiff(
            split_index=-1,
            field="source_row",
            expected="present",
            actual="MISSING",
        ))
        return diff

    # 2. intent (classified_intent)
    expected_intent = golden.get("intent")
    actual_intent = source.classified_intent
    if expected_intent and actual_intent != expected_intent:
        diff.deviations.append(BookingDiff(
            split_index=-1,
            field="intent",
            expected=expected_intent,
            actual=actual_intent,
        ))

    # 3. source_status
    expected_status = golden.get("source_status")
    actual_status = source.processing_status
    if expected_status and actual_status != expected_status:
        diff.deviations.append(BookingDiff(
            split_index=-1,
            field="source_status",
            expected=expected_status,
            actual=actual_status,
        ))

    # 4. booking count
    golden_bookings = golden.get("bookings", [])
    expected_count = len(golden_bookings)
    actual_count = len(db_bookings)
    if expected_count != actual_count:
        diff.deviations.append(BookingDiff(
            split_index=-1,
            field="booking_count",
            expected=expected_count,
            actual=actual_count,
        ))
        # Can't do split-level comparison when counts differ
        return diff

    # 5. Per-split comparison
    db_map = {b.split_index: b for b in db_bookings}
    for g in golden_bookings:
        idx: int = g.get("split_index", 0)
        db_b = db_map.get(idx)

        if db_b is None:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="booking_row",
                expected="present",
                actual="MISSING",
            ))
            continue

        actual_flags: list[dict] = db_b.validation_flags or []
        actual_blocking = {f["code"] for f in actual_flags if f.get("severity") == "blocking"}
        actual_warning  = {f["code"] for f in actual_flags if f.get("severity") == "warning"}

        exp_blocking = _norm_flags(g.get("blocking_flags"))
        exp_warning  = _norm_flags(g.get("warning_flags"))
        exp_missing  = set(g.get("missing_fields", []))
        # missing_fields is not a column; derive it from the MISSING_REQUIRED flag's raw value
        actual_missing: set[str] = set()
        for _f in actual_flags:
            if _f.get("code") == "MISSING_REQUIRED" and _f.get("raw"):
                actual_missing = {s.strip() for s in _f["raw"].split(",") if s.strip()}
                break

        # booking status (READY / VALIDATION_REQUIRED)
        exp_bstatus = g.get("status")
        act_bstatus = db_b.processing_status
        # CANCELLED is a terminal override — only compare READY/VALIDATION_REQUIRED
        if exp_bstatus and act_bstatus not in ("CANCELLED",) and act_bstatus != exp_bstatus:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="booking_status",
                expected=exp_bstatus,
                actual=act_bstatus,
            ))

        # blocking flags — missing flags are the important ones
        missing_blocking = exp_blocking - actual_blocking
        extra_blocking   = actual_blocking - exp_blocking
        if missing_blocking:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="missing_blocking_flags",
                expected=sorted(missing_blocking),
                actual="not raised",
            ))
        if extra_blocking:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="extra_blocking_flags",
                expected="not expected",
                actual=sorted(extra_blocking),
            ))

        # warning flags
        missing_warn = exp_warning - actual_warning
        extra_warn   = actual_warning - exp_warning
        if missing_warn:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="missing_warning_flags",
                expected=sorted(missing_warn),
                actual="not raised",
            ))
        if extra_warn:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="extra_warning_flags",
                expected="not expected",
                actual=sorted(extra_warn),
            ))

        # missing_fields
        if exp_missing != actual_missing:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="missing_fields",
                expected=sorted(exp_missing),
                actual=sorted(actual_missing),
            ))

        # resolved ports
        for fld in ("pol", "pod"):
            exp_val = g.get(fld)
            act_val = getattr(db_b, fld, None)
            if exp_val is not None and act_val != exp_val:
                diff.deviations.append(BookingDiff(
                    split_index=idx,
                    field=fld,
                    expected=exp_val,
                    actual=act_val,
                ))

        # quantity
        exp_qty = g.get("quantity")
        if exp_qty is not None and db_b.quantity != exp_qty:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="quantity",
                expected=exp_qty,
                actual=db_b.quantity,
            ))

        # requested_date
        exp_date = g.get("requested_date")
        if exp_date is not None and db_b.requested_date != exp_date:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="requested_date",
                expected=exp_date,
                actual=db_b.requested_date,
            ))

        # customer_id
        exp_cust = g.get("customer_id")
        if exp_cust is not None and db_b.customer_id != exp_cust:
            diff.deviations.append(BookingDiff(
                split_index=idx,
                field="customer_id",
                expected=exp_cust,
                actual=db_b.customer_id,
            ))

    return diff


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _render_markdown(diffs: list[EmailDiff], total: int) -> str:
    ok_count    = sum(1 for d in diffs if d.ok)
    fail_count  = total - ok_count
    match_pct   = round(ok_count / total * 100, 1) if total else 0

    lines: list[str] = [
        "# UGR Booking — LLM Evaluation Report",
        "",
        f"**Total emails evaluated:** {total}  ",
        f"**Matching golden set:** {ok_count} / {total} ({match_pct}%)  ",
        f"**Deviations found in:** {fail_count} email(s)",
        "",
    ]

    # Summary table
    lines += [
        "## Summary",
        "",
        "| Email | Result | Deviations |",
        "|---|---|---|",
    ]
    for d in sorted(diffs, key=lambda x: x.email_id):
        status = "✅ MATCH" if d.ok else "❌ DIFF"
        dev_str = "; ".join(
            f"split={dv.split_index} {dv.field}: {dv.expected!r}→{dv.actual!r}"
            for dv in d.deviations[:3]          # first 3 to keep table narrow
        )
        if len(d.deviations) > 3:
            dev_str += f" (+{len(d.deviations)-3} more)"
        lines.append(f"| {d.email_id} | {status} | {dev_str} |")

    lines.append("")

    # Detail section — only failing emails
    failing = [d for d in diffs if not d.ok]
    if failing:
        lines += ["## Deviation Detail", ""]
        for d in sorted(failing, key=lambda x: x.email_id):
            lines += [f"### {d.email_id}", ""]
            lines += [
                "| split | field | expected | actual |",
                "|---|---|---|---|",
            ]
            for dv in d.deviations:
                lines.append(
                    f"| {dv.split_index} | `{dv.field}` "
                    f"| `{dv.expected}` | `{dv.actual}` |"
                )
            lines.append("")

    lines += [
        "## Legend",
        "",
        "- **missing_blocking_flags**: golden set expects this flag but validator did not raise it",
        "- **extra_blocking_flags**: validator raised this flag but golden set does not expect it",
        "- **source_status**: SourceEmail.processing_status mismatch",
        "- **intent**: classified_intent mismatch",
        "",
        "> Generated by `scripts/eval_real_llm.py`",
        "",
    ]
    return "\n".join(lines)


def _render_console(diffs: list[EmailDiff], total: int) -> None:
    ok = sum(1 for d in diffs if d.ok)
    print(f"\n{'='*60}")
    print(f"  Golden-set evaluation: {ok}/{total} match")
    print(f"{'='*60}")
    for d in sorted(diffs, key=lambda x: x.email_id):
        marker = "[OK]" if d.ok else "[X]"
        print(f"  {marker} {d.email_id}")
        for dv in d.deviations:
            print(f"      split={dv.split_index}  {dv.field}: {dv.expected!r} -> {dv.actual!r}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Diff DB results against the golden set")
    ap.add_argument(
        "--golden",
        default="plan/expected_outcomes.json",
        metavar="PATH",
        help="Path to expected_outcomes.json (default: plan/expected_outcomes.json)",
    )
    ap.add_argument(
        "--db-url",
        default=DATABASE_URL,
        metavar="URL",
        help=f"SQLAlchemy DB URL (default: {DATABASE_URL})",
    )
    ap.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Write Markdown report to this file (default: print to stdout)",
    )
    args = ap.parse_args()

    golden = _load_golden(Path(args.golden))
    sources, bookings = _load_db(args.db_url)

    diffs: list[EmailDiff] = []
    for email_id, g in golden.items():
        diff = _compare_email(
            email_id=email_id,
            golden=g,
            source=sources.get(email_id),
            db_bookings=bookings.get(email_id, []),
        )
        diffs.append(diff)

    total = len(golden)
    _render_console(diffs, total)

    report_md = _render_markdown(diffs, total)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_md, encoding="utf-8")
        print(f"Report written to {out_path}")
    else:
        print(report_md)

    # Exit 1 if any deviations
    if any(not d.ok for d in diffs):
        sys.exit(1)


if __name__ == "__main__":
    main()
