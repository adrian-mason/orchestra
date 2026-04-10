"""Tests for orchestra.persistence (P0-02).

Tests cover:
- 5 DB initialization
- WAL mode enablement
- Events table schema (16 event types + 7 indexes)
- DatabaseSet container
- Helper utilities (execute_ddl, get_table_names, get_journal_mode)
"""

import pytest

from sqlalchemy import text

from orchestra.persistence.databases import (
    DatabaseSet,
    _apply_events_schema,
    _enable_wal,
    execute_ddl,
    get_journal_mode,
    get_table_names,
    initialize_databases,
)
from orchestra.persistence.schemas import (
    EVENTS_INDEXES_DDL,
    EVENTS_SCHEMA_DDL,
    EVENTS_TABLE_DDL,
)


@pytest.fixture()
def db_dir(tmp_path):
    """Provide a temporary directory for database files."""
    return tmp_path / ".orchestra"


class TestInitializeDatabases:
    def test_creates_five_databases(self, db_dir):
        dbs = initialize_databases(db_dir)
        assert isinstance(dbs, DatabaseSet)
        assert dbs.traces is not None
        assert dbs.events is not None
        assert dbs.mail is not None
        assert dbs.metrics is not None
        assert dbs.merge is not None

    def test_creates_db_files(self, db_dir):
        initialize_databases(db_dir)
        expected = {"traces.db", "events.db", "mail.db", "metrics.db", "merge.db"}
        actual = {f.name for f in db_dir.iterdir() if f.suffix == ".db"}
        assert expected == actual

    def test_all_dbs_have_wal_mode(self, db_dir):
        dbs = initialize_databases(db_dir)
        for db in [dbs.traces, dbs.events, dbs.mail, dbs.metrics, dbs.merge]:
            assert get_journal_mode(db) == "wal"

    def test_wal_can_be_disabled(self, db_dir):
        dbs = initialize_databases(db_dir, enable_wal=False)
        # Without WAL, SQLite defaults to "delete" journal mode
        for db in [dbs.traces, dbs.events, dbs.mail, dbs.metrics, dbs.merge]:
            mode = get_journal_mode(db)
            assert mode != "wal", f"Expected non-WAL mode, got {mode}"

    def test_events_db_has_events_table(self, db_dir):
        dbs = initialize_databases(db_dir)
        tables = get_table_names(dbs.events)
        assert "events" in tables

    def test_other_dbs_have_no_custom_tables(self, db_dir):
        dbs = initialize_databases(db_dir)
        for db in [dbs.traces, dbs.mail, dbs.metrics, dbs.merge]:
            tables = get_table_names(db)
            assert "events" not in tables

    def test_idempotent_initialization(self, db_dir):
        dbs1 = initialize_databases(db_dir)
        # Insert a row to verify data survives re-init
        with dbs1.events.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO events (event_type, created_at) "
                    "VALUES ('test', '2026-01-01T00:00:00')"
                )
            )
            conn.commit()

        # Re-initialize — should not destroy existing data
        dbs2 = initialize_databases(db_dir)
        with dbs2.events.db_engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM events"))
            assert result.scalar() == 1

    def test_creates_missing_directory(self, tmp_path):
        deep_dir = tmp_path / "a" / "b" / "c" / ".orchestra"
        assert not deep_dir.exists()
        dbs = initialize_databases(deep_dir)
        assert deep_dir.exists()
        assert dbs.traces is not None

    def test_frozen_database_set(self, db_dir):
        dbs = initialize_databases(db_dir)
        with pytest.raises(AttributeError):
            dbs.traces = None  # type: ignore[misc]


class TestWalMode:
    def test_enable_wal_on_single_db(self, db_dir):
        from agno.db.sqlite import SqliteDb

        db = SqliteDb(db_file=str(db_dir / "test_wal.db"))
        _enable_wal(db)
        assert get_journal_mode(db) == "wal"

    def test_wal_is_idempotent(self, db_dir):
        from agno.db.sqlite import SqliteDb

        db = SqliteDb(db_file=str(db_dir / "test_wal2.db"))
        _enable_wal(db)
        _enable_wal(db)  # Second call should not fail
        assert get_journal_mode(db) == "wal"


