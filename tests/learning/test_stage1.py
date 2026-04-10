"""Tests for orchestra.learning.stage1 — P0-09 stage1_outputs table.

Covers: Pydantic model validation, DDL creation, CRUD operations,
consolidation lifecycle, and failure paths.
"""

from __future__ import annotations

import pytest
from agno.db.sqlite import SqliteDb
from sqlalchemy import text

from orchestra.learning.stage1 import (
    Stage1Output,
    Stage1Payload,
    count_pending,
    ensure_stage1_table,
    get_stage1_output,
    list_pending,
    mark_consolidated,
    save_stage1_output,
    STAGE1_SCHEMA_DDL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    """Create a temporary SQLite database with stage1_outputs table."""
    db = SqliteDb(db_file=str(tmp_path / "test.db"))
    ensure_stage1_table(db)
    return db


def _make_output(**overrides) -> Stage1Output:
    """Create a Stage1Output with sensible defaults."""
    defaults = dict(
        run_id="run-001",
        extracted_at="2026-04-10T08:00:00+00:00",
        payload=Stage1Payload(
            tool_patterns=[{"combo": ["read", "edit"], "count": 5}],
            file_hotspots=[{"path": "src/main.py", "edits": 12}],
            error_patterns=[{"type": "ImportError", "count": 3}],
            review_patterns=[{"feedback": "missing tests", "count": 2}],
            timing=[{"phase": "implement", "seconds": 300}],
        ),
    )
    defaults.update(overrides)
    return Stage1Output(**defaults)


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------


class TestStage1Payload:
    def test_empty_payload(self):
        p = Stage1Payload()
        assert p.tool_patterns == []
        assert p.file_hotspots == []

    def test_full_payload(self):
        p = Stage1Payload(
            tool_patterns={"read_edit": 5},
            file_hotspots=[{"path": "x.py"}],
            error_patterns=[],
            review_patterns={"missing_tests": 2},
            timing={"implement": 300},
        )
        assert p.tool_patterns == {"read_edit": 5}
        assert isinstance(p.review_patterns, dict)

    def test_payload_json_roundtrip(self):
        p = Stage1Payload(
            tool_patterns=[1, 2, 3],
            file_hotspots={"a.py": 10},
        )
        raw = p.model_dump_json()
        restored = Stage1Payload.model_validate_json(raw)
        assert restored == p


class TestStage1Output:
    def test_minimal_output(self):
        o = _make_output()
        assert o.run_id == "run-001"
        assert o.consolidated is False

    def test_empty_run_id_rejected(self):
        with pytest.raises(Exception):
            Stage1Output(run_id="", extracted_at="2026-04-10T08:00:00+00:00")

    def test_invalid_extracted_at_rejected(self):
        with pytest.raises(Exception):
            _make_output(extracted_at="not-a-date")

    def test_valid_extracted_at(self):
        o = _make_output(extracted_at="2026-04-10T12:30:00Z")
        assert "2026-04-10" in o.extracted_at

    def test_default_extracted_at(self):
        o = Stage1Output(run_id="run-auto")
        assert o.extracted_at is not None
        assert len(o.extracted_at) > 10

    def test_consolidated_flag(self):
        o = _make_output(consolidated=True)
        assert o.consolidated is True

    def test_to_row(self):
        o = _make_output()
        row = o.to_row()
        assert row["run_id"] == "run-001"
        assert row["consolidated"] == 0
        assert isinstance(row["payload"], str)
        # payload is valid JSON
        import json
        parsed = json.loads(row["payload"])
        assert "tool_patterns" in parsed

    def test_to_row_consolidated(self):
        o = _make_output(consolidated=True)
        assert o.to_row()["consolidated"] == 1

    def test_from_row_roundtrip(self):
        o = _make_output()
        row = o.to_row()
        restored = Stage1Output.from_row(row)
        assert restored.run_id == o.run_id
        assert restored.payload.tool_patterns == o.payload.tool_patterns
        assert restored.consolidated == o.consolidated


# ---------------------------------------------------------------------------
# DDL / Table creation
# ---------------------------------------------------------------------------


class TestDDL:
    def test_table_created(self, db):
        with db.db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='stage1_outputs'")
            )
            assert result.fetchone() is not None

    def test_index_created(self, db):
        with db.db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_s1_consolidated'")
            )
            assert result.fetchone() is not None

    def test_idempotent_creation(self, db):
        """Calling ensure_stage1_table twice doesn't error."""
        ensure_stage1_table(db)
        with db.db_engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM sqlite_master WHERE name='stage1_outputs'")
            )
            assert result.scalar() == 1

    def test_schema_ddl_count(self):
        """DDL list has table + 1 index = 2 statements."""
        assert len(STAGE1_SCHEMA_DDL) == 2


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_save_and_get(self, db):
        o = _make_output()
        save_stage1_output(db, o)
        fetched = get_stage1_output(db, "run-001")
        assert fetched is not None
        assert fetched.run_id == "run-001"
        assert fetched.payload.tool_patterns == o.payload.tool_patterns

    def test_get_nonexistent(self, db):
        assert get_stage1_output(db, "nope") is None

    def test_duplicate_run_id_rejected(self, db):
        o = _make_output()
        save_stage1_output(db, o)
        with pytest.raises(Exception):
            save_stage1_output(db, o)

    def test_list_pending_empty(self, db):
        assert list_pending(db) == []

    def test_list_pending_returns_unconsolidated(self, db):
        save_stage1_output(db, _make_output(run_id="r1"))
        save_stage1_output(db, _make_output(run_id="r2"))
        save_stage1_output(db, _make_output(run_id="r3", consolidated=True))
        pending = list_pending(db)
        assert len(pending) == 2
        assert {p.run_id for p in pending} == {"r1", "r2"}

    def test_list_pending_ordered_by_extracted_at(self, db):
        save_stage1_output(db, _make_output(
            run_id="r-late", extracted_at="2026-04-10T12:00:00+00:00"
        ))
        save_stage1_output(db, _make_output(
            run_id="r-early", extracted_at="2026-04-10T08:00:00+00:00"
        ))
        pending = list_pending(db)
        assert pending[0].run_id == "r-early"
        assert pending[1].run_id == "r-late"

    def test_count_pending(self, db):
        assert count_pending(db) == 0
        save_stage1_output(db, _make_output(run_id="r1"))
        save_stage1_output(db, _make_output(run_id="r2"))
        assert count_pending(db) == 2

    def test_count_pending_excludes_consolidated(self, db):
        save_stage1_output(db, _make_output(run_id="r1"))
        save_stage1_output(db, _make_output(run_id="r2", consolidated=True))
        assert count_pending(db) == 1


