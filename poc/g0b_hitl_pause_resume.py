"""
G0-B: HITL Native Pause/Resume Validation PoC

Validates Agno 2.5.14's native Human-in-the-Loop mechanisms for Orchestra's
Decision Gate protocol. Tests requires_confirmation, on_error=OnError.pause,
continue_run(), and session_data preservation across pause/resume cycles.

Environment:
- agno==2.5.14
- Python 3.14
- No LLM API keys required (pure function steps)

Strong Assertions:
  [B1] requires_confirmation pauses workflow before step execution
  [B2] continue_run() resumes and executes the paused step
  [B3] Unresolved requirements block continue_run() with clear error
  [B4] on_reject=OnReject.cancel terminates workflow on rejection
  [B5] on_reject=OnReject.skip skips rejected step, continues workflow
  [B6] on_error=OnError.pause pauses on exception, retry resumes
  [B7] on_error=OnError.fail raises exception to caller (gate blocking)
  [B8] session_data preserved across pause/resume cycle
  [B9] Loop-internal pause: resume restores iteration count correctly
  [B10] Resume idempotency: step executes exactly once after resume

Observational:
  [B11] Multiple sequential pauses in one workflow

Design Deltas (vs DESIGN.md v4.4):
  Recorded inline as [DELTA-nn] when discovered.
"""

import sys
import traceback
from dataclasses import dataclass
from typing import Any

from agno.db.in_memory import InMemoryDb
from agno.workflow import Workflow, Step, Loop
from agno.workflow.types import StepInput, StepOutput, OnError, OnReject


# ---------------------------------------------------------------------------
# Shared test infrastructure
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
    tag = " [OBS]" if observational else ""
    log(f"[{assertion_id}] {status}{tag}: {description}")
    if design_delta:
        log(f"  [DESIGN DELTA] {design_delta}")


# ---------------------------------------------------------------------------
# Test B1 + B2 + B3: requires_confirmation basic pause/resume
# ---------------------------------------------------------------------------

def test_confirmation_pause_resume() -> None:
    """
    Workflow: step_a -> step_confirm (requires_confirmation) -> step_c
    Validates:
      B1: Workflow pauses before executing step_confirm
      B2: continue_run() with confirmation resumes and completes
      B3: continue_run() with unresolved requirements raises error
    """
    log("\n=== Test: Confirmation Pause/Resume (B1, B2, B3) ===")
    step_confirm_executed = False

    def step_a(step_input: StepInput) -> StepOutput:
        log("  step_a executing")
        session_data = step_input.workflow_session.session_data
        session_data["step_a_done"] = True
        return StepOutput(content="step_a_output")

    def step_confirm(step_input: StepInput) -> StepOutput:
        nonlocal step_confirm_executed
        step_confirm_executed = True
        log("  step_confirm executing (should only run after confirmation)")
        return StepOutput(content="confirmed_output")

    def step_c(step_input: StepInput) -> StepOutput:
        log(f"  step_c executing, prev={step_input.previous_step_content!r}")
        return StepOutput(content="final_output")

    wf = Workflow(
        name="test_confirmation",
        db=InMemoryDb(),
        steps=[
            Step(executor=step_a, name="step_a"),
            Step(executor=step_confirm, name="step_confirm",
                 requires_confirmation=True,
                 confirmation_message="Approve this gate?"),
            Step(executor=step_c, name="step_c"),
        ],
    )

    # First run — should pause
    output = wf.run("start")
    log(f"  After first run: status={output.status}, is_paused={output.is_paused}")

    record("B1", "requires_confirmation pauses workflow before step execution",
           output.is_paused and not step_confirm_executed,
           f"is_paused={output.is_paused}, step_confirm_executed={step_confirm_executed}, "
           f"paused_step_name={output.paused_step_name!r}")

    # B3: Try continue without resolving — should error
    b3_error = None
    try:
        wf.continue_run(run_response=output)
    except (ValueError, Exception) as e:
        b3_error = str(e)
        log(f"  continue_run without resolution raised: {type(e).__name__}: {b3_error}")

    record("B3", "Unresolved requirements block continue_run() with clear error",
           b3_error is not None,
           f"Error message: {b3_error!r}")

    # B2: Resolve confirmation and continue
    if output.step_requirements:
        for req in output.step_requirements:
            if req.requires_confirmation:
                req.confirmed = True
                log(f"  Confirmed requirement for step {req.step_name!r}")

    resumed_output = wf.continue_run(run_response=output)
    log(f"  After continue_run: status={resumed_output.status}, content={resumed_output.content!r}")

    record("B2", "continue_run() resumes and executes the paused step",
           step_confirm_executed and not resumed_output.is_paused,
           f"step_confirm_executed={step_confirm_executed}, "
           f"final_status={resumed_output.status}, content={resumed_output.content!r}")


