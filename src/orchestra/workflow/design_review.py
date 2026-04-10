"""Design Review Gate — broadcast-mode expert review (P1-05, DESIGN.md §4.4).

The Design Review Gate uses TeamMode.broadcast: 5+ domain experts
receive the design simultaneously and return independent verdicts.
This gate is the ONLY write point for session_state["approved_design"].

Reuses GateVerdict, parse_verdicts(), and format_feedback() from
orchestra.workflow.plan_review (P1-04) — same verdict model, different
verdict values (APPROVED/REJECTED vs PASS/FAIL).

Gate 0 Constraints:
- AC-01: Loop exit via end_condition callable, never StepOutput(stop=True)
- AC-02: Gate steps use on_error=OnError.fail
- AC-03: Session state via get_ss()/set_ss() only
- AC-04: Decision Gate AFTER loop, never inside
- AC-05: Team must specify db= parameter
- AC-06: check_team_member_errors() on team verdicts
- AC-07: mode=TeamMode.broadcast (explicit, not boolean)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from agno.workflow.types import StepInput, StepOutput

from orchestra.model_resolver import instantiate_model
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import check_team_member_errors
from orchestra.workflow.gate import create_decision_gate
from orchestra.workflow.plan_review import (
    GateVerdict,
    format_feedback,
    parse_verdicts,
)

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb

logger = logging.getLogger(__name__)


def _is_genuine_team_error(errors: list[str]) -> bool:
    """Filter check_team_member_errors results for genuine failures.

    Same heuristic as P1-03/P1-04. Bare mentions of 'Error' in expert
    feedback (e.g., "Error handling needs improvement") are legitimate
    and should not trigger AC-06 rejection.
    """
    for err_context in errors:
        lower = err_context.lower()
        if "traceback (most recent call last)" in lower:
            return True
        if "error occurred during execution" in lower:
            return True
        if "member" in lower and "failed" in lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Design Review Team construction (AC-05, AC-07)
# ---------------------------------------------------------------------------


def create_design_review_team(db: SqliteDb) -> Any:
    """Create the Design Review Gate team with TeamMode.broadcast.

    AC-05: db= is required.
    AC-07: mode=TeamMode.broadcast (explicit enum).

    5 domain expert reviewers from different providers:
    - Product Manager (Gemini) — requirements alignment
    - Architect (Claude Opus) — structural soundness
    - Security Expert (OpenAI) — threat modeling
    - UX Expert (Claude Haiku) — usability
    - QA Expert (Gemini) — testability

    Args:
        db: SqliteDb instance for persistence.

    Returns:
        Configured Agno Team instance.
    """
    from agno.agent import Agent
    from agno.team import Team
    from agno.team.mode import TeamMode

    verdict_instructions = (
        "Return your verdict as a JSON object:\n"
        '{"reviewer": "<your role>", "verdict": "APPROVED" or "REJECTED",\n'
        ' "reasoning": "...", "blockers": [...], "suggestions": [...]}'
    )

    return Team(
        name="Design Review Gate",
        model=instantiate_model("claude-sonnet-4-6"),
        members=[
            Agent(
                name="Product Manager",
                model=instantiate_model("gemini-pro"),
                instructions=(
                    "You are a Product Manager reviewing the design.\n"
                    "Evaluate: requirements coverage, user story alignment,\n"
                    "acceptance criteria completeness, and business value.\n\n"
                    + verdict_instructions
                ),
            ),
            Agent(
                name="Architect Reviewer",
                model=instantiate_model("claude-sonnet-4-6"),
                instructions=(
                    "You are an Architect reviewing the design.\n"
                    "Evaluate: structural soundness, component boundaries,\n"
                    "dependency management, scalability, and maintainability.\n\n"
                    + verdict_instructions
                ),
            ),
            Agent(
                name="Security Expert",
                model=instantiate_model("codex-gpt-5.3"),
                instructions=(
                    "You are a Security Expert reviewing the design.\n"
                    "Evaluate: threat model, authentication/authorization,\n"
                    "data protection, input validation, and OWASP compliance.\n\n"
                    + verdict_instructions
                ),
            ),
            Agent(
                name="UX Expert",
                model=instantiate_model("claude-haiku-4-5"),
                instructions=(
                    "You are a UX Expert reviewing the design.\n"
                    "Evaluate: user experience, accessibility, interaction\n"
                    "patterns, error messaging, and documentation quality.\n\n"
                    + verdict_instructions
                ),
            ),
            Agent(
                name="QA Expert",
                model=instantiate_model("gemini-pro"),
                instructions=(
                    "You are a QA Expert reviewing the design.\n"
                    "Evaluate: testability, edge case coverage, integration\n"
                    "test strategy, and observability hooks.\n\n"
                    + verdict_instructions
                ),
            ),
        ],
        mode=TeamMode.broadcast,
        db=db,
    )


# ---------------------------------------------------------------------------
# Gate check functions (workflow steps)
# ---------------------------------------------------------------------------


def check_design_gate(step_input: StepInput) -> StepOutput:
    """Check Design Review Gate — ALL must APPROVE (DESIGN.md §4.4).

    This is the ONLY write point for session_state["approved_design"].
    When all experts approve, the final design is written to
    session_state["approved_design"] for downstream consumption.

    AC-02: This step must be wrapped with on_error=OnError.fail.
    AC-03: Uses get_ss()/set_ss() for session state access.
    AC-06: Calls check_team_member_errors() on team output.
    """
    content = step_input.previous_step_content

    # AC-06: Check for team member errors with false-positive filtering
    errors = check_team_member_errors(content, raise_on_error=False)
    if errors and _is_genuine_team_error(errors):
        from orchestra.utils.team import TeamMemberError
        raise TeamMemberError(errors)

    verdicts = parse_verdicts(content)
    all_approved = all(v.verdict == "APPROVED" for v in verdicts)
    round_num = get_ss(step_input, "design_review_round", 0) + 1
    set_ss(step_input, "design_review_round", round_num)

    if all_approved:
        # Design gate passed — write approved_design to session_state.
        # This is the ONLY write point for approved_design (DESIGN.md §4.4).
        approved = get_ss(step_input, "latest_design_content", "")
        set_ss(step_input, "approved_design", approved)
        set_ss(step_input, "design_gate_passed", True)
        # "GATE_PASSED" is the signal string checked by design_review_approved().
        # We use content (not metadata) because Agno's StepOutput has no metadata field.
        return StepOutput(content="GATE_PASSED")

    # Not approved — store feedback for revision step
    set_ss(step_input, "design_gate_passed", False)
    set_ss(step_input, "design_gate_verdicts", [v.model_dump() for v in verdicts])
    return StepOutput(content=format_feedback(verdicts))


def design_review_approved(step_outputs: list[StepOutput]) -> bool:
    """End condition for design_review_loop (AC-01).

    Returns True when the gate check step output "GATE_PASSED".
    Never uses StepOutput(stop=True) — AC-01 compliant.
    """
    return any(
        isinstance(o.content, str) and o.content == "GATE_PASSED"
        for o in step_outputs
    )


def create_check_design_review_result(
    events_db: SqliteDb,
) -> Callable[[StepInput], StepOutput]:
    """Factory that returns a post-Loop step function with events_db bound.

    DecisionGate records must go to events_db (not traces_db) per
    DESIGN.md §4.5, §10.1. Same closure pattern as P1-04.

    Args:
        events_db: The events database for DecisionGate persistence.

    Returns:
        Step function with signature (StepInput) -> StepOutput.
    """

    def check_design_review_result(step_input: StepInput) -> StepOutput:
        """Post-Loop step: check if design review passed (AC-04).

        If passed, returns DESIGN_REVIEW_PASSED.
        If not passed, creates a DecisionGate for human escalation.
        """
        if get_ss(step_input, "design_gate_passed"):
            return StepOutput(content="DESIGN_REVIEW_PASSED")

        # Not passed after max iterations — create DecisionGate
        verdicts_data = get_ss(step_input, "design_gate_verdicts", [])
        verdicts = [GateVerdict(**v) for v in verdicts_data]

        workflow_run_id = getattr(
            step_input.workflow_session, "session_id", "unknown"
        )
        agent_id = "design_review_gate"

        gate = create_decision_gate(
            events_db,
            workflow_run_id=workflow_run_id,
            agent_id=agent_id,
            gate_type="design_review",
            context={
                "verdicts": [v.model_dump() for v in verdicts],
                "feedback": format_feedback(verdicts),
            },
        )
        set_ss(step_input, "pending_decision_gate_id", gate.id)
        logger.info("Created DecisionGate %s for design review escalation", gate.id)

        round_num = get_ss(step_input, "design_review_round", 0)
        return StepOutput(content=f"DESIGN_REVIEW_FAILED_AFTER_{round_num}_ROUNDS")

    return check_design_review_result


def revise_design_from_review(step_input: StepInput) -> StepOutput:
    """Prepare a revision prompt from design review feedback.

    Called inside the design review loop when experts reject.
    Structures a revision prompt combining the current design and
    review feedback. The **caller** (loop orchestrator) is responsible
    for re-executing the Design Team with this input.

    This function does NOT execute the team itself — it only prepares
    the revision input (same pattern as P1-03 revise_design_from_feedback
    and P1-04 revise_plan_from_feedback).
    """
    feedback = step_input.previous_step_content
    current_design = get_ss(step_input, "latest_design_content", "")

    if not current_design:
        raise ValueError(
            "No design found in session_state['latest_design_content']. "
            "Design team output must be persisted before design review."
        )

    round_num = get_ss(step_input, "design_review_round", 0)
    revision_content = (
        f"## Design Revision Request (Round {round_num})\n\n"
        f"### Current Design\n{current_design}\n\n"
        f"### Expert Review Feedback\n{feedback}\n\n"
        f"Please revise the design to address all blocking issues raised by the reviewers."
    )

    set_ss(step_input, "latest_design_content", revision_content)
    return StepOutput(content=revision_content)
