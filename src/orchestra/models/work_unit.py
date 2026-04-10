"""WorkUnit data model — the atomic unit of work in Orchestra's DAG scheduler.

Matches DESIGN.md \u00a72.4 specification. Each WorkUnit represents an independent
implementation task with defined scope, dependencies, and completion criteria.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WorkUnit(BaseModel):
    """A single unit of work in the execution DAG.

    WorkUnits are decomposed from an approved design document and form a
    directed acyclic graph via the ``dependencies`` field. Independent units
    (no mutual dependencies) can execute in parallel; dependent units execute
    in topological order.
    """

    id: str = Field(description="Unique identifier, e.g. 'wu-001'")
    title: str = Field(description="Short descriptive title")
    description: str = Field(description="Detailed implementation instructions")
    dod: list[str] = Field(description="Definition of Done checklist items (must be non-empty)")
    file_scope: list[str] = Field(
        description="Glob patterns for affected files; must not overlap between units",
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="IDs of prerequisite WorkUnits (edges in the DAG)",
    )
    estimated_complexity: Literal["S", "M", "L"] = Field(
        description="Size estimate: S(mall), M(edium), L(arge)",
    )
    assigned_model: str | None = Field(
        default=None,
        description="Model ID populated at runtime by the routing strategy",
    )