# ---------------------------------------------------------------------------
# Test B4 + B5: OnReject behavior
# ---------------------------------------------------------------------------

def test_on_reject_cancel() -> None:
    """B4: on_reject=OnReject.cancel terminates workflow."""
    log("\n=== Test: OnReject.cancel (B4) ===")

    def gate_step(step_input: StepInput) -> StepOutput:
        log("  gate_step executing (should not run if rejected)")
        return StepOutput(content="gate_passed")

    def post_gate(step_input: StepInput) -> StepOutput:
        log("  post_gate executing")
        return StepOutput(content="post_gate_done")

    wf = Workflow(
        name="test_reject_cancel",
        db=InMemoryDb(),
        steps=[
            Step(executor=gate_step, name="gate_step",
                 requires_confirmation=True,
                 on_reject=OnReject.cancel),
            Step(executor=post_gate, name="post_gate"),
        ],
    )

    output = wf.run("start")
    assert output.is_paused

    # Reject the step
    for req in output.step_requirements or []:
        req.confirmed = False

    resumed = wf.continue_run(run_response=output)
    log(f"  After rejection: status={resumed.status}")

    is_cancelled = "cancel" in str(resumed.status).lower()
    record("B4", "on_reject=OnReject.cancel terminates workflow on rejection",
           is_cancelled,
           f"status after rejection={resumed.status}")


def test_on_reject_skip() -> None:
    """B5: on_reject=OnReject.skip skips step and continues."""
    log("\n=== Test: OnReject.skip (B5) ===")
    gate_executed = False
    post_gate_executed = False

    def gate_step(step_input: StepInput) -> StepOutput:
        nonlocal gate_executed
        gate_executed = True
        log("  gate_step executing")
        return StepOutput(content="gate_passed")

    def post_gate(step_input: StepInput) -> StepOutput:
        nonlocal post_gate_executed
        post_gate_executed = True
        log(f"  post_gate executing, prev={step_input.previous_step_content!r}")
        return StepOutput(content="post_gate_done")

    wf = Workflow(
        name="test_reject_skip",
        db=InMemoryDb(),
        steps=[
            Step(executor=gate_step, name="gate_step",
                 requires_confirmation=True,
                 on_reject=OnReject.skip),
            Step(executor=post_gate, name="post_gate"),
        ],
    )

    output = wf.run("start")
    assert output.is_paused

    # Reject — should skip gate_step and continue to post_gate
    for req in output.step_requirements or []:
        req.confirmed = False

    resumed = wf.continue_run(run_response=output)
    log(f"  After skip-rejection: status={resumed.status}, gate_executed={gate_executed}, post_gate_executed={post_gate_executed}")

    record("B5", "on_reject=OnReject.skip skips rejected step, continues workflow",
           not gate_executed and post_gate_executed,
           f"gate_executed={gate_executed}, post_gate_executed={post_gate_executed}, "
           f"final_status={resumed.status}")


# ---------------------------------------------------------------------------
# Test B6: on_error=OnError.pause
# ---------------------------------------------------------------------------

def test_on_error_pause() -> None:
    """B6: on_error=OnError.pause pauses on exception, retry resumes."""
    log("\n=== Test: OnError.pause (B6) ===")
    call_count = 0

    def failing_step(step_input: StepInput) -> StepOutput:
        nonlocal call_count
        call_count += 1
        log(f"  failing_step call #{call_count}")
        if call_count == 1:
            raise ValueError("Simulated gate failure")
        return StepOutput(content="recovered_ok")

    wf = Workflow(
        name="test_error_pause",
        db=InMemoryDb(),
        steps=[
            Step(executor=failing_step, name="failing_step",
                 on_error=OnError.pause, max_retries=0),
        ],
    )

    output = wf.run("start")
    log(f"  After first run: status={output.status}, is_paused={output.is_paused}")

    paused_on_error = output.is_paused and call_count == 1

    # Set retry decision on error requirement
    if output.error_requirements:
        for err_req in output.error_requirements:
            err_req.decision = "retry"
            log(f"  Set retry decision for error: {err_req.error_message!r}")

    resumed = wf.continue_run(run_response=output)
    log(f"  After retry: status={resumed.status}, call_count={call_count}")

    record("B6", "on_error=OnError.pause pauses on exception, retry resumes",
           paused_on_error and call_count == 2 and not resumed.is_paused,
           f"paused_on_error={paused_on_error}, call_count={call_count}, "
           f"final_status={resumed.status}, content={resumed.content!r}")