class TestEventsSchema:
    def test_events_table_columns(self, db_dir):
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(events)"))
            columns = {row[1] for row in result}
        expected = {
            "id",
            "event_type",
            "agent_name",
            "run_id",
            "session_id",
            "tool_name",
            "level",
            "correlation_id",
            "content",
            "created_at",
        }
        assert expected == columns

    def test_events_table_has_seven_indexes(self, db_dir):
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='events' "
                    "ORDER BY name"
                )
            )
            indexes = [row[0] for row in result]
        expected_prefixes = [
            "idx_agent_time",
            "idx_correlation",
            "idx_decision_gate",
            "idx_error",
            "idx_event_type_time",
            "idx_run_time",
            "idx_tool_agent",
        ]
        assert len(indexes) == 7
        for prefix in expected_prefixes:
            assert any(idx.startswith(prefix) for idx in indexes), (
                f"Missing index: {prefix}"
            )

    def test_can_insert_all_16_event_types(self, db_dir):
        event_types = [
            "tool_start",
            "tool_end",
            "session_start",
            "session_end",
            "mail_sent",
            "mail_received",
            "spawn",
            "error",
            "gate_verdict",
            "reaction_fired",
            "merge_attempt",
            "checkpoint",
            "decision_gate_created",
            "decision_gate_resolved",
            "wu_blocked",
            "wu_unblocked",
        ]
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            for et in event_types:
                conn.execute(
                    text(
                        "INSERT INTO events (event_type, created_at) "
                        "VALUES (:et, :ts)"
                    ),
                    {"et": et, "ts": "2026-01-01T00:00:00"},
                )
            conn.commit()
            result = conn.execute(text("SELECT COUNT(*) FROM events"))
            assert result.scalar() == 16

    def test_event_type_not_null_enforced(self, db_dir):
        dbs = initialize_databases(db_dir)
        with pytest.raises(Exception):
            with dbs.events.db_engine.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO events (event_type, created_at) "
                        "VALUES (NULL, '2026-01-01T00:00:00')"
                    )
                )
                conn.commit()

    def test_created_at_not_null_enforced(self, db_dir):
        dbs = initialize_databases(db_dir)
        with pytest.raises(Exception):
            with dbs.events.db_engine.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO events (event_type, created_at) "
                        "VALUES ('test', NULL)"
                    )
                )
                conn.commit()

    def test_correlation_id_queryable(self, db_dir):
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO events (event_type, correlation_id, created_at) "
                    "VALUES ('spawn', 'cid-abc123', '2026-01-01T00:00:00')"
                )
            )
            conn.commit()
            result = conn.execute(
                text("SELECT event_type FROM events WHERE correlation_id = 'cid-abc123'")
            )
            assert result.scalar() == "spawn"

    def test_autoincrement_id(self, db_dir):
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO events (event_type, created_at) "
                    "VALUES ('spawn', '2026-01-01')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO events (event_type, created_at) "
                    "VALUES ('error', '2026-01-02')"
                )
            )
            conn.commit()
            result = conn.execute(text("SELECT id FROM events ORDER BY id"))
            ids = [row[0] for row in result]
            assert ids == [1, 2]


class TestExecuteDdl:
    def test_single_ddl_string(self, db_dir):
        dbs = initialize_databases(db_dir)
        execute_ddl(
            dbs.mail,
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, body TEXT);",
        )
        assert "messages" in get_table_names(dbs.mail)

    def test_ddl_list(self, db_dir):
        dbs = initialize_databases(db_dir)
        execute_ddl(
            dbs.merge,
            [
                "CREATE TABLE IF NOT EXISTS merge_queue (id TEXT PRIMARY KEY, status TEXT);",
                "CREATE INDEX IF NOT EXISTS idx_mq_status ON merge_queue(status);",
            ],
        )
        assert "merge_queue" in get_table_names(dbs.merge)

    def test_idempotent_ddl(self, db_dir):
        dbs = initialize_databases(db_dir)
        ddl = "CREATE TABLE IF NOT EXISTS test_tbl (id INTEGER PRIMARY KEY);"
        execute_ddl(dbs.metrics, ddl)
        execute_ddl(dbs.metrics, ddl)  # Should not fail
        assert "test_tbl" in get_table_names(dbs.metrics)


