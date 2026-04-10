"""Decision Gate persistence layer (P0-03).

Provides DDL for the decision_gates table and CRUD operations
against events_db via SQLAlchemy.
"""

from __future__ import annotations

from agno.db.sqlite import SqliteDb
from sqlalchemy import text

from orchestra.models.decision_gate import DecisionGate, DecisionGateStatus


# ── DDL ──

DECISION_GATES_TABLE_DDL = """\
CREATE TABLE IF NOT EXISTS decision_gates (
    id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    gate_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolver TEXT,
    context TEXT,
    ttl_minutes INTEGER DEFAULT 480
);
"""

DECISION_GATES_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_dg_status ON decision_gates(status, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_dg_agent ON decision_gates(agent_id, status);",
]

DECISION_GATES_SCHEMA_DDL = [DECISION_GATES_TABLE_DDL] + DECISION_GATES_INDEXES_DDL


def ensure_decision_gates_table(db: SqliteDb) -> None:
    """Create decision_gates table + indexes if they don't exist."""
    with db.db_engine.connect() as conn:
        for ddl in DECISION_GATES_SCHEMA_DDL:
            conn.execute(text(ddl))
        conn.commit()


# ── CRUD ──

def save_decision_gate(db: SqliteDb, gate: DecisionGate) -> None:
    """Insert a new Decision Gate into events_db."""
    row = gate.to_row()
    with db.db_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO decision_gates "
                "(id, workflow_run_id, agent_id, gate_type, status, "
                "created_at, resolved_at, resolver, context, ttl_minutes) "
                "VALUES (:id, :workflow_run_id, :agent_id, :gate_type, :status, "
                ":created_at, :resolved_at, :resolver, :context, :ttl_minutes)"
            ),
            row,
        )
        conn.commit()


def get_decision_gate(db: SqliteDb, gate_id: str) -> DecisionGate | None:
    """Fetch a Decision Gate by ID. Returns None if not found."""
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM decision_gates WHERE id = :id"),
            {"id": gate_id},
        )
        row = result.mappings().fetchone()
        if row is None:
            return None
        return DecisionGate.from_row(dict(row))


def update_decision_gate(db: SqliteDb, gate: DecisionGate) -> None:
    """Update an existing Decision Gate (status, resolved_at, resolver)."""
    row = gate.to_row()
    with db.db_engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE decision_gates SET "
                "status = :status, resolved_at = :resolved_at, "
                "resolver = :resolver, context = :context "
                "WHERE id = :id"
            ),
            row,
        )
        conn.commit()


def list_pending_gates(
    db: SqliteDb,
    *,
    agent_id: str | None = None,
    workflow_run_id: str | None = None,
) -> list[DecisionGate]:
    """List all pending Decision Gates, optionally filtered."""
    clauses = ["status = 'pending'"]
    params: dict[str, str] = {}

    if agent_id is not None:
        clauses.append("agent_id = :agent_id")
        params["agent_id"] = agent_id
    if workflow_run_id is not None:
        clauses.append("workflow_run_id = :workflow_run_id")
        params["workflow_run_id"] = workflow_run_id

    where = " AND ".join(clauses)
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT * FROM decision_gates WHERE {where} ORDER BY created_at"),
            params,
        )
        return [DecisionGate.from_row(dict(row)) for row in result.mappings()]


def has_pending_decision_gate(
    db: SqliteDb,
    agent_id: str,
    workflow_run_id: str | None = None,
) -> bool:
    """Check if an agent has any pending Decision Gates.

    Used by Watchdog (§7.2) and ActivityState (§8.2).
    """
    if workflow_run_id:
        with db.db_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM decision_gates "
                    "WHERE (agent_id = :agent_id OR workflow_run_id = :wfr) "
                    "AND status = 'pending'"
                ),
                {"agent_id": agent_id, "wfr": workflow_run_id},
            )
            return (result.scalar() or 0) > 0
    else:
        with db.db_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT COUNT(*) FROM decision_gates "
                    "WHERE agent_id = :agent_id AND status = 'pending'"
                ),
                {"agent_id": agent_id},
            )
            return (result.scalar() or 0) > 0


def find_expired_gates(db: SqliteDb) -> list[DecisionGate]:
    """Find all pending gates whose TTL has expired.

    Used by TTL Reaper (§4.6) in Watchdog monitor_loop.
    """
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT * FROM decision_gates "
                "WHERE status = 'pending' "
                "AND datetime(created_at, '+' || ttl_minutes || ' minutes') < datetime('now')"
            )
        )
        return [DecisionGate.from_row(dict(row)) for row in result.mappings()]
