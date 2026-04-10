"""
G0-D: TeamMode.broadcast + coordinate Validation PoC

Validates Agno 2.5.14 Team and TeamMode for Orchestra's multi-agent
orchestration requirements.

Environment:
- agno==2.5.14
- Python 3.14
- No LLM API keys required (uses StubModel returning deterministic responses)

Assertions:
  [D1] broadcast mode: delegate_to_all_members flag correctly set
  [D2] coordinate mode: default flags correctly set (no broadcast, no route)
  [D3] route mode: respond_directly flag correctly set
  [D4] Mode resolution: explicit mode overrides conflicting boolean flags
  [D5] broadcast run: ALL members produce responses (3 agents)
  [D6] coordinate run: leader delegates to specific member via tool call
  [D7] Team-in-Workflow: Team can be assigned to Step(team=...) and execute
  [D8] Member identity: leader has access to member names/descriptions

Design Deltas (vs DESIGN.md v4.4):
  Recorded inline as [DELTA-nn] when discovered.
"""

import json
import sys
import traceback
import uuid
from copy import copy
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union

from agno.agent import Agent
from agno.models.base import Model
from agno.models.response import ModelResponse
from agno.team import Team
from agno.team.mode import TeamMode
from agno.workflow import Workflow, Step
from agno.workflow.types import StepInput, StepOutput


# ---------------------------------------------------------------------------
# StubModel: deterministic model that produces canned responses and tool calls
# ---------------------------------------------------------------------------

@dataclass
class StubModel(Model):
    """A fake Model for testing Team orchestration without real LLM API keys.

    Behavior:
    - When tools contain 'delegate_task_to_members': produces that tool call
      (broadcast mode)
    - When tools contain 'delegate_task_to_member': produces a tool call to
      the first available member (coordinate mode)
    - When no delegation tools present (member agent context): returns a
      deterministic text response including the agent name
    - After tool results are received: produces a synthesis response
    """

    # Name tag embedded in responses for assertion matching
    agent_tag: str = "stub"
    # Track invocations for assertion evidence
    invocation_log: list = field(default_factory=list)

    def __post_init__(self):
        super().__post_init__()
        if self.name is None:
            self.name = f"StubModel-{self.agent_tag}"
        if self.provider is None:
            self.provider = "stub"

    def invoke(self, messages=None, assistant_message=None,
               response_format=None, tools=None, tool_choice=None,
               run_response=None, compress_tool_results=False,
               **kwargs) -> ModelResponse:
        """Synchronous invoke — returns deterministic response or tool call."""

        messages = messages or []
        tools = tools or []

        call_id = f"call_{uuid.uuid4().hex[:12]}"

        self.invocation_log.append({
            "agent_tag": self.agent_tag,
            "num_messages": len(messages),
            "num_tools": len(tools),
        })

        # Check if we've already received tool results (synthesis phase)
        has_tool_results = any(
            getattr(m, "role", None) == "tool" or
            getattr(m, "tool_call_id", None) is not None
            for m in messages
        )

        if has_tool_results:
            # Synthesis phase: collect tool results and produce final answer
            tool_contents = []
            for m in messages:
                if getattr(m, "role", None) == "tool" or getattr(m, "tool_call_id", None) is not None:
                    tool_contents.append(str(getattr(m, "content", "")))
            synthesis = f"[{self.agent_tag}] Synthesis: " + " | ".join(tool_contents)
            return ModelResponse(role="assistant", content=synthesis)

        # Check for delegation tools
        tool_names = set()
        for t in tools:
            if isinstance(t, dict):
                fn = t.get("function", {})
                tool_names.add(fn.get("name", ""))

        if "delegate_task_to_members" in tool_names:
            # Broadcast: delegate to all members
            user_input = self._extract_user_input(messages)
            return ModelResponse(
                role="assistant",
                content=None,
                tool_calls=[{
                    "type": "function",
                    "id": call_id,
                    "function": {
                        "name": "delegate_task_to_members",
                        "arguments": json.dumps({
                            "task": f"Process this input: {user_input}"
                        }),
                    },
                }],
            )

        if "delegate_task_to_member" in tool_names:
            # Coordinate: delegate to a specific member
            # Extract first member_id from system message
            member_id = self._extract_first_member_id(messages)
            user_input = self._extract_user_input(messages)
            return ModelResponse(
                role="assistant",
                content=None,
                tool_calls=[{
                    "type": "function",
                    "id": call_id,
                    "function": {
                        "name": "delegate_task_to_member",
                        "arguments": json.dumps({
                            "member_id": member_id,
                            "task": f"Process this input: {user_input}",
                        }),
                    },
                }],
            )

        # No delegation tools → this is a member agent, return text response
        user_input = self._extract_user_input(messages)
        return ModelResponse(
            role="assistant",
            content=f"[{self.agent_tag}] Processed: {user_input}",
        )

    async def ainvoke(self, *args, **kwargs) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args, **kwargs) -> Iterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args, **kwargs) -> AsyncIterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response: Any, **kwargs) -> ModelResponse:
        return ModelResponse(role="assistant", content=str(response))

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return ModelResponse(role="assistant", content=str(response))

    def _extract_user_input(self, messages) -> str:
        """Extract the last user message content."""
        for m in reversed(messages):
            if getattr(m, "role", None) == "user":
                content = getattr(m, "content", "")
                if content:
                    return str(content)[:200]
        return "<no_user_input>"

    def _extract_first_member_id(self, messages) -> str:
        """Extract the first member agent ID from the system message.

        Agno formats member info as: <member id="member_id" name="MemberName">
        """
        import re
        for m in messages:
            if getattr(m, "role", None) in ("system", "developer"):
                content = str(getattr(m, "content", ""))
                # Agno uses <member id="..."> XML-style tags
                ids = re.findall(r'<member\s+id="([^"]+)"', content)
                if ids:
                    return ids[0]
        return "unknown_member"