class TestSchemaConstants:
    def test_events_table_ddl_is_string(self):
        assert isinstance(EVENTS_TABLE_DDL, str)
        assert "CREATE TABLE" in EVENTS_TABLE_DDL

    def test_events_indexes_count(self):
        assert len(EVENTS_INDEXES_DDL) == 7

    def test_events_schema_ddl_total(self):
        # 1 table + 7 indexes = 8
        assert len(EVENTS_SCHEMA_DDL) == 8


class TestGetHelpers:
    def test_get_table_names_empty_db(self, db_dir):
        from agno.db.sqlite import SqliteDb

        db = SqliteDb(db_file=str(db_dir / "empty.db"))
        tables = get_table_names(db)
        assert tables == []

    def test_get_journal_mode_default(self, db_dir):
        from agno.db.sqlite import SqliteDb

        db = SqliteDb(db_file=str(db_dir / "default.db"))
        mode = get_journal_mode(db)
        assert mode in ("delete", "wal", "memory")


class TestWalConcurrencySemantics:
    """Document WAL's actual concurrency contract.

    WAL enables concurrent readers with a single writer. Overlapping writers
    may raise ``database is locked``. These tests codify the boundary behavior
    so upstream consumers (P0-03, Phase 1) know what to expect.
    """

    def test_concurrent_reads_succeed(self, db_dir):
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO events (event_type, created_at) "
                    "VALUES (:et, :ts)"
                ),
                {"et": "test", "ts": "2026-01-01"},
            )
            conn.commit()

        # Two concurrent readers should succeed
        with dbs.events.db_engine.connect() as r1, dbs.events.db_engine.connect() as r2:
            c1 = r1.execute(text("SELECT COUNT(*) FROM events")).scalar()
            c2 = r2.execute(text("SELECT COUNT(*) FROM events")).scalar()
            assert c1 == 1
            assert c2 == 1

    def test_sequential_writes_succeed(self, db_dir):
        dbs = initialize_databases(db_dir)
        with dbs.events.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO events (event_type, created_at) "
                    "VALUES (:et, :ts)"
                ),
                {"et": "write1", "ts": "2026-01-01"},
            )
            conn.commit()
        with dbs.events.db_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO events (event_type, created_at) "
                    "VALUES (:et, :ts)"
                ),
                {"et": "write2", "ts": "2026-01-02"},
            )
            conn.commit()
        with dbs.events.db_engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM events")).scalar() == 2

    def test_overlapping_writers_raise_lock_error(self, db_dir):
        """Overlapping writers may raise 'database is locked'.

        This is SQLite's fundamental limitation under WAL: concurrent readers
        are fine, but a second writer while another holds an uncommitted write
        transaction will block until busy_timeout expires, then raise
        OperationalError. Upper layers (P0-03, Phase 1) must handle this.
        """
        from sqlalchemy import create_engine

        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "contention.db"
        # Use raw engines with minimal busy_timeout to trigger lock quickly
        engine_a = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"timeout": 0.1},
        )
        engine_b = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"timeout": 0.1},
        )
        # Enable WAL + create table
        with engine_a.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS t "
                    "(id INTEGER PRIMARY KEY, v TEXT)"
                )
            )
            conn.commit()

        conn_a = engine_a.connect()
        conn_b = engine_b.connect()
        try:
            # Writer A starts a write transaction (not yet committed)
            conn_a.execute(text("INSERT INTO t (v) VALUES ('a')"))

            # Writer B attempts to write while A holds uncommitted transaction
            with pytest.raises(Exception, match="database is locked"):
                conn_b.execute(text("INSERT INTO t (v) VALUES ('b')"))
        finally:
            conn_a.close()
            conn_b.close()
            engine_a.dispose()
            engine_b.dispose()