# ---------------------------------------------------------------------------
# Consolidation lifecycle
# ---------------------------------------------------------------------------


class TestConsolidation:
    def test_mark_consolidated(self, db):
        save_stage1_output(db, _make_output(run_id="r1"))
        save_stage1_output(db, _make_output(run_id="r2"))
        updated = mark_consolidated(db, ["r1", "r2"])
        assert updated == 2
        assert count_pending(db) == 0

    def test_mark_consolidated_partial(self, db):
        save_stage1_output(db, _make_output(run_id="r1"))
        save_stage1_output(db, _make_output(run_id="r2"))
        updated = mark_consolidated(db, ["r1"])
        assert updated == 1
        assert count_pending(db) == 1

    def test_mark_consolidated_idempotent(self, db):
        save_stage1_output(db, _make_output(run_id="r1"))
        mark_consolidated(db, ["r1"])
        # Second mark returns 0 (already consolidated)
        updated = mark_consolidated(db, ["r1"])
        assert updated == 0

    def test_mark_consolidated_nonexistent(self, db):
        """Marking a nonexistent run_id returns 0, no error."""
        updated = mark_consolidated(db, ["nope"])
        assert updated == 0

    def test_mark_consolidated_empty_list(self, db):
        assert mark_consolidated(db, []) == 0

    def test_full_lifecycle(self, db):
        """Phase 1 extract → list pending → Phase 2 consolidate → verify."""
        # Phase 1: extract 5 runs
        for i in range(5):
            save_stage1_output(db, _make_output(
                run_id=f"run-{i:03d}",
                extracted_at=f"2026-04-10T{8+i:02d}:00:00+00:00",
            ))
        assert count_pending(db) == 5

        # Phase 2: consolidate
        pending = list_pending(db)
        assert len(pending) == 5
        run_ids = [p.run_id for p in pending]
        updated = mark_consolidated(db, run_ids)
        assert updated == 5
        assert count_pending(db) == 0
        assert list_pending(db) == []

        # Already-consolidated rows still retrievable individually
        fetched = get_stage1_output(db, "run-000")
        assert fetched is not None
        assert fetched.consolidated is True


# ---------------------------------------------------------------------------
# Payload preservation
# ---------------------------------------------------------------------------


class TestPayloadPreservation:
    def test_complex_payload_roundtrip(self, db):
        """Complex nested payload survives DB roundtrip."""
        payload = Stage1Payload(
            tool_patterns=[
                {"combo": ["read", "grep", "edit"], "frequency": 0.85},
                {"combo": ["bash", "read"], "frequency": 0.42},
            ],
            file_hotspots={"src/main.py": 12, "tests/test_main.py": 8},
            error_patterns=[{"type": "ValidationError", "count": 5, "files": ["a.py"]}],
            review_patterns={"missing_tests": 3, "style_issues": 1},
            timing={"planning": 60, "implement": 300, "test": 120, "review": 45},
        )
        o = Stage1Output(
            run_id="complex-run",
            extracted_at="2026-04-10T08:00:00+00:00",
            payload=payload,
        )
        save_stage1_output(db, o)
        fetched = get_stage1_output(db, "complex-run")
        assert fetched is not None
        assert fetched.payload.tool_patterns == payload.tool_patterns
        assert fetched.payload.file_hotspots == payload.file_hotspots
        assert fetched.payload.timing == payload.timing

    def test_empty_payload_roundtrip(self, db):
        """Empty payload (all defaults) survives DB roundtrip."""
        o = Stage1Output(
            run_id="empty-run",
            extracted_at="2026-04-10T08:00:00+00:00",
            payload=Stage1Payload(),
        )
        save_stage1_output(db, o)
        fetched = get_stage1_output(db, "empty-run")
        assert fetched is not None
        assert fetched.payload.tool_patterns == []
        assert fetched.payload.error_patterns == []
