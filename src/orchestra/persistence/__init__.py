"""Orchestra persistence layer (P0-02).

Provides 5 functionally-separated SQLite databases with WAL mode
for concurrent multi-agent access.

DB responsibility boundaries:
- traces_db:  Agno native OTel tracing (setup_tracing auto-manages)
- events_db:  Orchestra EventStore (16 event types + 7 indexes)
- mail_db:    Inter-agent async messaging
- metrics_db: Token/cost tracking
- merge_db:   Merge queue + conflict resolution history
"""

from orchestra.persistence.databases import (
    DatabaseSet,
    initialize_databases,
)
from orchestra.persistence.schemas import EVENTS_TABLE_DDL

__all__ = [
    "DatabaseSet",
    "EVENTS_TABLE_DDL",
    "initialize_databases",
]
