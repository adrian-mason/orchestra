"""Plan Review Gate — broadcast-mode adversarial review (P1-04, DESIGN.md §4.3).

The Plan Review Gate uses TeamMode.broadcast: all 3 adversarial critics
receive the plan simultaneously and return independent verdicts.

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

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, Field

from agno.workflow.types import StepInput, StepOutput

from orchestra.model_resolver import instantiate_model
from orchestra.utils.session import get_ss, set_ss
from orchestra.utils.team import check_team_member_errors
from orchestra.workflow.gate import create_decision_gate

if TYPE_CHECKING:
    from agno.db.sqlite import SqliteDb


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GateVerdict model
# ---------------------------------------------------------------------------


class GateVerdict(BaseModel):
    """A single reviewer's verdict on a plan or design.

    Each critic returns a JSON object with these fields. The verdict
    must be "PASS" or "FAIL" (plan review) or "APPROVED"/"REJECTED"
    (design review).
    """

    reviewer: str = Field(description="Name of the reviewing agent")
    verdict: str = Field(
        description="Verdict outcome",
        pattern=r"^(PASS|FAIL|APPROVED|NEEDS_REVISION|REJECTED)$",
    )
    reasoning: str = Field(default="", description="Explanation for the verdict")
    blockers: list[str] = Field(
        default_factory=list,
        description="Specific blocking issues (only when verdict is FAIL/REJECTED)",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Non-blocking improvement suggestions",
    )


# ---------------------------------------------------------------------------
# Verdict parsing
# ---------------------------------------------------------------------------

# Regex to find JSON blocks in LLM output (fenced or raw)
_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```|(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})",
    re.DOTALL,
)


def parse_verdicts(content: str) -> list[GateVerdict]:
    """Parse GateVerdict objects from team broadcast output.

    Handles multiple formats:
    - JSON code blocks (```json ... ```)
    - Raw JSON objects in text
    - Multiple verdicts from different reviewers

    Args:
        content: Raw text output from the broadcast team.

    Returns:
        List of parsed GateVerdict objects.

    Raises:
        ValueError: If no valid verdicts could be parsed.
    """
    if not content or not content.strip():
        raise ValueError("Empty content — no verdicts to parse")

    verdicts: list[GateVerdict] = []
    seen_reviewers: set[str] = set()

    for match in _JSON_BLOCK_RE.finditer(content):
        json_str = match.group(1) or match.group(2)
        if not json_str:
            continue
        try:
            data = json.loads(json_str.strip())
        except json.JSONDecodeError:
            continue

        # Handle both single verdict and array of verdicts
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if "verdict" not in item:
                continue
            try:
                verdict = GateVerdict(**item)
                # Deduplicate by reviewer name
                if verdict.reviewer not in seen_reviewers:
                    seen_reviewers.add(verdict.reviewer)
                    verdicts.append(verdict)
            except Exception:
                continue

    if not verdicts:
        raise ValueError(
            f"No valid GateVerdict found in content ({len(content)} chars). "
            "Expected JSON with at least a 'verdict' field."
        )

    return verdicts


def format_feedback(verdicts: list[GateVerdict]) -> str:
    """Format verdict feedback for the revision step.

    Produces a structured markdown summary of all non-passing verdicts
    with their blockers and suggestions.

    Args:
        verdicts: List of GateVerdict objects.

    Returns:
        Formatted feedback string for the design/plan revision step.
    """
    lines: list[str] = ["## Review Feedback\n"]

    for v in verdicts:
        status = "✅ PASS" if v.verdict == "PASS" else f"❌ {v.verdict}"
        lines.append(f"### {v.reviewer}: {status}")
        if v.reasoning:
            lines.append(f"\n{v.reasoning}\n")
        if v.blockers:
            lines.append("**Blockers:**")
            for b in v.blockers:
                lines.append(f"- {b}")
            lines.append("")
        if v.suggestions:
            lines.append("**Suggestions:**")
            for s in v.suggestions:
                lines.append(f"- {s}")
            lines.append("")

    passing = sum(1 for v in verdicts if v.verdict == "PASS")
    lines.append(f"\n**Summary:** {passing}/{len(verdicts)} critics passed.\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan Review Team construction (AC-05, AC-07)
# ---------------------------------------------------------------------------


def create_plan_review_team(db: SqliteDb) -> Any:
    """Create the Plan Review Gate team with TeamMode.broadcast.

    AC-05: db= is required.
    AC-07: mode=TeamMode.broadcast (explicit enum).

    3 adversarial critics from different providers:
    - Feasibility Critic (Gemini)
    - Completeness Critic (OpenAI/Codex)
    - Scope Critic (Claude Haiku)

    Args:
        db: SqliteDb instance for persistence.

    Returns:
        Configured Agno Team instance.
    """
    from agno.agent import Agent
    from agno.team import Team
    from agno.team.mode import TeamMode

    # All models use instantiate_model() for provider-agnostic resolution
    # instead of direct Claude/Gemini/OpenAI imports.
    return Team(
        name="Plan Review Gate",
        model=instantiate_model("claude-sonnet-4-6"),
        members=[
            Agent(
                name="Feasibility Critic",
                model=instantiate_model("gemini-pro"),
                instructions=(
                    "You are a Feasibility Critic. Evaluate the plan's feasibility.\n"
                    "Consider: technical complexity, resource requirements, timeline,\n"
                    "dependencies, and potential blockers.\n\n"
                    "Return your verdict as a JSON object:\n"
                    '{"reviewer": "Feasibility Critic", "verdict": "PASS" or "FAIL",\n'
                    ' "reasoning": "...", "blockers": [...], "suggestions": [...]}'
                ),
            ),
            Agent(
                name="Completeness Critic",
                model=instantiate_model("codex-gpt-5.3"),
                instructions=(
                    "You are a Completeness Critic. Evaluate the plan's completeness.\n"
                    "Consider: missing edge cases, error handling gaps, untested\n"
                    "assumptions, integration points, and documentation needs.\n\n"
                    "Return your verdict as a JSON object:\n"
                    '{"reviewer": "Completeness Critic", "verdict": "PASS" or "FAIL",\n'
                    ' "reasoning": "...", "blockers": [...], "suggestions": [...]}'
                ),
            ),
            Agent(
                name="Scope Critic",
                model=instantiate_model("claude-haiku-4-5"),
                instructions=(
                    "You are a Scope Critic. Evaluate scope alignment.\n"
                    "Consider: scope creep, unnecessary complexity, alignment with\n"
                    "original requirements, and appropriate boundaries.\n\n"
                    "Return your verdict as a JSON object:\n"
                    '{"reviewer": "Scope Critic", "verdict": "PASS" or "FAIL",\n'
                    ' "reasoning": "...", "blockers": [...], "suggestions": [...]}'
                ),
            ),
        ],
        mode=TeamMode.broadcast,
        db=db,
    )


# ---------------------------------------------------------------------------
# Gate check functions (workflow steps)
# ---------------------------------------------------------------------------


def _is_genuine_team_error(errors: list[str]) -> bool:
    """Filter check_team_member_errors results for genuine failures.

    Same heuristic as P1-03's design_team._is_genuine_team_error().
    Bare mentions of 'Error' in critic feedback (e.g., "Error handling
    is incomplete") are legitimate and should not trigger AC-06 rejection.
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


def check_plan_gate(step_input: StepInput) -> StepOutput:
    """Check Plan Review Gate — ALL must PASS (DESIGN.md §4.3).

    Executed inside the review Loop as a gate check step.
    Results written to session_state for end_condition and post-Loop gate.

    AC-02: This step must be wrapped with on_error=OnError.fail.
    AC-03: Uses get_ss()/set_ss() for session state access.
    AC-06: Calls check_team_member_errors() on team output.
    """
    content = step_input.previous_step_content

    # AC-06: Check for team member errors. Use raise_on_error=False
    # and filter for genuine Agno failures to avoid false positives
    # from critic feedback discussing errors legitimately.
    errors = check_team_member_errors(content, raise_on_error=False)
    if errors and _is_genuine_team_error(errors):
        from orchestra.utils.team import TeamMemberError
        raise TeamMemberError(errors)

    verdicts = parse_verdicts(content)
    all_pass = all(v.verdict == "PASS" for v in verdicts)
    round_num = get_ss(step_input, "plan_review_round", 0) + 1
    set_ss(step_input, "plan_review_round", round_num)

    if all_pass:
        set_ss(step_input, "plan_gate_passed", True)
        # "GATE_PASSED" is the signal string checked by plan_review_approved().
        # We use content (not metadata) because Agno's StepOutput has no metadata field.
        return StepOutput(content="GATE_PASSED")

    # Not passed — store feedback for revision step
    set_ss(step_input, "plan_gate_passed", False)
    set_ss(step_input, "plan_gate_verdicts", [v.model_dump() for v in verdicts])
    return StepOutput(content=format_feedback(verdicts))


def plan_review_approved(step_outputs: list[StepOutput]) -> bool:
    """End condition for plan_review_loop (AC-01).

    Returns True when the gate check step output "GATE_PASSED".
    Never uses StepOutput(stop=True) — AC-01 compliant.

    Note: We check the content string rather than metadata because
    Agno's StepOutput does not support a metadata field.
    """
    return any(
        isinstance(o.content, str) and o.content == "GATE_PASSED"
        for o in step_outputs
    )


def create_check_plan_review_result(
    events_db: SqliteDb,
) -> Any:
    """Factory that returns a post-Loop step function with events_db bound.

    DecisionGate records must go to events_db (not traces_db) so the
    REST API and Watchdog can find them (DESIGN.md §4.5, §10.1).
    Step functions only receive StepInput (which gives access to traces_db
    via workflow_session.db), so we use a closure to bind events_db at
    workflow assembly time.

    Usage at workflow assembly::

        Step(
            executor=create_check_plan_review_result(events_db),
            name="check_plan_review_result",
            ...
        )

    Args:
        events_db: The events database for DecisionGate persistence.

    Returns:
        Step function with signature (StepInput) -> StepOutput.
    """

    def check_plan_review_result(step_input: StepInput) -> StepOutput:
        """Post-Loop step: check if plan review passed (AC-04).

        If passed, returns PLAN_REVIEW_PASSED.
        If not passed, creates a DecisionGate for human escalation.
        """
        if get_ss(step_input, "plan_gate_passed"):
            return StepOutput(content="PLAN_REVIEW_PASSED")

        # Not passed after max iterations — create DecisionGate for human review
        verdicts_data = get_ss(step_input, "plan_gate_verdicts", [])
        verdicts = [GateVerdict(**v) for v in verdicts_data]

        workflow_run_id = getattr(
            step_input.workflow_session, "session_id", "unknown"
        )
        agent_id = "plan_review_gate"

        # events_db is bound via closure — DecisionGate records go to
        # events.db, not traces.db (DESIGN.md §4.5, §10.1).
        gate = create_decision_gate(
            events_db,
            workflow_run_id=workflow_run_id,
            agent_id=agent_id,
            gate_type="plan_review",
            context={
                "verdicts": [v.model_dump() for v in verdicts],
                "feedback": format_feedback(verdicts),
            },
        )
        set_ss(step_input, "pending_decision_gate_id", gate.id)
        logger.info("Created DecisionGate %s for plan review escalation", gate.id)

        round_num = get_ss(step_input, "plan_review_round", 0)
        return StepOutput(content=f"PLAN_REVIEW_FAILED_AFTER_{round_num}_ROUNDS")

    return check_plan_review_result


def revise_plan_from_feedback(step_input: StepInput) -> StepOutput:
    """Revise plan based on review feedback (inside Loop).

    Called when check_plan_gate returns FAIL. The previous_step_content
    contains the formatted feedback from check_plan_gate.
    """
    feedback = step_input.previous_step_content
    current_plan = get_ss(step_input, "latest_design_content", "")

    if not current_plan:
        raise ValueError(
            "No plan found in session_state['latest_design_content']. "
            "Design team output must be persisted before plan review."
        )

    revision_content = (
        f"## Plan Revision Request (Round {get_ss(step_input, 'plan_review_round', 0)})\n\n"
        f"### Current Plan\n{current_plan}\n\n"
        f"### Reviewer Feedback\n{feedback}\n\n"
        f"Please revise the plan to address all blocking issues."
    )

    set_ss(step_input, "latest_design_content", revision_content)
    return StepOutput(content=revision_content)