# ---------------------------------------------------------------------------
# Shared test infrastructure (follows G0-A pattern)
# ---------------------------------------------------------------------------

@dataclass
class Assertion:
    id: str
    description: str
    passed: bool
    evidence: str
    design_delta: str = ""
    observational: bool = False


results: list[Assertion] = []
run_log: list[str] = []


def log(msg: str) -> None:
    run_log.append(msg)


def record(assertion_id: str, description: str, passed: bool, evidence: str,
           design_delta: str = "", observational: bool = False) -> None:
    results.append(Assertion(assertion_id, description, passed, evidence, design_delta, observational))
    status = "PASS" if passed else "FAIL"
    log(f"[{assertion_id}] {status}: {description}")
    if design_delta:
        log(f"  [DESIGN DELTA] {design_delta}")


# ---------------------------------------------------------------------------
# D1-D4: Team construction and mode resolution
# ---------------------------------------------------------------------------

def test_mode_configuration() -> None:
    """Validate Team mode flags are correctly set from TeamMode enum."""

    # Helper: create minimal agents
    def make_agents():
        return [
            Agent(name="Agent_A", model=StubModel(id="stub-a", agent_tag="A")),
            Agent(name="Agent_B", model=StubModel(id="stub-b", agent_tag="B")),
        ]

    # D1: broadcast mode
    team_bc = Team(
        members=make_agents(),
        mode=TeamMode.broadcast,
        model=StubModel(id="stub-leader", agent_tag="leader"),
    )
    bc_correct = team_bc.delegate_to_all_members is True and team_bc.respond_directly is False
    record("D1", "broadcast mode sets delegate_to_all_members=True, respond_directly=False",
           bc_correct,
           f"delegate_to_all_members={team_bc.delegate_to_all_members}, "
           f"respond_directly={team_bc.respond_directly}")

    # D2: coordinate mode
    team_co = Team(
        members=make_agents(),
        mode=TeamMode.coordinate,
        model=StubModel(id="stub-leader", agent_tag="leader"),
    )
    co_correct = (team_co.delegate_to_all_members is False and
                  team_co.respond_directly is False)
    record("D2", "coordinate mode sets delegate_to_all_members=False, respond_directly=False",
           co_correct,
           f"delegate_to_all_members={team_co.delegate_to_all_members}, "
           f"respond_directly={team_co.respond_directly}")

    # D3: route mode
    team_rt = Team(
        members=make_agents(),
        mode=TeamMode.route,
        model=StubModel(id="stub-leader", agent_tag="leader"),
    )
    rt_correct = team_rt.respond_directly is True and team_rt.delegate_to_all_members is False
    record("D3", "route mode sets respond_directly=True, delegate_to_all_members=False",
           rt_correct,
           f"respond_directly={team_rt.respond_directly}, "
           f"delegate_to_all_members={team_rt.delegate_to_all_members}")

    # D4: explicit mode overrides conflicting boolean flags
    # Set delegate_to_all_members=True but mode=coordinate — mode should win
    team_override = Team(
        members=make_agents(),
        mode=TeamMode.coordinate,
        delegate_to_all_members=True,  # conflict: should be overridden
        model=StubModel(id="stub-leader", agent_tag="leader"),
    )
    override_correct = (team_override.delegate_to_all_members is False and
                        team_override.mode == TeamMode.coordinate)
    record("D4", "explicit TeamMode overrides conflicting boolean flags",
           override_correct,
           f"mode={team_override.mode}, delegate_to_all_members={team_override.delegate_to_all_members}",
           design_delta="DELTA-04: TeamMode enum normalizes boolean flags deterministically — "
           "DESIGN.md should note that mode= always wins over respond_directly/delegate_to_all_members"
           if override_correct else "")


