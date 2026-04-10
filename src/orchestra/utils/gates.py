"""Gate step factory functions (AC-02, AC-04).

Provides two factory functions that enforce Gate 0 architectural constraints:

- create_gate_check_step(): For Loop-internal check steps.
  Forces on_error=OnError.fail (AC-02).

- create_decision_gate_step(): For post-Loop Decision Gate steps.
  Forces on_error=OnError.fail (AC-02) + requires_confirmation=True +
  on_reject=OnReject.cancel (AC-04).
"""

from __future__ import annotations

from typing import Any, Callable

from agno.workflow.step import Step
from agno.workflow.types import OnError, OnReject, StepInput, StepOutput


def create_gate_check_step(
    name: str,
    executor: Callable[[StepInput], StepOutput],
    **kwargs: Any,
) -> Step:
    """Create a gate check Step with enforced on_error=OnError.fail (AC-02).

    Use this for check steps inside or after a Loop (e.g., plan_gate_check,
    design_gate_check). These steps evaluate pass/fail but do NOT trigger
    human confirmation.

    Raises ValueError if caller tries to override on_error to a non-fail value.
    """
    if "on_error" in kwargs and kwargs["on_error"] != OnError.fail:
        raise ValueError(
            f"Gate check step '{name}' must use on_error=OnError.fail (AC-02). "
            f"Got: {kwargs['on_error']}"
        )
    if kwargs.get("requires_confirmation"):
        raise ValueError(
            f"Gate check step '{name}' does not accept requires_confirmation=True. "
            "Use create_decision_gate_step() for post-Loop Decision Gates (AC-04)."
        )
    if "on_reject" in kwargs:
        raise ValueError(
            f"Gate check step '{name}' does not accept on_reject. "
            "Use create_decision_gate_step() for post-Loop Decision Gates (AC-04)."
        )
    kwargs["on_error"] = OnError.fail
    kwargs.setdefault("requires_confirmation", False)
    return Step(name=name, executor=executor, **kwargs)


def create_decision_gate_step(
    name: str,
    executor: Callable[[StepInput], StepOutput],
    **kwargs: Any,
) -> Step:
    """Create a Decision Gate Step with enforced HITL constraints (AC-02, AC-04).

    Use this for post-Loop Decision Gate steps that require human confirmation
    before the workflow can proceed. Enforces:
    - on_error=OnError.fail (AC-02)
    - requires_confirmation=True (AC-04)
    - on_reject=OnReject.cancel (AC-04)

    Raises ValueError if caller tries to override any enforced parameter.
    """
    enforced = {
        "on_error": OnError.fail,
        "requires_confirmation": True,
        "on_reject": OnReject.cancel,
    }
    for key, required_val in enforced.items():
        if key in kwargs and kwargs[key] != required_val:
            raise ValueError(
                f"Decision gate step '{name}' must use {key}={required_val!r} "
                f"(AC-02/AC-04). Got: {kwargs[key]!r}"
            )
    kwargs.update(enforced)
    return Step(name=name, executor=executor, **kwargs)