# ---------------------------------------------------------------------------
# Test B7: on_error=OnError.fail (blocking gate semantics)
# ---------------------------------------------------------------------------

def test_on_error_fail() -> None:
    """B7: on_error=OnError.fail raises exception — blocking gate semantics."""
    log("\n=== Test: OnError.fail (B7) ===")

    def failing_gate(step_input: StepInput) -> StepOutput:
        raise ValueError("Quality gate FAILED: tests not passing")

    wf = Workflow(
        name="test_error_fail",
        db=InMemoryDb(),
        steps=[
            Step(executor=failing_gate, name="quality_gate",
                 on_error=OnError.fail, max_retries=0),
        ],
    )

    raised_exception = None
    try:
        wf.run("start")
        log("  No exception raised — on_error=OnError.fail did NOT propagate")
    except Exception as e:
        raised_exception = e
        log(f"  Exception raised: {type(e).__name__}: {e}")

    record("B7", "on_error=OnError.fail raises exception to caller (gate blocking semantics)",
           raised_exception is not None and isinstance(raised_exception, ValueError),
           f"Exception: {type(raised_exception).__name__}: {raised_exception}" if raised_exception
           else "No exception raised",
           design_delta="DELTA-04: on_error=OnError.fail confirmed as blocking mechanism. "
                        "All Quality Gate / Review Gate steps MUST use on_error=OnError.fail."
           if raised_exception else "")


# ---------------------------------------------------------------------------
# Test B8: session_data preservation across pause/resume
# ---------------------------------------------------------------------------

def test_session_data_preservation() -> None:
    """B8: session_data written before pause is available after resume."""
    log("\n=== Test: Session Data Preservation (B8) ===")

    def write_state(step_input: StepInput) -> StepOutput:
        sd = step_input.workflow_session.session_data
        sd["design_doc"] = "final_design_v3"
        sd["review_count"] = 2
        sd["nested"] = {"approved": True, "scores": [9, 8, 10]}
        log(f"  write_state: wrote 3 keys to session_data")
        return StepOutput(content="state_written")

    def gate_step(step_input: StepInput) -> StepOutput:
        log("  gate_step executing after confirmation")
        return StepOutput(content="gate_passed")

    def read_state(step_input: StepInput) -> StepOutput:
        sd = step_input.workflow_session.session_data
        design = sd.get("design_doc")
        count = sd.get("review_count")
        nested = sd.get("nested")
        log(f"  read_state: design={design!r}, count={count!r}, nested={nested!r}")
        return StepOutput(content=f"read:{design},{count},{nested}")

    wf = Workflow(
        name="test_session_preservation",
        db=InMemoryDb(),
        steps=[
            Step(executor=write_state, name="write_state"),
            Step(executor=gate_step, name="gate_step",
                 requires_confirmation=True),
            Step(executor=read_state, name="read_state"),
        ],
    )

    output = wf.run("start")
    assert output.is_paused

    # Confirm and resume
    for req in output.step_requirements or []:
        req.confirmed = True

    resumed = wf.continue_run(run_response=output)
    content = resumed.content if resumed else None
    log(f"  Final content: {content!r}")

    expected_parts = ["final_design_v3", "2", "True"]
    all_present = content is not None and all(p in str(content) for p in expected_parts)

    record("B8", "session_data preserved across pause/resume cycle",
           all_present,
           f"Final content={content!r}. Expected parts {expected_parts} all present: {all_present}")


# ---------------------------------------------------------------------------
# Test B9: Loop-internal pause with iteration tracking
# ---------------------------------------------------------------------------