# ---------------------------------------------------------------------------
# D5: broadcast run — all members respond
# ---------------------------------------------------------------------------

def test_broadcast_run() -> None:
    """Validate broadcast mode delivers input to ALL 3 member agents."""

    member_models = {
        "Alpha": StubModel(id="stub-alpha", agent_tag="Alpha"),
        "Beta": StubModel(id="stub-beta", agent_tag="Beta"),
        "Gamma": StubModel(id="stub-gamma", agent_tag="Gamma"),
    }

    members = [
        Agent(name=name, model=model, description=f"{name} specialist agent")
        for name, model in member_models.items()
    ]

    leader_model = StubModel(id="stub-leader-bc", agent_tag="leader-bc")

    team = Team(
        name="BroadcastTeam",
        members=members,
        mode=TeamMode.broadcast,
        model=leader_model,
    )

    try:
        response = team.run("Analyze the orchestra design document")
        content = response.content if hasattr(response, 'content') else str(response)

        # Check that all 3 members produced responses
        alpha_responded = "[Alpha]" in str(content)
        beta_responded = "[Beta]" in str(content)
        gamma_responded = "[Gamma]" in str(content)
        all_responded = alpha_responded and beta_responded and gamma_responded

        record("D5", "broadcast run: all 3 members receive input and produce responses",
               all_responded,
               f"Alpha={alpha_responded}, Beta={beta_responded}, Gamma={gamma_responded}, "
               f"content_preview={str(content)[:300]}")
    except Exception as e:
        record("D5", "broadcast run: all 3 members receive input and produce responses",
               False,
               f"Exception: {type(e).__name__}: {e}")
        log(f"  Traceback: {traceback.format_exc()}")


# ---------------------------------------------------------------------------
# D6: coordinate run — leader delegates to specific member
# ---------------------------------------------------------------------------

