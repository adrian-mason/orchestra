"""Design Team composition and workflow steps (P1-03, DESIGN.md §2.2, §3.4).

The Design Team uses TeamMode.coordinate: the Architect decomposes the task
and delegates sub-tasks to dynamically loaded Specialists. Output is a
structured design document (final_design.md) written to session_state.

The callable factory for dynamic member loading (tag-to-specialist mapping,
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
from orchestra.model_resolver import instantiate_model
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import check_team_member_errors, is_genuine_team_error

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

    In TeamMode.coordinate, the Team's ``model`` acts as the coordination
    leader -- it decomposes the task and delegates to members. The Architect
    is a member who contributes domain expertise (system design, component
    architecture), while the leader handles orchestration/synthesis. This
    is consistent with Agno's Team semantics where the leader model is
    distinct from the member agents.

    The team uses a callable factory from orchestra.agents.factory (P1-02)
    for dynamic member loading (Agno F7).

    Args:
        db: SqliteDb instance for persistence (AC-05).
        config: Optional ModelsConfig for model resolution.
        project: Optional project name for model overrides.

    Returns:
        Configured Agno Team instance.
    """
    from agno.team import Team
    from agno.team.mode import TeamMode

    # Team leader model uses instantiate_model() for provider-agnostic
    # resolution instead of direct Claude import (Gemini review feedback).
    leader_model = instantiate_model('claude-sonnet-4-6')

    return Team(
        name='Design Squad',
        model=leader_model,
        members=create_member_factory(config=config, project=project),
        mode=TeamMode.coordinate,
        db=db,
    )


# ---------------------------------------------------------------------------
# Workflow step functions
# ---------------------------------------------------------------------------


def persist_design_output(step_input: StepInput) -> StepOutput:
    """Persist the Design Team's output to session_state (DESIGN.md §2.4).

    Writes the design document to session_state['latest_design_content']
    for downstream review loops and gates. AC-03 compliant.

    Raises:
        ValueError: If design output is empty or too short (<50 chars).
        TeamMemberError: If genuine team member errors are detected (AC-06).
    """
    design_content = step_input.previous_step_content
    if not design_content or len(design_content.strip()) < 50:
        raise ValueError('Design team produced empty or too-short output')

    # AC-06: Check for team member errors before persisting.
    # Use raise_on_error=False and apply heuristic filtering to avoid
    # false positives on design content that legitimately discusses
    # errors (e.g., 'Error handling should use Result types').
    errors = check_team_member_errors(design_content, raise_on_error=False)
    if errors and is_genuine_team_error(errors):
        from orchestra.utils.team import TeamMemberError

        raise TeamMemberError(errors)

    set_ss(step_input, 'latest_design_content', design_content)
    logger.info('Design output persisted to session_state (%d chars)', len(design_content))
    return StepOutput(content=design_content)


def revise_design_from_feedback(step_input: StepInput) -> StepOutput:
    """Prepare a revision prompt from review feedback (DESIGN.md §2.4).

    Called inside the design review loop when the gate check fails.
    ``previous_step_content`` contains the feedback from the gate check.
    The current design is read from session_state['latest_design_content'].

    This function structures a revision prompt combining the current design
    and review feedback, then writes it to session_state. The **caller**
    (the loop orchestrator) is responsible for re-executing the Design Team
    with this input to produce the actual revised design -- this function
    does NOT execute the team itself.

    The revised prompt replaces ``latest_design_content`` so the next
    team execution step picks it up as input context.
    """
    feedback = step_input.previous_step_content
    current_design = get_ss(step_input, 'latest_design_content', '')

    if not current_design:
        raise ValueError(
            "No design found in session_state['latest_design_content']. "
            'persist_design_output() must run before review loop.'
        )

    # Build revision prompt combining current design + feedback.
    # This is input for the next Design Team execution, not the
    # revised design itself.
    revision_content = (
        f"## Design Revision Request\n\n"
        f"### Current Design\n{current_design}\n\n"
        f"### Review Feedback\n{feedback}\n\n"
        f"Please revise the design to address all feedback points."
    )

    set_ss(step_input, 'latest_design_content', revision_content)
    return StepOutput(content=revision_content)