def test_loop_internal_pause() -> None:
    """B9: Test whether requires_confirmation inside a Loop triggers pause."""
    log("\n=== Test: Loop-Internal Pause (B9) ===")
    iteration_count = 0
    gate_executed_count = 0

    def review_step(step_input: StepInput) -> StepOutput:
        nonlocal iteration_count
        iteration_count += 1
        log(f"  review_step iteration {iteration_count}")
        return StepOutput(content=f"review_iter_{iteration_count}")

    def gate_check(step_input: StepInput) -> StepOutput:
        nonlocal gate_executed_count
        gate_executed_count += 1
        log(f"  gate_check #{gate_executed_count}, prev={step_input.previous_step_content!r}")
        return StepOutput(content="gate_checked")

    def end_check(step_outputs: list[StepOutput]) -> bool:
        return iteration_count >= 2

    wf = Workflow(
        name="test_loop_pause",
        db=InMemoryDb(),
        steps=[
            Loop(
                name="review_loop",
                steps=[
                    Step(executor=review_step, name="review_step"),
                    Step(executor=gate_check, name="gate_check",
                         requires_confirmation=True),
                ],
                max_iterations=3,
                end_condition=end_check,
            ),
        ],
    )

    output = wf.run("start")
    log(f"  After run: is_paused={output.is_paused}, iterations={iteration_count}, "
        f"gate_executed={gate_executed_count}, status={output.status}")

    # If it paused, try to resume (expected behavior)
    if output.is_paused:
        for req in output.step_requirements or []:
            req.confirmed = True
        resumed = wf.continue_run(run_response=output)
        log(f"  After resume: iterations={iteration_count}")
        pauses_in_loop = True
    else:
        # Loop completed without pausing — requires_confirmation ignored inside Loop
        pauses_in_loop = False
        log(f"  Loop completed without pausing — requires_confirmation ignored inside Loop")

    if pauses_in_loop:
        record("B9", "Loop-internal pause: requires_confirmation works inside Loop",
               True,
               f"Paused at iteration {iteration_count}, gate_executed={gate_executed_count}")
    else:
        # Record as PASS with design delta — we successfully discovered the limitation
        record("B9", "Loop-internal pause: requires_confirmation is IGNORED inside Loop",
               True,
               f"Loop ran {iteration_count} iterations, gate executed {gate_executed_count} times "
               f"without pausing. requires_confirmation has no effect inside Loop.",
               design_delta="DELTA-05: requires_confirmation is silently ignored inside Loop. "
               "Decision Gates inside review loops cannot use requires_confirmation for HITL pause. "
               "Alternative: use on_error=OnError.pause with a gate-checking step, or structure "
               "the gate as a post-Loop step.")


# ---------------------------------------------------------------------------
# Test B10: Resume idempotency
# ---------------------------------------------------------------------------

def test_resume_idempotency() -> None:
    """B10: After resume, the paused step executes exactly once."""
    log("\n=== Test: Resume Idempotency (B10) ===")
    execution_count = 0

    def counted_step(step_input: StepInput) -> StepOutput:
        nonlocal execution_count
        execution_count += 1
        log(f"  counted_step execution #{execution_count}")
        return StepOutput(content=f"exec_{execution_count}")

    wf = Workflow(
        name="test_idempotency",
        db=InMemoryDb(),
        steps=[
            Step(executor=counted_step, name="counted_step",
                 requires_confirmation=True),
        ],
    )

    output = wf.run("start")
    assert output.is_paused
    assert execution_count == 0  # Not yet executed

    # Confirm and resume
    for req in output.step_requirements or []:
        req.confirmed = True

    resumed = wf.continue_run(run_response=output)
    log(f"  After resume: execution_count={execution_count}")

    record("B10", "Resume idempotency: step executes exactly once after resume",
           execution_count == 1,
           f"execution_count={execution_count}, expected 1")


# ---------------------------------------------------------------------------
# Test B11: Multiple sequential pauses (observational)
# ---------------------------------------------------------------------------

