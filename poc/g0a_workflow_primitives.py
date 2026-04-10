"""
G0-A: Workflow/Step/Loop Primitives Validation PoC

Validates Agno 2.5.14 Workflow, Step, Loop, Condition, and Parallel primitives
for Orchestra's 5-stage workflow requirements.

Environment:
- agno==2.5.14
- Python 3.14
- No LLM API keys required (pure function steps)

Assertions:
  [A1] Linear step chain: previous_step_content passes correctly between consecutive steps
  [A2] Loop(max=2): runs exactly 2 iterations, exits normally
  [A3] Loop exit: downstream step receives last iteration's output (not stale)
  [A4] Utility step: previous_step_content only contains immediate predecessor output
  [A5] get_step_content(): retrieves named step output across intermediate steps
  [A6] previous_step_outputs: dict contains all prior step outputs indexed by name
  [A7] Loop conditional exit via end_condition callable: exits before max_iterations
  [A8] Loop conditional exit via StepOutput(stop=True): exits before max_iterations
  [A9] Step returns None/empty: downstream previous_step_content behavior
  [A10] Loop step exception: error propagation and iteration identification

Design Deltas (vs DESIGN.md v4.4):
  Recorded inline as [DELTA-nn] when discovered.
"""

import sys
import traceback
from dataclasses import dataclass
from typing import Any

from agno.workflow import Workflow, Step, Loop
from agno.workflow.types import StepInput, StepOutput


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
    observational: bool = False  # True = behavior documentation, not a strong pass/fail


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
# Test A1 + A4 + A5 + A6: Linear chain with utility step
# ---------------------------------------------------------------------------

def test_linear_chain_and_utility_step() -> None:
    """
    Workflow: step_a -> step_b (utility) -> step_c
    Validates:
      A1: step_b sees step_a's output via previous_step_content
      A4: step_c sees step_b's output (NOT step_a's) via previous_step_content
      A5: step_c can retrieve step_a's output via get_step_content("step_a")
      A6: step_c's previous_step_outputs contains both step_a and step_b
    """
    log("\n=== Test: Linear Chain + Utility Step ===")

    def step_a(step_input: StepInput) -> StepOutput:
        log("  step_a executing")
        return StepOutput(content="design_document_v1")

    def step_b_utility(step_input: StepInput) -> StepOutput:
        log(f"  step_b_utility executing, prev={step_input.previous_step_content!r}")
        # A1: check previous_step_content from step_a
        prev = step_input.previous_step_content
        record("A1", "previous_step_content passes between consecutive steps",
               prev == "design_document_v1",
               f"step_b received previous_step_content={prev!r}, expected 'design_document_v1'")
        return StepOutput(content="utility_status_ok")

    def step_c(step_input: StepInput) -> StepOutput:
        log(f"  step_c executing, prev={step_input.previous_step_content!r}")

        prev = step_input.previous_step_content
        # A4: previous_step_content is ONLY from step_b, not step_a
        record("A4", "previous_step_content only contains immediate predecessor output",
               prev == "utility_status_ok",
               f"step_c received previous_step_content={prev!r}, expected 'utility_status_ok' (not 'design_document_v1')")

        # A5: get_step_content retrieves step_a output across intermediate step
        step_a_content = step_input.get_step_content("step_a")
        record("A5", "get_step_content() retrieves named step output across intermediate steps",
               step_a_content == "design_document_v1",
               f"get_step_content('step_a')={step_a_content!r}, expected 'design_document_v1'")

        # A6: previous_step_outputs dict contains all prior steps
        prev_outputs = step_input.previous_step_outputs
        prev_keys = set(prev_outputs.keys()) if prev_outputs else set()
        has_both = "step_a" in prev_keys and "step_b_utility" in prev_keys
        record("A6", "previous_step_outputs contains all prior step outputs indexed by name",
               has_both,
               f"previous_step_outputs keys={prev_keys}, expected to contain 'step_a' and 'step_b_utility'")

        return StepOutput(content="final_result")

    wf = Workflow(
        name="test_linear_chain",
        steps=[
            Step(executor=step_a, name="step_a"),
            Step(executor=step_b_utility, name="step_b_utility"),
            Step(executor=step_c, name="step_c"),
        ],
    )
    output = wf.run("start")
    log(f"  Workflow completed. Output content: {output.content!r}")


