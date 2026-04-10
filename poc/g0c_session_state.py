"""
G0-C: session_data Cross-Step Transmission Verification PoC

Validates that Agno's workflow_session.session_data["session_state"] can reliably
transmit state across steps, through loops, and between independent workflow runs.

Environment:
  - agno 2.5.14
  - Python 3.14
  - No LLM / API key required (pure function steps)

Exit Criteria (Blocking):
  B1. Basic transmission — Step 1 writes, Step 5 reads
  B2. Cumulative writes — Each step adds a key, final step sees all
  B3. Override semantics — Step B overwrites Step A's key, downstream reads B's value
  B4. Loop inner→outer visibility — Loop modifies state, post-loop step reads latest
  B5. Nested dict integrity — Nested structure survives cross-step transmission
  B6. Type preservation — int/float/bool/list/None types survive cross-step
  B7. Medium payload — ~100 keys, no truncation
  B8. Cross-run isolation — Two workflow runs have independent session_state
  B9. Missing key behavior — Reading absent key is stable and predictable
  B10. Nested dict in-place mutation — Mutating nested value visible in next step

Exploratory (non-blocking):
  E1. Parallel step write behavior — deterministic? silent overwrite? conflict?

Design Delta:
  - DESIGN.md says `step_input.session_state["key"]`, actual API is
    `step_input.workflow_session.session_data["session_state"]["key"]`
"""

from __future__ import annotations

import platform
import sys
import traceback
from dataclasses import dataclass
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Agno imports
# ---------------------------------------------------------------------------
from agno.workflow import Workflow, Step, Loop
from agno.workflow.types import StepInput, StepOutput


# ===========================================================================
# Helper: session_state accessor
# ===========================================================================
def _ss(si: StepInput) -> Dict[str, Any]:
    """Shorthand: get the session_state dict from a StepInput."""
    assert si.workflow_session is not None, "workflow_session is None"
    sd = si.workflow_session.session_data
    assert sd is not None, "session_data is None"
    if "session_state" not in sd:
        sd["session_state"] = {}
    return sd["session_state"]


# ===========================================================================
# Assertion collector
# ===========================================================================
@dataclass
class Assertion:
    id: str
    description: str
    passed: bool
    detail: str = ""


results: List[Assertion] = []


def assert_check(id: str, description: str, condition: bool, detail: str = "") -> None:
    results.append(Assertion(id=id, description=description, passed=condition, detail=detail))
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {id}: {description}" + (f" — {detail}" if detail else ""))


