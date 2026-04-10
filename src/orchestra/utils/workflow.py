"""Workflow factory with enforced db parameter (AC-05).

Agno treats the db parameter as optional. Without it, Workflow executes
normally but resume() silently fails — no checkpoint is persisted, no error
is raised. This is a silent failure mode identified in Gate 0.

This wrapper ensures every Workflow has a db configured.
"""

from __future__ import annotations

from typing import Any

from agno.workflow import Workflow


def create_workflow(
    name: str,
    *,
    db: Any,
    **kwargs: Any,
) -> Workflow:
    """Create a Workflow with enforced db parameter (AC-05).

    The db parameter is required (not optional). Callers must explicitly
    provide a BaseDb implementation. P0-02 will provide Orchestra's
    standard DB backends; for tests use agno.db.InMemoryDb or equivalent.

    Args:
        name: Workflow name.
        db: Database backend (required). Must be a BaseDb subclass instance.
        **kwargs: Additional Workflow constructor arguments.

    Returns:
        Configured Workflow instance.

    Raises:
        TypeError: If db is None (caught at call site by required kwarg).
    """
    if db is None:
        raise ValueError(
            f"Workflow '{name}' requires a db parameter (AC-05). "
            "Without db, resume/checkpoint semantics silently fail. "
            "Use InMemoryDb() for tests or SqliteDb() for production."
        )

    return Workflow(name=name, db=db, **kwargs)
