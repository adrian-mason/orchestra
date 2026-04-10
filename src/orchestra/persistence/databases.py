"""5-DB initialization and WAL mode management.

References:
- DESIGN.md §10.1: 5 DB functional separation (Overstory pattern)
- DESIGN.md §8.1: EventStore schema (16 event types + 7 indexes)
- AC-05: All Workflows must have db parameter
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agno.db.sqlite import SqliteDb
from sqlalchemy import text

from orchestra.persistence.schemas import EVENTS_SCHEMA_DDL


@dataclass(frozen=True)
class DatabaseSet:
    """Container for Orchestra's 5 functionally-separated databases.

    Each database has a distinct responsibility:
    - traces:  Agno OTel tracing (auto-managed by setup_tracing)
    - events:  Orchestra EventStore + custom tables (decision_gates, stage1_outputs)
    - mail:    Inter-agent async messaging
    - metrics: Token/cost tracking per run
    - merge:   Merge queue FIFO + conflict resolution history
    """

    traces: SqliteDb
    events: SqliteDb
    mail: SqliteDb
    metrics: SqliteDb
    merge: SqliteDb


def initialize_databases(
    base_dir: str | Path = ".orchestra",
    *,
    enable_wal: bool = True,
) -> DatabaseSet:
    """Initialize all 5 Orchestra databases.

    Creates the database files under ``base_dir/``, enables WAL mode, and
    applies custom schemas (events table + indexes).

    WAL semantics: WAL enables concurrent readers with a single writer.
    Overlapping writers may still raise ``database is locked`` errors
    (SQLite busy_timeout applies). Upper layers (EventStore, Decision Gate
    CRUD) are responsible for write-retry/backoff if needed.

    Args:
        base_dir: Root directory for all .db files. Defaults to ``.orchestra``.
            Created automatically if it doesn't exist.
        enable_wal: Whether to enable WAL journal mode. Defaults to True.
            Disable for in-memory or test scenarios where WAL is unsupported.

    Returns:
        A frozen DatabaseSet with all 5 initialized databases.
    """
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)

    traces_db = SqliteDb(db_file=str(base / "traces.db"))
    events_db = SqliteDb(db_file=str(base / "events.db"))
    mail_db = SqliteDb(db_file=str(base / "mail.db"))
    metrics_db = SqliteDb(db_file=str(base / "metrics.db"))
    merge_db = SqliteDb(db_file=str(base / "merge.db"))

    all_dbs = [traces_db, events_db, mail_db, metrics_db, merge_db]

    # Enable WAL mode: concurrent readers + single writer semantics
    if enable_wal:
        for db in all_dbs:
            _enable_wal(db)

    # Create Orchestra custom schema in events_db
    _apply_events_schema(events_db)

    return DatabaseSet(
        traces=traces_db,
        events=events_db,
        mail=mail_db,
        metrics=metrics_db,
        merge=merge_db,
    )


def _enable_wal(db: SqliteDb) -> None:
    """Enable WAL journal mode on a SqliteDb instance.

    WAL (Write-Ahead Logging) allows concurrent readers with a single writer.
    Overlapping writers will block until busy_timeout expires, then raise
    ``database is locked``. This is SQLite's fundamental limitation — upper
    layers must handle write contention if multiple agents write concurrently.
    """
    with db.db_engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()


def _apply_events_schema(db: SqliteDb) -> None:
    """Create the events table and indexes in events_db.

    All DDL uses IF NOT EXISTS for idempotency.
    """
    with db.db_engine.connect() as conn:
        for ddl in EVENTS_SCHEMA_DDL:
            conn.execute(text(ddl))
        conn.commit()


def get_journal_mode(db: SqliteDb) -> str:
    """Query the current journal mode of a database. Useful for verification."""
    with db.db_engine.connect() as conn:
        result = conn.execute(text("PRAGMA journal_mode"))
        return result.scalar() or "unknown"


def get_table_names(db: SqliteDb) -> list[str]:
    """List all user tables in a database. Useful for verification."""
    with db.db_engine.connect() as conn:
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        return [row[0] for row in result]


def execute_ddl(db: SqliteDb, ddl: str | list[str]) -> None:
    """Execute arbitrary DDL on a database.

    Used by downstream modules (P0-03, P0-09) to add their own tables
    to the appropriate database (e.g., decision_gates in events_db).

    Args:
        db: Target SqliteDb instance.
        ddl: Single DDL string or list of DDL strings to execute.
    """
    statements = [ddl] if isinstance(ddl, str) else ddl
    with db.db_engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()