# ---------------------------------------------------------------------------
# Test A2 + A3: Loop with max_iterations
# ---------------------------------------------------------------------------

def test_loop_max_iterations() -> None:
    """
    Loop(max_iterations=2) with a single step inside.
    Validates:
      A2: Loop runs exactly 2 iterations
      A3: Downstream step after loop receives last iteration's result
    """
    log("\n=== Test: Loop max_iterations ===")
    iteration_count = 0

    def loop_step(step_input: StepInput) -> StepOutput:
        nonlocal iteration_count
        iteration_count += 1
        log(f"  loop_step iteration {iteration_count}")
        return StepOutput(content=f"iter_{iteration_count}")

    def post_loop_step(step_input: StepInput) -> StepOutput:
        prev = step_input.previous_step_content
        log(f"  post_loop_step, prev={prev!r}")

        # A3: downstream sees last iteration result
        record("A3", "Loop exit: downstream step receives last iteration's output",
               prev == "iter_2",
               f"post_loop received previous_step_content={prev!r}, expected 'iter_2'")
        return StepOutput(content="post_loop_done")

    wf = Workflow(
        name="test_loop_max",
        steps=[
            Loop(
                name="test_loop",
                steps=[Step(executor=loop_step, name="loop_step")],
                max_iterations=2,
            ),
            Step(executor=post_loop_step, name="post_loop_step"),
        ],
    )
    output = wf.run("start")

    record("A2", "Loop(max=2) runs exactly 2 iterations and exits normally",
           iteration_count == 2,
           f"iteration_count={iteration_count}, expected 2")
    log(f"  Workflow completed. Output: {output.content!r}")


# ---------------------------------------------------------------------------
# Test A7: Loop conditional exit via end_condition callable
# ---------------------------------------------------------------------------

def test_loop_end_condition_callable() -> None:
    """
    Loop with max_iterations=5 but end_condition exits after 2 iterations.
    Validates A7: end_condition callable can exit loop early.
    """
    log("\n=== Test: Loop end_condition callable ===")
    iteration_count = 0

    def loop_step(step_input: StepInput) -> StepOutput:
        nonlocal iteration_count
        iteration_count += 1
        log(f"  loop_step iteration {iteration_count}")
        if iteration_count >= 2:
            return StepOutput(content="ALL_APPROVED")
        return StepOutput(content="NEEDS_REVISION")

    def check_approved(step_outputs: list[StepOutput]) -> bool:
        """end_condition: return True to stop loop."""
        last = step_outputs[-1] if step_outputs else None
        result = last is not None and last.content == "ALL_APPROVED"
        log(f"  end_condition check: last_content={last.content if last else None!r}, stop={result}")
        return result

    wf = Workflow(
        name="test_end_condition",
        steps=[
            Loop(
                name="review_loop",
                steps=[Step(executor=loop_step, name="review_step")],
                max_iterations=5,
                end_condition=check_approved,
            ),
        ],
    )
    output = wf.run("start")

    record("A7", "Loop conditional exit via end_condition callable exits before max_iterations",
           iteration_count == 2,
           f"iteration_count={iteration_count}, expected 2 (max was 5)")
    log(f"  Workflow completed. Output: {output.content!r}")


# ---------------------------------------------------------------------------
# Test A8: Loop conditional exit via StepOutput(stop=True)
# ---------------------------------------------------------------------------