# ===========================================================================
# B1–B3: Basic transmission, cumulative writes, override semantics
# ===========================================================================
def test_b1_b2_b3() -> None:
    print("\n=== B1/B2/B3: Basic transmission, cumulative, override ===")

    def step1(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["key_from_step1"] = "value1"
        ss["overridable"] = "original"
        return StepOutput(content="step1 done")

    def step2(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["key_from_step2"] = "value2"
        ss["overridable"] = "overridden_by_step2"
        return StepOutput(content="step2 done")

    def step3(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["key_from_step3"] = "value3"
        return StepOutput(content="step3 done")

    def step_final(si: StepInput) -> StepOutput:
        ss = _ss(si)
        # B1: basic transmission
        assert_check("B1", "Basic transmission (step1→final)", ss.get("key_from_step1") == "value1",
                      f"got {ss.get('key_from_step1')!r}")
        # B2: cumulative
        all_present = all(f"key_from_step{i}" in ss for i in range(1, 4))
        assert_check("B2", "Cumulative writes (3 keys present)", all_present,
                      f"keys={list(ss.keys())}")
        # B3: override
        assert_check("B3", "Override semantics (step2 overwrites step1)",
                      ss.get("overridable") == "overridden_by_step2",
                      f"got {ss.get('overridable')!r}")
        return StepOutput(content="final done")

    wf = Workflow(
        name="b1_b2_b3",
        steps=[
            Step(name="s1", executor=step1),
            Step(name="s2", executor=step2),
            Step(name="s3", executor=step3),
            Step(name="s_final", executor=step_final),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# B4: Loop inner → outer visibility
# ===========================================================================
def test_b4() -> None:
    print("\n=== B4: Loop inner→outer visibility (max_iterations exit) ===")

    iteration_count = 0

    def loop_step(si: StepInput) -> StepOutput:
        nonlocal iteration_count
        ss = _ss(si)
        iteration_count += 1
        ss["loop_counter"] = iteration_count
        ss[f"loop_iter_{iteration_count}"] = f"data_{iteration_count}"
        return StepOutput(content=f"loop iter {iteration_count}")

    def post_loop(si: StepInput) -> StepOutput:
        ss = _ss(si)
        assert_check("B4a", "Loop counter visible after loop", ss.get("loop_counter") == 2,
                      f"got {ss.get('loop_counter')!r}")
        assert_check("B4b", "Loop iter 1 data visible", ss.get("loop_iter_1") == "data_1",
                      f"got {ss.get('loop_iter_1')!r}")
        assert_check("B4c", "Loop iter 2 data visible", ss.get("loop_iter_2") == "data_2",
                      f"got {ss.get('loop_iter_2')!r}")
        return StepOutput(content="post loop done")

    wf = Workflow(
        name="b4_loop",
        steps=[
            Loop(
                name="test_loop",
                steps=[Step(name="loop_body", executor=loop_step)],
                max_iterations=2,  # exit via max_iterations
            ),
            Step(name="post_loop", executor=post_loop),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# B5: Nested dict integrity
# ===========================================================================
def test_b5() -> None:
    print("\n=== B5: Nested dict integrity ===")

    def write_nested(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["config"] = {
            "db": {"wal": True, "timeout": 5000},
            "agents": ["scout", "architect"],
        }
        return StepOutput(content="nested written")

    def read_nested(si: StepInput) -> StepOutput:
        ss = _ss(si)
        cfg = ss.get("config")
        assert_check("B5a", "Nested dict exists", cfg is not None)
        if cfg:
            assert_check("B5b", "Nested db.wal is True", cfg.get("db", {}).get("wal") is True,
                          f"got {cfg.get('db', {}).get('wal')!r}")
            assert_check("B5c", "Nested db.timeout is int 5000",
                          cfg.get("db", {}).get("timeout") == 5000,
                          f"got {cfg.get('db', {}).get('timeout')!r}")
            assert_check("B5d", "Nested agents list intact",
                          cfg.get("agents") == ["scout", "architect"],
                          f"got {cfg.get('agents')!r}")
        return StepOutput(content="nested verified")

    wf = Workflow(
        name="b5_nested",
        steps=[
            Step(name="write", executor=write_nested),
            Step(name="read", executor=read_nested),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# B6: Type preservation
# ===========================================================================
def test_b6() -> None:
    print("\n=== B6: Type preservation ===")

    def write_types(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["t_int"] = 42
        ss["t_float"] = 3.14
        ss["t_bool_true"] = True
        ss["t_bool_false"] = False
        ss["t_list"] = [1, "two", 3.0]
        ss["t_none"] = None
        return StepOutput(content="types written")

    def check_types(si: StepInput) -> StepOutput:
        ss = _ss(si)
        assert_check("B6a", "int preserved", type(ss.get("t_int")) is int,
                      f"type={type(ss.get('t_int')).__name__}")
        assert_check("B6b", "float preserved", type(ss.get("t_float")) is float,
                      f"type={type(ss.get('t_float')).__name__}")
        assert_check("B6c", "bool True preserved", ss.get("t_bool_true") is True,
                      f"val={ss.get('t_bool_true')!r}")
        assert_check("B6d", "bool False preserved", ss.get("t_bool_false") is False,
                      f"val={ss.get('t_bool_false')!r}")
        assert_check("B6e", "list preserved", ss.get("t_list") == [1, "two", 3.0],
                      f"val={ss.get('t_list')!r}")
        assert_check("B6f", "None preserved", ss.get("t_none") is None,
                      f"val={ss.get('t_none')!r}")
        return StepOutput(content="types verified")

    wf = Workflow(
        name="b6_types",
        steps=[
            Step(name="write", executor=write_types),
            Step(name="check", executor=check_types),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# B7: Medium payload (~100 keys)
# ===========================================================================
def test_b7() -> None:
    print("\n=== B7: Medium payload ===")

    def write_payload(si: StepInput) -> StepOutput:
        ss = _ss(si)
        for i in range(100):
            ss[f"payload_key_{i}"] = {"index": i, "data": f"value_{i}" * 10}
        return StepOutput(content="payload written")

    def check_payload(si: StepInput) -> StepOutput:
        ss = _ss(si)
        count = sum(1 for k in ss if k.startswith("payload_key_"))
        assert_check("B7a", "100 payload keys present", count == 100, f"count={count}")
        # Spot check first and last
        first = ss.get("payload_key_0")
        last = ss.get("payload_key_99")
        assert_check("B7b", "First payload key intact",
                      first == {"index": 0, "data": "value_0" * 10},
                      f"got index={first.get('index') if first else None}")
        assert_check("B7c", "Last payload key intact",
                      last == {"index": 99, "data": "value_99" * 10},
                      f"got index={last.get('index') if last else None}")
        return StepOutput(content="payload verified")

    wf = Workflow(
        name="b7_payload",
        steps=[
            Step(name="write", executor=write_payload),
            Step(name="check", executor=check_payload),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# B8: Cross-run isolation
# ===========================================================================
def test_b8() -> None:
    print("\n=== B8: Cross-run isolation ===")

    # Use captured state from within steps to verify isolation
    captured: Dict[str, Dict[str, Any]] = {}

    def capture_write_marker(run_id: str):
        def _step(si: StepInput) -> StepOutput:
            ss = _ss(si)
            run_marker = ss.get("_marker", "unset")
            ss["seen_marker"] = run_marker
            ss["unique_to_run"] = run_id
            # Capture a snapshot
            captured[run_id] = dict(ss)
            return StepOutput(content=f"marker={run_marker}")
        return _step

    # Run 1: init with marker "run_A"
    wf1 = Workflow(
        name="b8_isolation_1",
        session_state={"_marker": "run_A"},
        steps=[
            Step(name="write", executor=capture_write_marker("run_A")),
        ],
    )
    wf1.run(input="go")

    # Run 2: different workflow instance with marker "run_B"
    wf2 = Workflow(
        name="b8_isolation_2",
        session_state={"_marker": "run_B"},
        steps=[
            Step(name="write", executor=capture_write_marker("run_B")),
        ],
    )
    wf2.run(input="go")

    # Verify isolation from captured snapshots
    ss1 = captured.get("run_A", {})
    ss2 = captured.get("run_B", {})

    assert_check("B8a", "Run 1 saw marker run_A", ss1.get("seen_marker") == "run_A",
                  f"got {ss1.get('seen_marker')!r}")
    assert_check("B8b", "Run 2 saw marker run_B", ss2.get("seen_marker") == "run_B",
                  f"got {ss2.get('seen_marker')!r}")
    assert_check("B8c", "Run 1 does not contain run_B's unique key",
                  ss1.get("unique_to_run") == "run_A",
                  f"got {ss1.get('unique_to_run')!r}")
    assert_check("B8d", "Run 2 does not contain run_A's marker",
                  ss2.get("_marker") == "run_B",
                  f"got {ss2.get('_marker')!r}")


# ===========================================================================
# B9: Missing key behavior
# ===========================================================================
def test_b9() -> None:
    print("\n=== B9: Missing key behavior ===")

    def read_missing(si: StepInput) -> StepOutput:
        ss = _ss(si)
        # dict.get() for missing key
        val_get = ss.get("nonexistent_key")
        assert_check("B9a", "dict.get() returns None for missing key", val_get is None,
                      f"got {val_get!r}")

        # "in" check for missing key
        assert_check("B9b", "'nonexistent' not in session_state", "nonexistent_key" not in ss)

        # KeyError for direct access
        raised_keyerror = False
        try:
            _ = ss["nonexistent_key"]
        except KeyError:
            raised_keyerror = True
        assert_check("B9c", "Direct access raises KeyError", raised_keyerror)

        return StepOutput(content="missing key verified")

    wf = Workflow(
        name="b9_missing",
        steps=[Step(name="check", executor=read_missing)],
    )
    wf.run(input="go")


# ===========================================================================
# B10: Nested dict in-place mutation
# ===========================================================================
def test_b10() -> None:
    print("\n=== B10: Nested dict in-place mutation ===")

    def write_nested(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["config"] = {"db": {"wal": True, "timeout": 5000}}
        return StepOutput(content="initial nested written")

    def mutate_nested(si: StepInput) -> StepOutput:
        ss = _ss(si)
        # In-place mutation of nested value (not reassigning the top-level key)
        ss["config"]["db"]["wal"] = False
        ss["config"]["db"]["new_key"] = "added"
        return StepOutput(content="mutated in-place")

    def verify_mutation(si: StepInput) -> StepOutput:
        ss = _ss(si)
        cfg = ss.get("config", {}).get("db", {})
        assert_check("B10a", "In-place mutation: wal changed to False",
                      cfg.get("wal") is False, f"got {cfg.get('wal')!r}")
        assert_check("B10b", "In-place mutation: new_key added",
                      cfg.get("new_key") == "added", f"got {cfg.get('new_key')!r}")
        assert_check("B10c", "In-place mutation: timeout unchanged",
                      cfg.get("timeout") == 5000, f"got {cfg.get('timeout')!r}")
        return StepOutput(content="mutation verified")

    wf = Workflow(
        name="b10_mutate",
        steps=[
            Step(name="write", executor=write_nested),
            Step(name="mutate", executor=mutate_nested),
            Step(name="verify", executor=verify_mutation),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# E1: Exploratory — Parallel step write behavior
# ===========================================================================
def test_e1() -> None:
    print("\n=== E1 (Exploratory): Parallel step write behavior ===")

    try:
        from agno.workflow import Parallel

        def parallel_a(si: StepInput) -> StepOutput:
            ss = _ss(si)
            ss["parallel_a"] = "from_a"
            ss["shared_key"] = "written_by_a"
            return StepOutput(content="parallel a done")

        def parallel_b(si: StepInput) -> StepOutput:
            ss = _ss(si)
            ss["parallel_b"] = "from_b"
            ss["shared_key"] = "written_by_b"
            return StepOutput(content="parallel b done")

        def check_parallel(si: StepInput) -> StepOutput:
            ss = _ss(si)
            a_present = "parallel_a" in ss
            b_present = "parallel_b" in ss
            shared_val = ss.get("shared_key")

            print(f"    [INFO] parallel_a present: {a_present}")
            print(f"    [INFO] parallel_b present: {b_present}")
            print(f"    [INFO] shared_key value: {shared_val!r}")
            print(f"    [INFO] Both independent keys present: {a_present and b_present}")

            # Record observations (not pass/fail since this is exploratory)
            assert_check("E1a", "[Exploratory] Both parallel branches wrote independent keys",
                          a_present and b_present,
                          f"a={a_present}, b={b_present}")
            assert_check("E1b", "[Exploratory] shared_key has deterministic value",
                          shared_val in ("written_by_a", "written_by_b"),
                          f"val={shared_val!r} — last-writer-wins semantics")

            return StepOutput(content=f"parallel check: shared={shared_val}")

        wf = Workflow(
            name="e1_parallel",
            steps=[
                Parallel(
                    "parallel_writes",
                    Step(name="branch_a", executor=parallel_a),
                    Step(name="branch_b", executor=parallel_b),
                ),
                Step(name="check", executor=check_parallel),
            ],
        )
        wf.run(input="go")

    except Exception as e:
        print(f"    [INFO] Parallel test encountered exception: {type(e).__name__}: {e}")
        assert_check("E1", "[Exploratory] Parallel write behavior",
                      False, f"Exception: {type(e).__name__}: {e}")


# ===========================================================================
# B4 supplement: Loop with end_condition callback
# ===========================================================================
def test_b4_end_condition() -> None:
    print("\n=== B4 supplement: Loop end_condition callback ===")

    counter = {"val": 0}

    def loop_body(si: StepInput) -> StepOutput:
        ss = _ss(si)
        counter["val"] += 1
        ss["end_cond_counter"] = counter["val"]
        return StepOutput(content=f"iter {counter['val']}")

    def should_stop(step_outputs: list) -> bool:
        # end_condition receives List[StepOutput] from current iteration
        # We check the counter via the last step's content
        if step_outputs:
            last = step_outputs[-1]
            content = str(getattr(last, "content", ""))
            # Parse iteration number from content "iter N"
            if "iter" in content:
                try:
                    n = int(content.split()[-1])
                    return n >= 2
                except (ValueError, IndexError):
                    pass
        return False

    def post_loop(si: StepInput) -> StepOutput:
        ss = _ss(si)
        assert_check("B4d", "end_condition stopped loop at counter=2",
                      ss.get("end_cond_counter") == 2,
                      f"got {ss.get('end_cond_counter')!r}")
        return StepOutput(content="end_condition verified")

    try:
        wf = Workflow(
            name="b4_end_cond",
            steps=[
                Loop(
                    name="cond_loop",
                    steps=[Step(name="body", executor=loop_body)],
                    max_iterations=10,
                    end_condition=should_stop,
                ),
                Step(name="post", executor=post_loop),
            ],
        )
        wf.run(input="go")
    except Exception as e:
        print(f"    [INFO] end_condition test failed: {type(e).__name__}: {e}")
        assert_check("B4d", "end_condition callback", False,
                      f"Exception: {type(e).__name__}: {e}")


# ===========================================================================
# Failure path: B-F1 — Workflow with empty initial session_state
# ===========================================================================
def test_failure_empty_init() -> None:
    print("\n=== Failure path: Empty initial session_state ===")

    def write_to_empty(si: StepInput) -> StepOutput:
        ss = _ss(si)
        ss["added_to_empty"] = "works"
        return StepOutput(content="wrote to initially empty")

    def verify(si: StepInput) -> StepOutput:
        ss = _ss(si)
        assert_check("BF1", "Write to initially empty session_state succeeds",
                      ss.get("added_to_empty") == "works",
                      f"got {ss.get('added_to_empty')!r}")
        return StepOutput(content="verified")

    # No session_state passed — should auto-init to {}
    wf = Workflow(
        name="bf1_empty",
        steps=[
            Step(name="write", executor=write_to_empty),
            Step(name="verify", executor=verify),
        ],
    )
    wf.run(input="go")


# ===========================================================================
# B11: Deepcopy proof — initial session_state is deepcopied
# ===========================================================================
def test_b11() -> None:
    print("\n=== B11: Deepcopy proof ===")

    original = {"marker": "original", "nested": {"inner": "before"}}

    captured_ss = {}

    def read_state(si: StepInput) -> StepOutput:
        ss = _ss(si)
        captured_ss["runtime"] = ss
        return StepOutput(content="read done")

    wf = Workflow(
        name="b11_deepcopy",
        session_state=original,
        steps=[Step(name="read", executor=read_state)],
    )
    wf.run(input="go")

    runtime_ss = captured_ss["runtime"]

    # 1. Runtime state should have the same values
    assert_check("B11a", "Runtime state has original values",
                  runtime_ss.get("marker") == "original",
                  f"got {runtime_ss.get('marker')!r}")

    # 2. Mutating the original dict AFTER workflow creation should NOT affect runtime state
    original["marker"] = "mutated_after_init"
    original["nested"]["inner"] = "mutated_after_init"
    assert_check("B11b", "Post-init mutation of original dict does not affect runtime",
                  runtime_ss.get("marker") == "original",
                  f"got {runtime_ss.get('marker')!r} (should still be 'original')")

    # 3. Check object identity — runtime state should NOT be the same object
    assert_check("B11c", "Runtime session_state is not the same object as original",
                  runtime_ss is not original,
                  f"id(original)={id(original)}, id(runtime)={id(runtime_ss)}")

    # 4. Nested object identity — deepcopy means nested dicts are also independent
    # Note: original["nested"] was mutated above, runtime should still have "before"
    assert_check("B11d", "Nested dict is also deepcopied (independent of original)",
                  runtime_ss.get("nested", {}).get("inner") == "before",
                  f"got {runtime_ss.get('nested', {}).get('inner')!r}")


# ===========================================================================
# Main — Run all tests, produce summary
# ===========================================================================
def run_all_tests() -> tuple[int, int, int, int, str]:
    # Run all tests
    tests = [
        ("B1/B2/B3", test_b1_b2_b3),
        ("B4 Loop", test_b4),
        ("B4 end_condition", test_b4_end_condition),
        ("B5 Nested dict", test_b5),
        ("B6 Types", test_b6),
        ("B7 Payload", test_b7),
        ("B8 Isolation", test_b8),
        ("B9 Missing key", test_b9),
        ("B10 In-place mutation", test_b10),
        ("BF1 Empty init", test_failure_empty_init),
        ("B11 Deepcopy", test_b11),
        ("E1 Parallel", test_e1),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            print(f"\n  [ERROR] Test {name} raised unhandled exception:")
            traceback.print_exc()
            results.append(Assertion(
                id=f"{name}_CRASH",
                description=f"Test {name} crashed",
                passed=False,
                detail=f"{type(e).__name__}: {e}",
            ))

    # Summary
    blocking = [r for r in results if not r.id.startswith("E")]
    exploratory = [r for r in results if r.id.startswith("E")]

    blocking_pass = sum(1 for r in blocking if r.passed)
    blocking_fail = sum(1 for r in blocking if not r.passed)
    expl_pass = sum(1 for r in exploratory if r.passed)
    expl_fail = sum(1 for r in exploratory if not r.passed)

    # Build normalized digest for repeatability comparison
    # Include detail for evidence-level comparison, but strip non-deterministic
    # parts like memory addresses (id=0x..., id(...)=...) which vary per run.
    import re
    _id_pattern = re.compile(r'id\([^)]*\)=\d+')
    digest = []
    for r in results:
        normalized_detail = _id_pattern.sub('id(...)=ADDR', r.detail)
        digest.append(f"{r.id}:{'PASS' if r.passed else 'FAIL'}:{normalized_detail}")
    digest_str = "|".join(sorted(digest))

    return blocking_pass, blocking_fail, expl_pass, expl_fail, digest_str


def print_summary(blocking_pass: int, blocking_fail: int, expl_pass: int, expl_fail: int) -> None:
    print(f"\nBlocking:     {blocking_pass} passed, {blocking_fail} failed (total {blocking_pass + blocking_fail})")
    print(f"Exploratory:  {expl_pass} passed, {expl_fail} failed (total {expl_pass + expl_fail})")

    if blocking_fail > 0:
        print("\nFailed blocking assertions:")
        for r in results:
            if not r.passed and not r.id.startswith("E"):
                print(f"  - {r.id}: {r.description} — {r.detail}")

    # Conclusion
    print("\n" + "-" * 70)
    if blocking_fail == 0:
        print("CONCLUSION: SUPPORTED")
        print("  session_data['session_state'] reliably transmits state across steps,")
        print("  through loops, with type preservation, nested dict support, and run isolation.")
    elif blocking_fail <= 2:
        print("CONCLUSION: PARTIALLY_SUPPORTED")
        print(f"  {blocking_fail} blocking assertion(s) failed. See details above.")
    else:
        print("CONCLUSION: NOT_SUPPORTED")
        print(f"  {blocking_fail} blocking assertions failed. session_state cannot be relied upon.")

    # Design delta
    print("\n" + "-" * 70)
    print("DESIGN DELTA:")
    print("  1. DESIGN.md uses `step_input.session_state[key]` — actual API is")
    print("     `step_input.workflow_session.session_data['session_state'][key]`")
    print("  2. Workflow(session_state={...}) is deepcopied into session_data on init (proven by B11)")
    print("  3. StepOutput(stop=True) terminates the ENTIRE WORKFLOW, not just the Loop.")
    print("     Review loops MUST use Loop(end_condition=...) for conditional exit.")

    # Exploratory findings
    exploratory = [r for r in results if r.id.startswith("E")]
    if exploratory:
        print("\nEXPLORATORY FINDINGS:")
        for r in exploratory:
            status = "PASS" if r.passed else "OBSERVED"
            print(f"  [{status}] {r.id}: {r.description} — {r.detail}")


def main() -> None:
    """Run all tests 3 times, compare digests for repeatability."""
    print("=" * 70)
    print("G0-C: session_data Cross-Step Transmission PoC")
    print("  (3-run repeatability harness)")
    print("=" * 70)

    # Environment
    try:
        import agno
        agno_version = getattr(agno, "__version__", "unknown")
    except Exception:
        agno_version = "import failed"

    print(f"\nEnvironment:")
    print(f"  agno version: {agno_version}")
    print(f"  Python: {sys.version}")
    print(f"  Platform: {platform.platform()}")

    NUM_RUNS = 3
    digests = []
    last_result = None

    for run_num in range(1, NUM_RUNS + 1):
        print(f"\n{'='*70}")
        print(f"RUN {run_num}/{NUM_RUNS}")
        print(f"{'='*70}")

        results.clear()
        bp, bf, ep, ef, digest = run_all_tests()
        digests.append(digest)
        last_result = (bp, bf, ep, ef)

    # Repeatability check
    print(f"\n{'='*70}")
    print("REPEATABILITY CHECK")
    print(f"{'='*70}")

    all_identical = len(set(digests)) == 1
    print(f"\n  Runs compared: {NUM_RUNS}")
    print(f"  All digests identical: {all_identical}")
    if not all_identical:
        for i, d in enumerate(digests, 1):
            print(f"  Run {i}: {d}")

    assert_check("REP", f"All {NUM_RUNS} runs produce identical results", all_identical,
                  f"unique digests: {len(set(digests))}")

    # Final summary (from last run + repeatability)
    print(f"\n{'='*70}")
    print("FINAL SUMMARY")
    print(f"{'='*70}")

    bp, bf, ep, ef = last_result  # type: ignore
    # Add repeatability assertion to counts
    if all_identical:
        bp += 1
    else:
        bf += 1

    print_summary(bp, bf, ep, ef)

    print("=" * 70)
    sys.exit(0 if bf == 0 else 1)


if __name__ == "__main__":
    main()