def test_coordinate_run() -> None:
    """Validate coordinate mode: leader selects and delegates to member."""

    member_models = {
        "Researcher": StubModel(id="stub-researcher", agent_tag="Researcher"),
        "Designer": StubModel(id="stub-designer", agent_tag="Designer"),
    }

    members = [
        Agent(name=name, model=model, description=f"{name} role in the team")
        for name, model in member_models.items()
    ]

    leader_model = StubModel(id="stub-leader-co", agent_tag="leader-co")

    team = Team(
        name="CoordinateTeam",
        members=members,
        mode=TeamMode.coordinate,
        model=leader_model,
    )

    try:
        response = team.run("Research the feasibility of the workflow design")
        content = response.content if hasattr(response, 'content') else str(response)

        # In coordinate mode, the leader delegates to at least one member
        member_responded = ("[Researcher]" in str(content) or
                            "[Designer]" in str(content))

        # The leader should have synthesized the response
        has_synthesis = "[leader-co]" in str(content) or "Synthesis" in str(content)

        record("D6", "coordinate run: leader delegates to member and synthesizes",
               member_responded and has_synthesis,
               f"member_responded={member_responded}, has_synthesis={has_synthesis}, "
               f"content_preview={str(content)[:300]}")
    except Exception as e:
        record("D6", "coordinate run: leader delegates to member and synthesizes",
               False,
               f"Exception: {type(e).__name__}: {e}")
        log(f"  Traceback: {traceback.format_exc()}")


# ---------------------------------------------------------------------------
# D7: Team-in-Workflow integration
# ---------------------------------------------------------------------------

def test_team_in_workflow() -> None:
    """Validate Team can be used as a step executor in Workflow."""

    members = [
        Agent(name="ReviewerA", model=StubModel(id="stub-ra", agent_tag="ReviewerA")),
        Agent(name="ReviewerB", model=StubModel(id="stub-rb", agent_tag="ReviewerB")),
    ]

    review_team = Team(
        name="ReviewTeam",
        members=members,
        mode=TeamMode.broadcast,
        model=StubModel(id="stub-leader-wf", agent_tag="leader-wf"),
    )

    # Pre-team step (pure function)
    def prepare_step(step_input: StepInput) -> StepOutput:
        return StepOutput(content="design_document_for_review")

    # Post-team step (pure function)
    def summarize_step(step_input: StepInput) -> StepOutput:
        prev = step_input.previous_step_content
        return StepOutput(content=f"summary_of: {prev}")

    try:
        wf = Workflow(
            name="team_in_workflow",
            steps=[
                Step(name="prepare", executor=prepare_step),
                Step(name="review", team=review_team),
                Step(name="summarize", executor=summarize_step),
            ],
        )

        output = wf.run("start review process")
        content = str(output.content) if hasattr(output, 'content') else str(output)

        # Verify workflow completed with full chain: pre → Team (with members) → post
        has_summary = "summary_of:" in content
        has_team_evidence = "leader-wf" in content or "ReviewerA" in content

        record("D7", "Team can be assigned to Step(team=...) in Workflow and execute",
               has_summary and has_team_evidence,
               f"has_summary={has_summary}, has_team_evidence={has_team_evidence}, "
               f"workflow_output_preview={content[:300]}",
               design_delta="DELTA-05: Step accepts team= parameter for Team-based execution — "
               "DESIGN.md §2.3 should document Team-as-step-executor pattern"
               if has_summary else "")
    except Exception as e:
        record("D7", "Team can be assigned to Step(team=...) in Workflow and execute",
               False,
               f"Exception: {type(e).__name__}: {e}")
        log(f"  Traceback: {traceback.format_exc()}")


# ---------------------------------------------------------------------------
# D8: Member identity visible to leader
# ---------------------------------------------------------------------------

def test_member_identity() -> None:
    """Validate that leader has access to member names and descriptions."""

    members = [
        Agent(name="Analyst", model=StubModel(id="stub-an", agent_tag="Analyst"),
              description="Performs data analysis"),
        Agent(name="Validator", model=StubModel(id="stub-val", agent_tag="Validator"),
              description="Validates analysis results"),
    ]

    team = Team(
        name="IdentityTeam",
        members=members,
        mode=TeamMode.coordinate,
        model=StubModel(id="stub-leader-id", agent_tag="leader-id"),
    )

    # Check that get_members_system_message_content includes member info
    try:
        member_info = team.get_members_system_message_content(indent=0)
        has_analyst = "Analyst" in member_info
        has_validator = "Validator" in member_info
        has_descriptions = "data analysis" in member_info.lower() or "validates" in member_info.lower()

        record("D8", "leader has access to member names and descriptions",
               has_analyst and has_validator and has_descriptions,
               f"has_analyst={has_analyst}, has_validator={has_validator}, "
               f"has_descriptions={has_descriptions}, "
               f"member_info_preview={member_info[:300]}")
    except Exception as e:
        record("D8", "leader has access to member names and descriptions",
               False,
               f"Exception: {type(e).__name__}: {e}")
        log(f"  Traceback: {traceback.format_exc()}")


