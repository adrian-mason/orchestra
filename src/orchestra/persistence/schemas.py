"""DDL definitions for Orchestra custom tables.

These tables live in events_db alongside Agno's auto-managed tables.
Each DDL string is idempotent (IF NOT EXISTS).
"""

# ── events table ──
# 16 event types: tool_start, tool_end, session_start, session_end,
# mail_sent, mail_received, spawn, error, gate_verdict, reaction_fired,
# merge_attempt, checkpoint, decision_gate_created, decision_gate_resolved,
# wu_blocked, wu_unblocked
#
# Each row carries a correlation_id for cross-system tracing.

EVENTS_TABLE_DDL = """\
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    agent_name TEXT,
    run_id TEXT,
    session_id TEXT,
    tool_name TEXT,
    level TEXT,
    correlation_id TEXT,
    content TEXT,
    created_at TEXT NOT NULL
);
"""

EVENTS_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_agent_time ON events(agent_name, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_run_time ON events(run_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_event_type_time ON events(event_type, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_tool_agent ON events(tool_name, agent_name);",
    "CREATE INDEX IF NOT EXISTS idx_error ON events(level) WHERE level='error';",
    "CREATE INDEX IF NOT EXISTS idx_decision_gate ON events(event_type, created_at) WHERE event_type LIKE 'decision_gate_%';",
    "CREATE INDEX IF NOT EXISTS idx_correlation ON events(correlation_id);",
]

# Full DDL sequence for events_db custom schema
EVENTS_SCHEMA_DDL = [EVENTS_TABLE_DDL] + EVENTS_INDEXES_DDL
