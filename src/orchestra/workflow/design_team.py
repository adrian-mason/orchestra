"""Design Team composition and workflow steps (P1-03, DESIGN.md §2.2, §3.4).

The Design Team uses TeamMode.coordinate: the Architect decomposes the task
and delegates sub-tasks to dynamically loaded Specialists. Output is a
structured design document (final_design.md) written to session_state.

The callable factory for dynamic member loading (tag → specialist mapping,
resolve_design_members, create_member_factory) lives in orchestra.agents.factory
(P1-02). This module consumes that factory and adds workflow-level steps.

Gate 0 Constraints:
- AC-03: Session state via get_ss()/set_ss() only
- AC-05: Team must specify db= parameter
- AC-06: check_team_member_errors() on team verdicts
- AC-07: mode=TeamMode.coordinate (explicit, not boolean)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agno.workflow.types import StepInput, StepOutput

from orchestra.agents.factory import create_member_factory
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import check_team_member_errors

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb
    from agno.team import Team
    from orchestra.model_resolver import ModelsConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Design Team construction (AC-05, AC-07)
# ---------------------------------------------------------------------------


def create_design_team(
    db: SqliteDb,
    *,
    config: ModelsConfig | None = None,
    project: str | None = None,
) -> Team:
    """Create the Design Team with TeamMode.coordinate.

    AC-05: db= is required (workflow constructor must specify db).
    AC-07: mode=TeamMode.coordinate (explicit enum, not boolean).

    The team uses a callable factory from orchestra.agents.factory (P1-02)
    for dynamic member loading (Agno F7). The Architect coordinates,
    decomposing the task and delegating to dynamically loaded Specialists.

    Args:
        db: SqliteDb instance for persistence (AC-05).
        config: Optional ModelsConfig for model resolution.
        project: Optional project name for model overrides.

    Returns:
        Configured Agno Team instance.
    """
    from agno.models.anthropic import Claude
    from agno.team import Team
    from agno.team.mode import TeamMode

    return Team(
        name="Design Squad",
        model=Claude(id="claude-sonnet-4-6"),
        members=create_member_factory(config=config, project=project),
        mode=TeamMode.coordinate,
        db=db,
    )


# ---------------------------------------------------------------------------
# Workflow step functions
# ---------------------------------------------------------------------------


def persist_design_output(step_input: StepInput) -> StepOutput:
    """Persist the Design Team's output to session_state (DESIGN.md §2.4).

    Writes the design document to session_state["latest_design_content"]
    for downstream review loops and gates. AC-03 compliant.

    Raises:
        ValueError: If design output is empty or too short (<50 chars).
    """
    design_content = step_input.previous_step_content
    if not design_content or len(design_content.strip()) < 50:
        raise ValueError("Design team produced empty or too-short output")

    # AC-06: Check for team member errors before persisting
    check_team_member_errors(design_content)

    set_ss(step_input, "latest_design_content", design_content)
    logger.info("Design output persisted to session_state (%d chars)", len(design_content))
    return StepOutput(content=design_content)


def revise_design_from_feedback(step_input: StepInput) -> StepOutput:
    """Revise design based on review feedback (DESIGN.md §2.4).

    Called inside the design_review_loop when the gate check fails.
    previous_step_content contains the feedback from check_design_gate.
    The current design is read from session_state["latest_design_content"].

    The revised design replaces latest_design_content for the next review round.
    """
    feedback = step_input.previous_step_content
    current_design = get_ss(step_input, "latest_design_content", "")

    if not current_design:
        raise ValueError(
            "No design found in session_state['latest_design_content']. "
            "persist_design_output() must run before review loop."
        )

    # Build revision prompt combining current design + feedback
    revision_content = (
        f"## Design Revision Request\n\n"
        f"### Current Design\n{current_design}\n\n"
        f"### Review Feedback\n{feedback}\n\n"
        f"Please revise the design to address all feedback points."
    )

    # Note: In production, this step_input feeds into the design_team executor
    # which processes it. For now, we structure the input for the next step.
    set_ss(step_input, "latest_design_content", revision_content)
    return StepOutput(content=revision_content)