# ---------------------------------------------------------------------------
# D9: broadcast member failure — not silently swallowed
# ---------------------------------------------------------------------------

def test_broadcast_member_failure() -> None:
    """Validate that a failing member in broadcast mode does not silently disappear."""

    class FailingStubModel(StubModel):
        """A StubModel that raises an exception on invoke."""
        def invoke(self, *args, **kwargs) -> ModelResponse:
            raise RuntimeError("Simulated agent failure: model unavailable")

    members = [
        Agent(name="HealthyA", model=StubModel(id="stub-ha", agent_tag="HealthyA")),
        Agent(name="Failing", model=FailingStubModel(id="stub-fail", agent_tag="Failing")),
        Agent(name="HealthyB", model=StubModel(id="stub-hb", agent_tag="HealthyB")),
    ]

    team = Team(
        name="BroadcastFailTeam",
        members=members,
        mode=TeamMode.broadcast,
        model=StubModel(id="stub-leader-fail", agent_tag="leader-fail"),
    )

    try:
        response = team.run("Test with one failing member")
        content = str(response.content) if hasattr(response, 'content') else str(response)

        # Key question: does the failure appear in the output, or is it silently swallowed?
        has_error_signal = ("Error" in content or "error" in content or
                           "fail" in content.lower() or "exception" in content.lower())
        healthy_a_responded = "[HealthyA]" in content
        healthy_b_responded = "[HealthyB]" in content

        record("D9", "broadcast: failing member produces visible error signal (not silently swallowed)",
               has_error_signal,
               f"has_error_signal={has_error_signal}, healthyA={healthy_a_responded}, "
               f"healthyB={healthy_b_responded}, content_preview={content[:400]}")
    except Exception as e:
        # If the entire Team.run() raises, that's also an observable failure (not silent)
        record("D9", "broadcast: failing member produces visible error signal (not silently swallowed)",
               True,
               f"Team.run() raised {type(e).__name__}: {str(e)[:200]} — failure is observable, not silent",
               observational=True)


# ---------------------------------------------------------------------------
# D10: coordinate member failure — leader receives error
# ---------------------------------------------------------------------------

def test_coordinate_member_failure() -> None:
    """Validate that coordinate mode surfaces member failure to leader."""

    class FailingStubModel(StubModel):
        """A StubModel that raises an exception on invoke."""
        def invoke(self, *args, **kwargs) -> ModelResponse:
            raise RuntimeError("Simulated agent failure: model unavailable")

    members = [
        Agent(name="FailingWorker", model=FailingStubModel(id="stub-fw", agent_tag="FailingWorker"),
              description="A worker that will fail"),
    ]

    # Leader StubModel will try to delegate to the only member
    team = Team(
        name="CoordinateFailTeam",
        members=members,
        mode=TeamMode.coordinate,
        model=StubModel(id="stub-leader-cfail", agent_tag="leader-cfail"),
    )

    try:
        response = team.run("Delegate to the worker")
        content = str(response.content) if hasattr(response, 'content') else str(response)

        has_error_signal = ("Error" in content or "error" in content or
                           "fail" in content.lower() or "exception" in content.lower())

        record("D10", "coordinate: member failure surfaces error to leader (not silent)",
               has_error_signal,
               f"has_error_signal={has_error_signal}, content_preview={content[:400]}")
    except Exception as e:
        # If the entire Team.run() raises, that's also observable
        record("D10", "coordinate: member failure surfaces error to leader (not silent)",
               True,
               f"Team.run() raised {type(e).__name__}: {str(e)[:200]} — failure is observable",
               observational=True)


# ---------------------------------------------------------------------------
# Runner and reporter (follows G0-A pattern)
# ---------------------------------------------------------------------------

