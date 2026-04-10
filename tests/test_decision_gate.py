"""Tests for Decision Gate model, workflow operations, and lifecycle.

Covers happy-path and failure-path scenarios per Challenger's requirement
that all submissions include failure-path evidence.
"""

from datetime import datetime, timezone

import pytest

from orchestra.models.decision_gate import DecisionGate, DecisionGateStatus
from orchestra.workflow.gate import (
    DECISION_GATES_SCHEMA_DDL,
    create_decision_gate,
    get_decision_gate,
    get_pending_gates,
    has_pending_decision_gate,
    initialize_decision_gates_schema,
    reap_expired_gates,
    resolve_decision_gate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_events_db(tmp_path):
    """Create a SqliteDb with decision_gates schema applied."""
    from agno.db.sqlite import SqliteDb

    db = SqliteDb(db_file=str(tmp_path / "events.db"))
    initialize_decision_gates_schema(db)
    return db


def _insert_gate(db, gate_id="dg-test", agent_id="agent-1",
                 workflow_run_id="wf-1", gate_type="plan_review",
                 status="pending", ttl_minutes=480,
                 created_at=None, context="{}"):
    """Insert a gate row directly for test setup."""
    from sqlalchemy import text

    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    with db.db_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO decision_gates "
                "(id, workflow_run_id, agent_id, gate_type, status, "
                "created_at, context, ttl_minutes) "
                "VALUES (:id, :wf, :agent, :gt, :status, :ca, :ctx, :ttl)"
            ),
            {
                "id": gate_id, "wf": workflow_run_id, "agent": agent_id,
                "gt": gate_type, "status": status, "ca": created_at,
                "ctx": context, "ttl": ttl_minutes,
            },
        )
        conn.commit()


# ---------------------------------------------------------------------------
# DecisionGate model
# ---------------------------------------------------------------------------

