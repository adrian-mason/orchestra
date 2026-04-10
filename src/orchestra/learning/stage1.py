"""Stage 1 extraction outputs — schema and persistence.

Implements DESIGN.md §9.4 Two-Stage Memory Consolidation (Phase 1).
Each workflow run produces one stage1_outputs row containing extracted
patterns (tool_patterns, file_hotspots, error_patterns, review_patterns,
timing).  Phase 2 consolidation reads pending rows and converts them
into KnowledgeEntry objects.

P0-09: stage1_outputs table schema + Pydantic model + CRUD
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agno.db.sqlite import SqliteDb
from sqlalchemy import text

from orchestra.persistence.databases import execute_ddl


# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class Stage1Payload(BaseModel):
    """Extracted patterns from a single workflow run.

    Five pattern categories per DESIGN.md §9.4.
    """

    tool_patterns: list[Any] | dict[str, Any] = Field(
        default_factory=list, description="Tool call combination patterns"
    )
    file_hotspots: list[Any] | dict[str, Any] = Field(
        default_factory=list, description="Files with repeated edits"
    )
    error_patterns: list[Any] | dict[str, Any] = Field(
        default_factory=list, description="Recurring error types"
    )
    review_patterns: list[Any] | dict[str, Any] = Field(
        default_factory=list, description="Repeated review feedback patterns"
    )
    timing: list[Any] | dict[str, Any] = Field(
        default_factory=list, description="Phase duration distribution"
    )


class Stage1Output(BaseModel):
    """A single stage1_outputs row — one per workflow run.

    Matches DESIGN.md §9.4 extraction schema.
    """

    run_id: str = Field(min_length=1, description="Workflow run_id (primary key)")
    extracted_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 extraction timestamp",
    )
    payload: Stage1Payload = Field(
        default_factory=Stage1Payload, description="Extracted pattern data"
    )
    consolidated: bool = Field(
        default=False, description="Whether Phase 2 has processed this row"
    )

    @field_validator("extracted_at")
    @classmethod
    def _validate_extracted_at(cls, v: str) -> str:
        datetime.fromisoformat(v)
        return v

    def to_row(self) -> dict[str, Any]:
        """Convert to a dict suitable for SQL INSERT."""
        return {
            "run_id": self.run_id,
            "extracted_at": self.extracted_at,
            "payload": self.payload.model_dump_json(),
            "consolidated": 1 if self.consolidated else 0,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Stage1Output:
        """Reconstruct from a database row (dict mapping)."""
        return cls(
            run_id=row["run_id"],
            extracted_at=row["extracted_at"],
            payload=Stage1Payload.model_validate_json(row["payload"]),
            consolidated=bool(row["consolidated"]),
        )


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

STAGE1_TABLE_DDL = """\
CREATE TABLE IF NOT EXISTS stage1_outputs (
    run_id TEXT PRIMARY KEY,
    extracted_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    consolidated INTEGER DEFAULT 0
);
"""

STAGE1_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_s1_consolidated ON stage1_outputs(consolidated);",
]

STAGE1_SCHEMA_DDL = [STAGE1_TABLE_DDL] + STAGE1_INDEXES_DDL


def ensure_stage1_table(db: SqliteDb) -> None:
    """Create stage1_outputs table + index if they don't exist.

    Uses P0-02's execute_ddl() for consistency with persistence layer.
    """
    execute_ddl(db, STAGE1_SCHEMA_DDL)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def save_stage1_output(db: SqliteDb, output: Stage1Output) -> None:
    """Insert a new stage1 extraction row.

    Raises IntegrityError if run_id already exists (one extraction per run).
    """
    row = output.to_row()
    with db.db_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO stage1_outputs (run_id, extracted_at, payload, consolidated) "
                "VALUES (:run_id, :extracted_at, :payload, :consolidated)"
            ),
            row,
        )
        conn.commit()


def get_stage1_output(db: SqliteDb, run_id: str) -> Stage1Output | None:
    """Fetch a stage1 output by run_id. Returns None if not found."""
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM stage1_outputs WHERE run_id = :run_id"),
            {"run_id": run_id},
        )
        row = result.mappings().fetchone()
        if row is None:
            return None
        return Stage1Output.from_row(dict(row))


def list_pending(db: SqliteDb) -> list[Stage1Output]:
    """List all stage1 outputs not yet consolidated (consolidated = 0).

    Used by Phase 2 consolidation to find work.
    """
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT * FROM stage1_outputs "
                "WHERE consolidated = 0 "
                "ORDER BY extracted_at"
            )
        )
        return [Stage1Output.from_row(dict(row)) for row in result.mappings()]


def mark_consolidated(db: SqliteDb, run_ids: list[str]) -> int:
    """Mark one or more stage1 outputs as consolidated.

    Returns the number of rows updated.
    """
    if not run_ids:
        return 0
    with db.db_engine.connect() as conn:
        # Use individual parameterized updates for SQLite compatibility
        total = 0
        for rid in run_ids:
            result = conn.execute(
                text(
                    "UPDATE stage1_outputs SET consolidated = 1 "
                    "WHERE run_id = :run_id AND consolidated = 0"
                ),
                {"run_id": rid},
            )
            total += result.rowcount
        conn.commit()
    return total


def count_pending(db: SqliteDb) -> int:
    """Count unconsolidated stage1 outputs.

    Used to check if consolidation threshold (N=5) has been reached.
    """
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT COUNT(*) FROM stage1_outputs WHERE consolidated = 0")
        )
        return result.scalar() or 0