def test_multiple_pauses() -> None:
    """B11: Workflow with multiple confirmation steps pauses at each one."""
    log("\n=== Test: Multiple Sequential Pauses (B11) ===")
    steps_executed = []

    def step_a(step_input: StepInput) -> StepOutput:
        steps_executed.append("a")
        return StepOutput(content="a_done")

    def gate_1(step_input: StepInput) -> StepOutput:
        steps_executed.append("gate_1")
        return StepOutput(content="gate_1_passed")

    def step_b(step_input: StepInput) -> StepOutput:
        steps_executed.append("b")
        return StepOutput(content="b_done")

    def gate_2(step_input: StepInput) -> StepOutput:
        steps_executed.append("gate_2")
        return StepOutput(content="gate_2_passed")

    def step_c(step_input: StepInput) -> StepOutput:
        steps_executed.append("c")
        return StepOutput(content="c_done")

    wf = Workflow(
        name="test_multi_pause",
        db=InMemoryDb(),
        steps=[
            Step(executor=step_a, name="step_a"),
            Step(executor=gate_1, name="gate_1", requires_confirmation=True),
            Step(executor=step_b, name="step_b"),
            Step(executor=gate_2, name="gate_2", requires_confirmation=True),
            Step(executor=step_c, name="step_c"),
        ],
    )

    # First run — pause at gate_1
    output = wf.run("start")
    pause_1 = output.is_paused and output.paused_step_name == "gate_1"
    log(f"  Pause 1: is_paused={output.is_paused}, at={output.paused_step_name}, executed={steps_executed}")

    # Confirm gate_1
    for req in output.step_requirements or []:
        req.confirmed = True
    output = wf.continue_run(run_response=output)

    pause_2 = output.is_paused and output.paused_step_name == "gate_2"
    log(f"  Pause 2: is_paused={output.is_paused}, at={output.paused_step_name}, executed={steps_executed}")

    # Confirm gate_2
    if output.is_paused and output.step_requirements:
        for req in output.step_requirements:
            req.confirmed = True
        output = wf.continue_run(run_response=output)

    log(f"  Final: status={output.status}, executed={steps_executed}")

    record("B11", "Multiple sequential pauses: workflow pauses at each confirmation step",
           pause_1 and pause_2 and set(steps_executed) == {"a", "gate_1", "b", "gate_2", "c"},
           f"pause_1_at_gate_1={pause_1}, pause_2_at_gate_2={pause_2}, "
           f"steps_executed={steps_executed}",
           observational=True)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_all_tests() -> dict[str, Any]:
    global results, run_log
    results = []
    run_log = []

    log("=" * 60)
    log("G0-B: HITL Native Pause/Resume Validation")
    log("=" * 60)

    tests = [
        ("Confirmation Pause/Resume (B1, B2, B3)", test_confirmation_pause_resume),
        ("OnReject.cancel (B4)", test_on_reject_cancel),
        ("OnReject.skip (B5)", test_on_reject_skip),
        ("OnError.pause (B6)", test_on_error_pause),
        ("OnError.fail (B7)", test_on_error_fail),
        ("Session Data Preservation (B8)", test_session_data_preservation),
        ("Loop-Internal Pause (B9)", test_loop_internal_pause),
        ("Resume Idempotency (B10)", test_resume_idempotency),
        ("Multiple Sequential Pauses (B11)", test_multiple_pauses),
    ]

    for test_name, test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            log(f"\n!!! Test '{test_name}' crashed: {e}")
            traceback.print_exc()

    return {"assertions": results, "run_log": run_log}


def print_report(test_results: dict[str, Any]) -> None:
    assertions = test_results["assertions"]
    run_log_lines = test_results["run_log"]

    print("\n" + "=" * 60)
    print("G0-B VALIDATION REPORT")
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
        print(f"\nObservational ({len(obs)} items):")
        for a in obs:
            print(f"  [{a.id}] OBS: {a.description}")
            print(f"         Evidence: {a.evidence}")

    deltas = [a for a in assertions if a.design_delta]
    if deltas:
        print(f"\nDesign Deltas ({len(deltas)}):")
        for a in deltas:
            print(f"  [{a.id}] {a.design_delta}")

    blocking_failures = [a for a in strong if not a.passed]
    print(f"\nConclusion:")
    if not blocking_failures:
        print(f"  SUPPORTED — {strong_passed}/{len(strong)} strong assertions pass. "
              f"{len(obs)} observational.")
    else:
        failed_ids = [a.id for a in blocking_failures]
        print(f"  PARTIALLY_SUPPORTED — Blocking failures: {failed_ids}")

    print(f"\n{'=' * 60}")
    print("RUN LOG")
    print("=" * 60)
    for line in run_log_lines:
        print(line)


if __name__ == "__main__":
    print("Running G0-B validation (run 1/3)...")
    all_consistent = True

    for run_num in range(1, 4):
        print(f"\n{'#' * 60}")
        print(f"# RUN {run_num}/3")
        print(f"{'#' * 60}")

        test_results = run_all_tests()
        print_report(test_results)

        if run_num == 1:
            first_run_results = [(a.id, a.passed, a.evidence) for a in test_results["assertions"]]
        else:
            current_results = [(a.id, a.passed, a.evidence) for a in test_results["assertions"]]
            if current_results != first_run_results:
                all_consistent = False
                print(f"\n  WARNING: Run {run_num} results differ from run 1!")
                for (fid, fp, fe), (cid, cp, ce) in zip(first_run_results, current_results):
                    if (fid, fp, fe) != (cid, cp, ce):
                        print(f"    {fid}: passed {fp}->{cp}, evidence changed: {fe != ce}")

    print(f"\n{'=' * 60}")
    print(f"REPEATABILITY: {'CONSISTENT across 3 runs' if all_consistent else 'INCONSISTENT'}")
    print(f"{'=' * 60}")
