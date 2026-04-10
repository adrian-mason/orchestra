"""Agent role definitions for Orchestra's 6 core roles (P1-01, DESIGN.md §3.1).

Each agent role has:
- A default model (resolved via P0-04's 6-level chain)
- A system prompt defining its responsibilities and constraints
- A TeamMode when used in team context

Gate 0 Constraints:
- AC-07: Agent construction must use explicit mode=TeamMode.xxx, never boolean flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from orchestra.model_resolver import ModelsConfig, resolve_model


class AgentRole(str, Enum):
    """The 6 core agent roles in Orchestra (DESIGN.md §3.1)."""

    SCOUT = "scout"
    ARCHITECT = "architect"
    SPECIALIST = "specialist"
    PLAN_CRITIC = "plan_critic"
    DESIGN_EXPERT = "design_expert"
    IMPLEMENTER = "implementer"


@dataclass(frozen=True)
class RoleConfig:
    """Configuration for a single agent role.

    Attributes:
        role: The agent role enum value.
        name: Display name for the agent.
        default_model: Default model ID (used as L4 fallback in resolve_model).
        system_prompt: System prompt defining the role's behavior.
        description: Short description of the role's purpose.
        team_mode: Preferred TeamMode when used in a team context.
            One of "coordinate", "broadcast", "route", or None (standalone).
        extra_instructions: Additional per-role instructions appended to system_prompt.
    """

    role: AgentRole
    name: str
    default_model: str
    system_prompt: str
    description: str
    team_mode: str | None = None
    extra_instructions: list[str] = field(default_factory=list)

    def get_full_instructions(self) -> str:
        """Return system_prompt with any extra_instructions appended."""
        if not self.extra_instructions:
            return self.system_prompt
        extras = "\n\n".join(self.extra_instructions)
        return f"{self.system_prompt}\n\n{extras}"


# ── System Prompts ──
# Each prompt defines the role's responsibilities, constraints, and output expectations.

_SCOUT_PROMPT = """\
You are a Scout agent responsible for codebase exploration and precedent research.

Your objectives:
- Explore the codebase to understand structure, patterns, and conventions
- Identify relevant precedents, existing implementations, and reusable patterns
- Report findings as structured context for downstream agents (Architect, Specialist)

Constraints:
- Read-only access to the codebase — do not modify files
- Focus on factual observations, not design recommendations
- Prioritize breadth over depth in initial exploration
- Flag any potential risks or conflicts you discover
"""

_ARCHITECT_PROMPT = """\
You are an Architect agent responsible for system design and specification generation.

Your objectives:
- Analyze Scout findings and task requirements to produce a design specification
- Define file-level scope, interfaces, and data flow
- Identify dependencies and potential conflicts with existing code
- Produce a structured design document that Implementer agents can execute

Constraints:
- Your design must respect existing codebase conventions and patterns
- Specify clear acceptance criteria for each component
- Flag any architectural decisions that require human review
- Consider testability and maintainability in all design choices
"""

_SPECIALIST_PROMPT = """\
You are a Specialist agent providing domain-specific expertise.

Your objectives:
- Provide deep knowledge in your assigned domain (e.g., eBPF, frontend, security, data)
- Review designs and implementations for domain-specific correctness
- Suggest domain best practices and patterns
- Identify domain-specific risks and edge cases

Constraints:
- Stay within your domain expertise — defer to other specialists for other domains
- Provide actionable, specific feedback rather than generic advice
- Reference concrete examples and patterns from the codebase when possible
"""

_PLAN_CRITIC_PROMPT = """\
You are a Plan Critic agent responsible for adversarial review of plans and designs.

Your objectives:
- Challenge feasibility, completeness, and scope of proposed plans
- Identify missing edge cases, error handling gaps, and untested assumptions
- Verify that the plan aligns with architectural constraints and conventions
- Provide constructive criticism with specific, actionable improvement suggestions

Constraints:
- Focus on correctness and completeness, not style preferences
- Every criticism must include a concrete suggestion for improvement
- Distinguish between blocking issues (must fix) and advisory notes (nice to have)
- Do not propose alternative designs — focus on evaluating the current proposal
"""

_DESIGN_EXPERT_PROMPT = """\
You are a Design Expert agent providing specialized review from a specific perspective.

Your objectives:
- Review designs from your assigned perspective (PM, security, architecture, UX, etc.)
- Identify issues specific to your domain of expertise
- Ensure the design meets quality standards for your area
- Provide prioritized feedback with clear severity levels

Constraints:
- Stay focused on your assigned review perspective
- Provide specific, actionable feedback with examples
- Use clear severity levels: BLOCKER, HIGH, MEDIUM, LOW
- Reference relevant standards, patterns, or best practices
"""

_IMPLEMENTER_PROMPT = """\
You are an Implementer agent responsible for code generation and execution.

Your objectives:
- Implement the design specification produced by the Architect
- Write production-quality code following codebase conventions
- Include appropriate tests for all new functionality
- Handle error cases and edge conditions explicitly

