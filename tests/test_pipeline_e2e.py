"""End-to-end test: all case emails -> pipeline -> database, checked against the golden set.

The pipeline runs in a subprocess against a throw-away SQLite file and replays the
recorded LLM responses from ``cache/llm_cache.json`` (no Ollama needed).

The test is skipped when the confidential case data or the LLM cache is not
available (both are git-ignored), so a fresh clone still gets a green test run.

Only *safety* properties are asserted unconditionally: the 7B model is known to
deviate from the golden set on warning flags and on two correction emails (014, 015);
see ``docs/eval_results.md``. What must never deviate is that nothing is wrongly READY.

The *completeness* checks (every expected READY booking present, intent/source status
matching the golden set) are only meaningful when ``cache/llm_cache.json`` is a fresh
snapshot of the current prompt. They run with ``E2E_STRICT_GOLDEN=1``. Refresh the cache with
``uv run python scripts/run_pipeline.py --reuse-llm-cache --write-cache`` (needs Ollama).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "plan" / "expected_outcomes.json"
CACHE = ROOT / "cache" / "llm_cache.json"
EMAILS = ROOT / "data" / "emails"

pytestmark = pytest.mark.skipif(
    not (GOLDEN.exists() and CACHE.exists() and EMAILS.exists()),
    reason="case data, golden set or LLM cache not available (git-ignored)",
)

# Emails whose model output deviates from the golden set in a *safe* way.
KNOWN_DEVIATIONS = {"EMAIL-014", "EMAIL-015"}

strict_golden = pytest.mark.skipif(
    os.getenv("E2E_STRICT_GOLDEN") != "1",
    reason="needs a fresh LLM cache; set E2E_STRICT_GOLDEN=1 (see module docstring)",
)


@pytest.fixture(scope="module")
def run_db(tmp_path_factory) -> sqlite3.Connection:
    db_file = tmp_path_factory.mktemp("e2e") / "e2e.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), "--reuse-llm-cache"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    conn = sqlite3.connect(db_file)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["emails"]


def test_every_email_is_processed(run_db, golden):
    """A malformed email must not stop the loop: all emails end up in source_emails."""
    ids = {r[0] for r in run_db.execute("select email_id from source_emails")}
    assert ids == set(golden)


def test_no_email_ends_in_error_state(run_db):
    bad = run_db.execute(
        "select email_id, processing_status from source_emails "
        "where processing_status in ('ERROR', 'PARSE_FAILED', 'RECEIVED')"
    ).fetchall()
    assert bad == []


def _ready_sets(run_db, golden):
    expected = {
        eid
        for eid, g in golden.items()
        for b in g.get("bookings", [])
        if b.get("status") == "READY"
    }
    got = {
        r[0]
        for r in run_db.execute(
            "select source_email_id from bookings where processing_status='READY'"
        )
    }
    return expected, got


def test_no_false_positive_ready(run_db, golden):
    """Core safety rule: nothing is READY unless the golden set says so."""
    expected, got = _ready_sets(run_db, golden)
    assert got - expected == set(), "false positive READY bookings"


@strict_golden
def test_ready_set_matches_golden_exactly(run_db, golden):
    expected, got = _ready_sets(run_db, golden)
    assert got - expected == set(), "false positive READY bookings"
    assert expected - got == set(), "missing READY bookings"


@strict_golden
def test_intent_and_source_status_match_golden(run_db, golden):
    rows = {
        r[0]: (r[1], r[2])
        for r in run_db.execute(
            "select email_id, classified_intent, processing_status from source_emails"
        )
    }
    mismatches = {
        eid: (rows[eid], (g["intent"], g["source_status"]))
        for eid, g in golden.items()
        if eid not in KNOWN_DEVIATIONS
        and rows[eid] != (g["intent"], g["source_status"])
    }
    assert mismatches == {}


def test_known_deviations_are_never_ready(run_db):
    """014/015: the model treats corrections as new bookings; they must not become READY."""
    for eid in KNOWN_DEVIATIONS:
        statuses = [
            r[0]
            for r in run_db.execute(
                "select processing_status from bookings where source_email_id=?", (eid,)
            )
        ]
        assert "READY" not in statuses


def test_every_booking_is_traceable_to_its_source_email(run_db):
    orphans = run_db.execute(
        "select count(*) from bookings b left join source_emails s "
        "on b.source_email_id = s.email_id where s.email_id is null"
    ).fetchone()[0]
    assert orphans == 0


def test_blocking_flags_never_coexist_with_ready(run_db):
    """No READY booking may carry a blocking validation flag."""
    from ugr_booking.enums import BLOCKING_FLAGS

    rows = run_db.execute(
        "select source_email_id, validation_flags from bookings where processing_status='READY'"
    ).fetchall()
    for eid, flags_json in rows:
        flags = json.loads(flags_json) if flags_json else []
        codes = {f.get("code") for f in flags}
        assert not (codes & BLOCKING_FLAGS), f"{eid} is READY with {codes & BLOCKING_FLAGS}"