class TestDecisionGateModel:
    def test_valid_construction(self):
        gate = DecisionGate(
            id="dg-001",
            workflow_run_id="wf-1",
            agent_id="agent-1",
            gate_type="plan_review",
            created_at=datetime.now(timezone.utc),
        )
        assert gate.status == DecisionGateStatus.PENDING
        assert gate.ttl_minutes == 480
        assert gate.resolved_at is None
        assert gate.resolver is None
        assert gate.context == {}

    def test_all_gate_types(self):
        for gt in ("plan_review", "design_review", "integration",
                    "pr_review", "pr_merge"):
            gate = DecisionGate(
                id=f"dg-{gt}",
                workflow_run_id="wf-1",
                agent_id="agent-1",
                gate_type=gt,
                created_at=datetime.now(timezone.utc),
            )
            assert gate.gate_type == gt

    def test_invalid_gate_type_raises(self):
        with pytest.raises(Exception):
            DecisionGate(
                id="dg-bad",
                workflow_run_id="wf-1",
                agent_id="agent-1",
                gate_type="invalid_type",
                created_at=datetime.now(timezone.utc),
            )

    def test_all_statuses(self):
        assert set(DecisionGateStatus) == {
            DecisionGateStatus.PENDING,
            DecisionGateStatus.APPROVED,
            DecisionGateStatus.REJECTED,
            DecisionGateStatus.EXPIRED,
            DecisionGateStatus.OVERRIDE,
        }

    def test_to_row_serialization(self):
        now = datetime.now(timezone.utc)
        gate = DecisionGate(
            id="dg-ser",
            workflow_run_id="wf-1",
            agent_id="agent-1",
            gate_type="pr_merge",
            created_at=now,
            context={"reason": "3 rounds exceeded"},
        )
        row = gate.to_row()
        assert row["id"] == "dg-ser"
        assert row["status"] == "pending"
        assert row["created_at"] == now.isoformat()
        assert '"reason"' in row["context"]
        assert row["resolved_at"] is None

    def test_from_row_deserialization(self):
        now = datetime.now(timezone.utc)
        row = {
            "id": "dg-de",
            "workflow_run_id": "wf-1",
            "agent_id": "agent-1",
            "gate_type": "integration",
            "status": "approved",
            "created_at": now.isoformat(),
            "resolved_at": now.isoformat(),
            "resolver": "human-admin",
            "context": '{"blockers": ["test failure"]}',
            "ttl_minutes": 120,
        }
        gate = DecisionGate.from_row(row)
        assert gate.status == DecisionGateStatus.APPROVED
        assert gate.resolver == "human-admin"
        assert gate.context == {"blockers": ["test failure"]}
        assert gate.ttl_minutes == 120

    def test_roundtrip_to_row_from_row(self):
        now = datetime.now(timezone.utc)
        original = DecisionGate(
            id="dg-rt",
            workflow_run_id="wf-rt",
            agent_id="agent-rt",
            gate_type="design_review",
            status=DecisionGateStatus.REJECTED,
            created_at=now,
            resolved_at=now,
            resolver="admin",
            context={"rounds": 3, "verdict": "fail"},
            ttl_minutes=60,
        )
        row = original.to_row()
        restored = DecisionGate.from_row(row)
        assert restored.id == original.id
        assert restored.status == original.status
        assert restored.context == original.context
        assert restored.ttl_minutes == original.ttl_minutes

    def test_missing_required_id(self):
        with pytest.raises(Exception):
            DecisionGate(
                workflow_run_id="wf-1",
                agent_id="agent-1",
                gate_type="plan_review",
                created_at=datetime.now(timezone.utc),
            )  # type: ignore[call-arg]

    def test_missing_required_workflow_run_id(self):
        with pytest.raises(Exception):
            DecisionGate(
                id="dg-x",
                agent_id="agent-1",
                gate_type="plan_review",
                created_at=datetime.now(timezone.utc),
            )  # type: ignore[call-arg]

    def test_missing_required_agent_id(self):
        with pytest.raises(Exception):
            DecisionGate(
                id="dg-x",
                workflow_run_id="wf-1",
                gate_type="plan_review",
                created_at=datetime.now(timezone.utc),
            )  # type: ignore[call-arg]

    def test_missing_required_created_at(self):
        with pytest.raises(Exception):
            DecisionGate(
                id="dg-x",
                workflow_run_id="wf-1",
                agent_id="agent-1",
                gate_type="plan_review",
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

class TestSchemaInitialization:
    def test_creates_table(self, tmp_path):
        db = _make_events_db(tmp_path)
        from orchestra.persistence.databases import get_table_names
        tables = get_table_names(db)
        assert "decision_gates" in tables

    def test_idempotent(self, tmp_path):
        db = _make_events_db(tmp_path)
        # Apply again — should not raise
        initialize_decision_gates_schema(db)
        from orchestra.persistence.databases import get_table_names
        tables = get_table_names(db)
        assert "decision_gates" in tables

    def test_initialize_via_function(self, tmp_path):
        from agno.db.sqlite import SqliteDb
        db = SqliteDb(db_file=str(tmp_path / "test_init.db"))
        initialize_decision_gates_schema(db)
        from orchestra.persistence.databases import get_table_names
        assert "decision_gates" in get_table_names(db)


# ---------------------------------------------------------------------------
# create_decision_gate
# ---------------------------------------------------------------------------

class TestCreateDecisionGate:
    def test_creates_pending_gate(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db,
            workflow_run_id="wf-1",
            agent_id="agent-1",
            gate_type="plan_review",
        )
        assert gate.status == DecisionGateStatus.PENDING
        assert gate.id.startswith("dg-")
        assert gate.workflow_run_id == "wf-1"
        assert gate.agent_id == "agent-1"
        assert gate.gate_type == "plan_review"
        assert gate.ttl_minutes == 480

    def test_custom_ttl(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db,
            workflow_run_id="wf-1",
            agent_id="agent-1",
            gate_type="pr_merge",
            ttl_minutes=60,
        )
        assert gate.ttl_minutes == 60

    def test_with_context(self, tmp_path):
        db = _make_events_db(tmp_path)
        ctx = {"rounds": 3, "blockers": ["test_auth failed"]}
        gate = create_decision_gate(
            db,
            workflow_run_id="wf-1",
            agent_id="agent-1",
            gate_type="design_review",
            context=ctx,
        )
        assert gate.context == ctx

    def test_persisted_to_db(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db,
            workflow_run_id="wf-1",
            agent_id="agent-1",
            gate_type="integration",
        )
        fetched = get_decision_gate(db, gate.id)
        assert fetched is not None
        assert fetched.id == gate.id
        assert fetched.status == DecisionGateStatus.PENDING

    def test_invalid_gate_type_raises(self, tmp_path):
        db = _make_events_db(tmp_path)
        with pytest.raises(Exception):
            create_decision_gate(
                db,
                workflow_run_id="wf-1",
                agent_id="agent-1",
                gate_type="nonexistent",
            )

    def test_unique_ids(self, tmp_path):
        db = _make_events_db(tmp_path)
        g1 = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="a-1", gate_type="pr_review",
        )
        g2 = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="a-1", gate_type="pr_review",
        )
        assert g1.id != g2.id