def run_all_tests() -> dict[str, Any]:
    """Execute all tests and return results."""
    global results, run_log
    results = []
    run_log = []

    log("=" * 60)
    log("G0-D VALIDATION: TeamMode.broadcast + coordinate")
    log("=" * 60)

    tests = [
        ("Mode Configuration (D1-D4)", test_mode_configuration),
        ("Broadcast Run (D5)", test_broadcast_run),
        ("Coordinate Run (D6)", test_coordinate_run),
        ("Team-in-Workflow (D7)", test_team_in_workflow),
        ("Member Identity (D8)", test_member_identity),
        ("Broadcast Member Failure (D9)", test_broadcast_member_failure),
        ("Coordinate Member Failure (D10)", test_coordinate_member_failure),
    ]

    for test_name, test_fn in tests:
        log(f"\n--- {test_name} ---")
        try:
            test_fn()
        except Exception as e:
            log(f"\n!!! Test '{test_name}' crashed: {e}")
            traceback.print_exc()

    return {
        "assertions": results,
        "run_log": run_log,
    }


def print_report(test_results: dict[str, Any]) -> None:
    """Print formatted report."""
    assertions = test_results["assertions"]
    run_log_lines = test_results["run_log"]

    print("\n" + "=" * 60)
    print("G0-D VALIDATION REPORT")
    print("=" * 60)

    import agno
    print(f"\nEnvironment:")
    print(f"  agno version: {agno.__version__}")
    print(f"  Python: {sys.version}")

    strong = [a for a in assertions if not a.observational]
    obs = [a for a in assertions if a.observational]

    strong_passed = sum(1 for a in strong if a.passed)
    print(f"\nStrong Assertions ({strong_passed}/{len(strong)} passed):")
    for a in strong:
        status = "PASS" if a.passed else "FAIL"
        print(f"  [{a.id}] {status}: {a.description}")
        print(f"         Evidence: {a.evidence}")
        if a.design_delta:
            print(f"         [DESIGN DELTA]: {a.design_delta}")

    if obs:
        print(f"\nObservational (behavior documentation, {len(obs)} items):")
        for a in obs:
            print(f"  [{a.id}] OBS: {a.description}")
            print(f"         Evidence: {a.evidence}")
            if a.design_delta:
                print(f"         [DESIGN DELTA]: {a.design_delta}")

    deltas = [a for a in assertions if a.design_delta]
    if deltas:
        print(f"\nDesign Deltas ({len(deltas)}):")
        for a in deltas:
            print(f"  [{a.id}] {a.design_delta}")

    blocking_failures = [a for a in strong if not a.passed]

    print(f"\nConclusion:")
    if not blocking_failures:
        print(f"  SUPPORTED — {strong_passed}/{len(strong)} strong assertions pass. "
              f"{len(obs)} observational items.")
    else:
        failed_ids = [a.id for a in blocking_failures]
        print(f"  PARTIALLY_SUPPORTED — Blocking failures: {failed_ids}")

    print(f"\n{'=' * 60}")
    print("RUN LOG")
    print("=" * 60)
    for line in run_log_lines:
        print(line)


if __name__ == "__main__":
    all_consistent = True

    for run_num in range(1, 4):
        print(f"\n{'#' * 60}")
        print(f"# RUN {run_num}/3")
        print(f"{'#' * 60}")

        test_results = run_all_tests()
        print_report(test_results)

        current = [(a.id, a.passed, a.evidence) for a in test_results["assertions"]]
        if run_num == 1:
            first_run_results = current
        else:
            if current != first_run_results:
                all_consistent = False
                print(f"\n  WARNING: Run {run_num} results differ from run 1!")
                for (fid, fp, fe), (cid, cp, ce) in zip(first_run_results, current):
                    if (fid, fp, fe) != (cid, cp, ce):
                        print(f"    {fid}: passed {fp}->{cp}, evidence changed: {fe != ce}")

    print(f"\n{'=' * 60}")
    print(f"REPEATABILITY: {'CONSISTENT across 3 runs' if all_consistent else 'INCONSISTENT — results varied between runs'}")
    print(f"{'=' * 60}")