Constraints:
- Follow the design specification exactly — do not add unplanned features
- Match existing code style, naming conventions, and patterns
- Write tests before or alongside implementation code
- Do not modify files outside the assigned work unit's file scope
"""

# ── Role Registry ──

ROLE_REGISTRY: dict[AgentRole, RoleConfig] = {
    AgentRole.SCOUT: RoleConfig(
        role=AgentRole.SCOUT,
        name="Scout",
        default_model="claude-haiku-4-5",
        system_prompt=_SCOUT_PROMPT,
        description="Codebase exploration and precedent research",
        team_mode=None,
    ),
    AgentRole.ARCHITECT: RoleConfig(
        role=AgentRole.ARCHITECT,
        name="Architect",
        default_model="claude-opus-4-6",
        system_prompt=_ARCHITECT_PROMPT,
        description="System design and specification generation",
        team_mode="coordinate",
    ),
    AgentRole.SPECIALIST: RoleConfig(
        role=AgentRole.SPECIALIST,
        name="Specialist",
        default_model="claude-sonnet-4-6",
        system_prompt=_SPECIALIST_PROMPT,
        description="Domain-specific expertise (dynamically loaded)",
        team_mode="coordinate",
    ),
    AgentRole.PLAN_CRITIC: RoleConfig(
        role=AgentRole.PLAN_CRITIC,
        name="Plan Critic",
        default_model="gemini-pro",
        system_prompt=_PLAN_CRITIC_PROMPT,
        description="Feasibility and completeness adversarial review",
        team_mode="broadcast",
    ),
    AgentRole.DESIGN_EXPERT: RoleConfig(
        role=AgentRole.DESIGN_EXPERT,
        name="Design Expert",
        default_model="claude-sonnet-4-6",
        system_prompt=_DESIGN_EXPERT_PROMPT,
        description="Domain-perspective design review (PM, security, architecture, UX)",
        team_mode="broadcast",
    ),
    AgentRole.IMPLEMENTER: RoleConfig(
        role=AgentRole.IMPLEMENTER,
        name="Implementer",
        default_model="codex-gpt-5.3",
        system_prompt=_IMPLEMENTER_PROMPT,
        description="Code generation and execution",
        team_mode="route",
    ),
}


def get_role_config(role: AgentRole | str) -> RoleConfig:
    """Get the configuration for a given role.

    Args:
        role: AgentRole enum or string role name (e.g. "architect").

    Returns:
        The RoleConfig for the role.

    Raises:
        KeyError: If the role is not found in the registry.
    """
    if isinstance(role, str):
        try:
            role = AgentRole(role)
        except ValueError:
            raise KeyError(
                f"Unknown agent role: '{role}'. "
                f"Valid roles: {[r.value for r in AgentRole]}"
            ) from None
    if role not in ROLE_REGISTRY:
        raise KeyError(f"Role {role} not found in registry")
    return ROLE_REGISTRY[role]


def list_roles() -> list[AgentRole]:
    """Return all registered agent roles."""
    return list(ROLE_REGISTRY.keys())


def create_agent(
    role: AgentRole | str,
    *,
    config: ModelsConfig | None = None,
    project: str | None = None,
    spawn_override: str | None = None,
    persisted_model: str | None = None,
    extra_instructions: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create an agent configuration dict for a given role.

    Uses the 6-level model resolution chain (P0-04) to determine the model.
    Returns a dict suitable for constructing an Agno Agent.

    AC-07: When team_mode is set, callers must use explicit mode=TeamMode.xxx
    parameter, never boolean flags.

    Args:
        role: AgentRole enum or string role name.
        config: ModelsConfig for model resolution. Uses empty config if None.
        project: Optional project name for project-level overrides.
        spawn_override: Optional runtime model override.
        persisted_model: Optional model from persisted agent config.
        extra_instructions: Additional instructions to append to the system prompt.
        **kwargs: Additional keyword arguments passed through to the agent config.

    Returns:
        Dict with keys: name, model, role, instructions, description, team_mode,
        plus any additional kwargs.

    Raises:
        KeyError: If the role is not found.
    """
    role_cfg = get_role_config(role)
    effective_config = config or ModelsConfig()

    model_id = resolve_model(
        role=role_cfg.role.value,
        config=effective_config,
        project=project,
        spawn_override=spawn_override,
        persisted_model=persisted_model,
        role_default=role_cfg.default_model,
    )

    # Build instructions with any extra instructions
    role_config_with_extras = RoleConfig(
        role=role_cfg.role,
        name=role_cfg.name,
        default_model=role_cfg.default_model,
        system_prompt=role_cfg.system_prompt,
        description=role_cfg.description,
        team_mode=role_cfg.team_mode,
        extra_instructions=extra_instructions or [],
    )

    agent_config: dict[str, Any] = {
        "name": role_cfg.name,
        "model": model_id,
        "role": role_cfg.description,
        "instructions": role_config_with_extras.get_full_instructions(),
        "description": role_cfg.description,
        "team_mode": role_cfg.team_mode,
        **kwargs,
    }
    return agent_config