# ---------------------------------------------------------------------------
# has_pending_decision_gate
# ---------------------------------------------------------------------------

class TestHasPendingDecisionGate:
    def test_no_gates(self, tmp_path):
        db = _make_events_db(tmp_path)
        assert has_pending_decision_gate(db, "agent-1") is False

    def test_with_pending_gate(self, tmp_path):
        db = _make_events_db(tmp_path)
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        assert has_pending_decision_gate(db, "agent-1") is True

    def test_resolved_gate_not_pending(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        resolve_decision_gate(db, gate.id, action=DecisionGateStatus.APPROVED,
                              resolver="admin")
        assert has_pending_decision_gate(db, "agent-1") is False

    def test_different_agent_not_found(self, tmp_path):
        db = _make_events_db(tmp_path)
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        assert has_pending_decision_gate(db, "agent-2") is False

    def test_workflow_run_id_match(self, tmp_path):
        db = _make_events_db(tmp_path)
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        # Different agent but same workflow — should match via workflow_run_id
        assert has_pending_decision_gate(
            db, "agent-2", workflow_run_id="wf-1"
        ) is True

    def test_workflow_run_id_no_match(self, tmp_path):
        db = _make_events_db(tmp_path)
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        assert has_pending_decision_gate(
            db, "agent-2", workflow_run_id="wf-other"
        ) is False


# ---------------------------------------------------------------------------
# get_decision_gate
# ---------------------------------------------------------------------------

class TestGetDecisionGate:
    def test_existing_gate(self, tmp_path):
        db = _make_events_db(tmp_path)
        created = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="pr_merge",
        )
        fetched = get_decision_gate(db, created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.gate_type == "pr_merge"

    def test_nonexistent_gate_returns_none(self, tmp_path):
        db = _make_events_db(tmp_path)
        assert get_decision_gate(db, "dg-nonexistent") is None


# ---------------------------------------------------------------------------
# get_pending_gates
# ---------------------------------------------------------------------------

class TestGetPendingGates:
    def test_empty(self, tmp_path):
        db = _make_events_db(tmp_path)
        assert get_pending_gates(db) == []

    def test_returns_only_pending(self, tmp_path):
        db = _make_events_db(tmp_path)
        g1 = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="a-1", gate_type="pr_review",
        )
        g2 = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="a-2", gate_type="pr_merge",
        )
        resolve_decision_gate(db, g1.id, action=DecisionGateStatus.APPROVED,
                              resolver="admin")
        pending = get_pending_gates(db)
        assert len(pending) == 1
        assert pending[0].id == g2.id

    def test_filter_by_agent(self, tmp_path):
        db = _make_events_db(tmp_path)
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="a-1", gate_type="pr_review",
        )
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="a-2", gate_type="pr_merge",
        )
        pending = get_pending_gates(db, agent_id="a-1")
        assert len(pending) == 1
        assert pending[0].agent_id == "a-1"


# ---------------------------------------------------------------------------
# resolve_decision_gate
# ---------------------------------------------------------------------------