def test_loop_stop_output() -> None:
    """
    Loop step returns StepOutput(stop=True) on iteration 2.
    A post-loop sentinel step verifies whether stop=True also terminates
    the entire workflow (not just the loop).
    Validates:
      A8: StepOutput(stop=True) terminates the loop before max_iterations
      A8b: StepOutput(stop=True) also terminates the entire workflow (DELTA-03 evidence)
    """
    log("\n=== Test: Loop StepOutput(stop=True) + Sentinel ===")
    iteration_count = 0
    sentinel_executed = False

    def loop_step(step_input: StepInput) -> StepOutput:
        nonlocal iteration_count
        iteration_count += 1
        log(f"  loop_step iteration {iteration_count}")
        if iteration_count >= 2:
            return StepOutput(content="STOP_SIGNAL", stop=True)
        return StepOutput(content=f"iter_{iteration_count}")

    def sentinel_step(step_input: StepInput) -> StepOutput:
        nonlocal sentinel_executed
        sentinel_executed = True
        log("  sentinel_step executed (should NOT happen if stop=True terminates workflow)")
        return StepOutput(content="sentinel_reached")

    wf = Workflow(
        name="test_stop_output",
        steps=[
            Loop(
                name="stop_loop",
                steps=[Step(executor=loop_step, name="stop_step")],
                max_iterations=5,
            ),
            Step(executor=sentinel_step, name="sentinel_step"),
        ],
    )
    output = wf.run("start")

    record("A8", "Loop conditional exit via StepOutput(stop=True) exits before max_iterations",
           iteration_count == 2,
           f"iteration_count={iteration_count}, expected 2 (max was 5)")

    # DELTA-03 evidence: does stop=True also skip the post-loop sentinel?
    record("A8b", "StepOutput(stop=True) terminates entire workflow, not just loop (DELTA-03)",
           not sentinel_executed,
           f"sentinel_executed={sentinel_executed}. "
           f"{'Post-loop step was SKIPPED — stop=True terminates entire workflow' if not sentinel_executed else 'Post-loop step WAS executed — stop=True only exits loop'}",
           design_delta="DELTA-03: StepOutput(stop=True) terminates entire Workflow. "
                        "Review loops MUST use end_condition callable, not stop=True." if not sentinel_executed else "")
    log(f"  Workflow completed. Output: {output.content!r}")


# ---------------------------------------------------------------------------
# Test A9: Step returns None/empty string
# ---------------------------------------------------------------------------

def test_none_and_empty_output() -> None:
    """
    Test behavior when step returns None content or empty string.
    Validates A9: downstream behavior is predictable.
    """
    log("\n=== Test: None/Empty Step Output ===")

    def step_none(step_input: StepInput) -> StepOutput:
        log("  step_none executing")
        return StepOutput(content=None)

    def step_after_none(step_input: StepInput) -> StepOutput:
        prev = step_input.previous_step_content
        log(f"  step_after_none, prev={prev!r}, type={type(prev).__name__}")
        # Record whatever behavior we observe
        record("A9", "Step returns None: downstream previous_step_content behavior documented",
               True,  # observational — we record the behavior
               f"After None-content step: previous_step_content={prev!r} (type={type(prev).__name__}). "
               f"Behavior is {'None passthrough' if prev is None else 'empty string' if prev == '' else 'other: ' + repr(prev)}",
               observational=True)
        return StepOutput(content="after_none_done")

    wf = Workflow(
        name="test_none_output",
        steps=[
            Step(executor=step_none, name="step_none"),
            Step(executor=step_after_none, name="step_after_none"),
        ],
    )
    output = wf.run("start")
    log(f"  Workflow completed. Output: {output.content!r}")


# ---------------------------------------------------------------------------
# Test A10: Loop step exception handling
# ---------------------------------------------------------------------------