class TestResolveDecisionGate:
    def test_approve(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        resolved = resolve_decision_gate(
            db, gate.id, action=DecisionGateStatus.APPROVED, resolver="admin",
        )
        assert resolved.status == DecisionGateStatus.APPROVED
        assert resolved.resolver == "admin"
        assert resolved.resolved_at is not None

    def test_reject(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="design_review",
        )
        resolved = resolve_decision_gate(
            db, gate.id, action=DecisionGateStatus.REJECTED, resolver="lead",
        )
        assert resolved.status == DecisionGateStatus.REJECTED

    def test_override(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="pr_merge",
        )
        resolved = resolve_decision_gate(
            db, gate.id, action=DecisionGateStatus.OVERRIDE, resolver="ops",
        )
        assert resolved.status == DecisionGateStatus.OVERRIDE

    def test_persisted_after_resolve(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="integration",
        )
        resolve_decision_gate(
            db, gate.id, action=DecisionGateStatus.APPROVED, resolver="admin",
        )
        fetched = get_decision_gate(db, gate.id)
        assert fetched is not None
        assert fetched.status == DecisionGateStatus.APPROVED
        assert fetched.resolver == "admin"
        assert fetched.resolved_at is not None

    def test_resolve_nonexistent_raises(self, tmp_path):
        db = _make_events_db(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            resolve_decision_gate(
                db, "dg-missing",
                action=DecisionGateStatus.APPROVED, resolver="admin",
            )

    def test_resolve_already_resolved_raises(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        resolve_decision_gate(
            db, gate.id, action=DecisionGateStatus.APPROVED, resolver="admin",
        )
        with pytest.raises(ValueError, match="already approved"):
            resolve_decision_gate(
                db, gate.id,
                action=DecisionGateStatus.REJECTED, resolver="other",
            )

    def test_resolve_with_pending_action_raises(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        with pytest.raises(ValueError, match="Invalid resolution action"):
            resolve_decision_gate(
                db, gate.id,
                action=DecisionGateStatus.PENDING, resolver="admin",
            )

    def test_resolve_with_expired_action_raises(self, tmp_path):
        db = _make_events_db(tmp_path)
        gate = create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review",
        )
        with pytest.raises(ValueError, match="Invalid resolution action"):
            resolve_decision_gate(
                db, gate.id,
                action=DecisionGateStatus.EXPIRED, resolver="admin",
            )


# ---------------------------------------------------------------------------
# reap_expired_gates
# ---------------------------------------------------------------------------

class TestReapExpiredGates:
    def test_no_expired(self, tmp_path):
        db = _make_events_db(tmp_path)
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="plan_review", ttl_minutes=480,
        )
        expired = reap_expired_gates(db)
        assert expired == []

    def test_expired_gate_reaped(self, tmp_path):
        db = _make_events_db(tmp_path)
        # Insert gate with created_at far in the past and tiny TTL
        _insert_gate(
            db, gate_id="dg-old", agent_id="agent-1",
            created_at="2020-01-01T00:00:00+00:00", ttl_minutes=1,
        )
        expired = reap_expired_gates(db)
        assert len(expired) == 1
        assert expired[0].id == "dg-old"
        assert expired[0].status == DecisionGateStatus.EXPIRED

    def test_expired_gate_persisted(self, tmp_path):
        db = _make_events_db(tmp_path)
        _insert_gate(
            db, gate_id="dg-old2", agent_id="agent-1",
            created_at="2020-01-01T00:00:00+00:00", ttl_minutes=1,
        )
        reap_expired_gates(db)
        fetched = get_decision_gate(db, "dg-old2")
        assert fetched is not None
        assert fetched.status == DecisionGateStatus.EXPIRED
        assert fetched.resolved_at is not None

    def test_already_resolved_not_reaped(self, tmp_path):
        db = _make_events_db(tmp_path)
        _insert_gate(
            db, gate_id="dg-resolved", agent_id="agent-1",
            created_at="2020-01-01T00:00:00+00:00", ttl_minutes=1,
            status="approved",
        )
        expired = reap_expired_gates(db)
        assert expired == []

    def test_mixed_expired_and_active(self, tmp_path):
        db = _make_events_db(tmp_path)
        # Old gate — should expire
        _insert_gate(
            db, gate_id="dg-old3", agent_id="agent-1",
            created_at="2020-01-01T00:00:00+00:00", ttl_minutes=1,
        )
        # Fresh gate — should not expire
        create_decision_gate(
            db, workflow_run_id="wf-1", agent_id="agent-1",
            gate_type="pr_review", ttl_minutes=480,
        )
        expired = reap_expired_gates(db)
        assert len(expired) == 1
        assert expired[0].id == "dg-old3"

    def test_empty_db(self, tmp_path):
        db = _make_events_db(tmp_path)
        assert reap_expired_gates(db) == []


# ---------------------------------------------------------------------------
# DDL constants
# ---------------------------------------------------------------------------

class TestDDLConstants:
    def test_schema_ddl_has_table_and_indexes(self):
        assert len(DECISION_GATES_SCHEMA_DDL) == 3  # 1 table + 2 indexes
        assert "CREATE TABLE" in DECISION_GATES_SCHEMA_DDL[0]
        assert "idx_dg_status" in DECISION_GATES_SCHEMA_DDL[1]
        assert "idx_dg_agent" in DECISION_GATES_SCHEMA_DDL[2]