def test_loop_exception() -> None:
    """
    Loop step raises exception on iteration 2.
    Validates A10: error can be attributed to specific iteration.
    """
    log("\n=== Test: Loop Step Exception ===")
    iteration_count = 0

    def failing_step(step_input: StepInput) -> StepOutput:
        nonlocal iteration_count
        iteration_count += 1
        log(f"  failing_step iteration {iteration_count}")
        if iteration_count == 2:
            raise ValueError(f"Deliberate failure at iteration {iteration_count}")
        return StepOutput(content=f"ok_{iteration_count}")

    wf = Workflow(
        name="test_loop_exception",
        steps=[
            Loop(
                name="failing_loop",
                steps=[Step(executor=failing_step, name="failing_step")],
                max_iterations=3,
            ),
        ],
    )

    try:
        output = wf.run("start")
        # If we get here, the framework may have handled the error (skip/retry)
        error_msg = None
        log(f"  Workflow completed without raising. Output: {output.content!r}")
        # Check if output indicates failure
        has_error_info = output.content is not None and ("error" in str(output.content).lower()
                                                          or "fail" in str(output.content).lower())
        record("A10", "Loop step exception: error propagation documented",
               True,  # observational
               f"Exception was handled by framework (not raised to caller). "
               f"Output={output.content!r}. "
               f"Iterations completed: {iteration_count}. "
               f"Framework default on_error behavior applies.",
               observational=True)
    except Exception as e:
        error_msg = str(e)
        tb = traceback.format_exc()
        log(f"  Exception caught: {error_msg}")
        # Check if we can identify the iteration
        identifies_iteration = "iteration 2" in error_msg or "failing_step" in tb
        record("A10", "Loop step exception: error propagation documented",
               True,  # observational
               f"Exception raised to caller: {type(e).__name__}: {error_msg}. "
               f"Iteration identifiable from error: {identifies_iteration}. "
               f"Iterations completed before error: {iteration_count}",
               observational=True)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_all_tests() -> dict[str, Any]:
    """Run all tests and return structured results."""
    global results, run_log
    results = []
    run_log = []

    log("=" * 60)
    log("G0-A: Workflow/Step/Loop Primitives Validation")
    log("=" * 60)

    tests = [
        ("Linear Chain + Utility Step (A1, A4, A5, A6)", test_linear_chain_and_utility_step),
        ("Loop max_iterations (A2, A3)", test_loop_max_iterations),
        ("Loop end_condition callable (A7)", test_loop_end_condition_callable),
        ("Loop StepOutput(stop=True) (A8)", test_loop_stop_output),
        ("None/Empty Output (A9)", test_none_and_empty_output),
        ("Loop Exception (A10)", test_loop_exception),
    ]

    for test_name, test_fn in tests:
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
    print("G0-A VALIDATION REPORT")
    print("=" * 60)

    # Environment
    import agno
    print(f"\nEnvironment:")
    print(f"  agno version: {agno.__version__}")
    print(f"  Python: {sys.version}")

    # Separate strong assertions from observational
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

    # Design deltas summary
    deltas = [a for a in assertions if a.design_delta]
    if deltas:
        print(f"\nDesign Deltas ({len(deltas)}):")
        for a in deltas:
            print(f"  [{a.id}] {a.design_delta}")

    # Conclusion
    blocking_failures = [a for a in strong if not a.passed]

    print(f"\nConclusion:")
    if not blocking_failures:
        print(f"  SUPPORTED — {strong_passed}/{len(strong)} strong assertions pass. "
              f"{len(obs)} observational items document framework behavior (not counted as pass/fail).")
    else:
        failed_ids = [a.id for a in blocking_failures]
        print(f"  PARTIALLY_SUPPORTED — Blocking failures: {failed_ids}")

    # Run log
    print(f"\n{'=' * 60}")
    print("RUN LOG")
    print("=" * 60)
    for line in run_log_lines:
        print(line)


if __name__ == "__main__":
    print("Running G0-A validation (run 1/3)...")
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
    print(f"REPEATABILITY: {'CONSISTENT across 3 runs' if all_consistent else 'INCONSISTENT — results varied between runs'}")
    print(f"{'=' * 60}")
