"""Callable Factory for dynamic role loading (P1-02, DESIGN.md §3.4).

Provides `resolve_design_members(team, session_state)` callable factory
for dynamically loading Specialist agents based on `project_tags`.

Usage with Agno F7 pattern:
    design_team = Team(
        members=resolve_design_members,  # callable, not invocation
        mode=TeamMode.coordinate,
        db=traces_db,
    )

The factory always includes an Architect and conditionally adds domain
Specialists based on tags in `session_state["project_tags"]`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from orchestra.agents.definitions import (
    AgentRole,
    create_agent,
)
from orchestra.model_resolver import ModelsConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpecialistMapping:
    """Maps a project tag to a specialist agent configuration.

    Attributes:
        tag: Project tag (e.g. "ebpf", "frontend").
        name: Display name for the specialist (e.g. "Probe", "Artisan").
        role_description: Short description of the specialist's domain.
        extra_instructions: Domain-specific instructions appended to the
            base Specialist system prompt.
    """

    tag: str
    name: str
    role_description: str
    extra_instructions: list[str] = field(default_factory=list)


# Tag-to-specialist mapping per DESIGN.md §3.4
DEFAULT_TAG_MAPPINGS: list[SpecialistMapping] = [
    SpecialistMapping(
        tag="ebpf",
        name="Probe",
        role_description="eBPF Domain Expert",
        extra_instructions=[
            "You specialize in eBPF programs, BPF maps, verifier constraints, "
            "and kernel-level tracing. Focus on correctness of BPF bytecode "
            "patterns and safe memory access."
        ],
    ),
    SpecialistMapping(
        tag="frontend",
        name="Artisan",
        role_description="Frontend Expert",
        extra_instructions=[
            "You specialize in frontend architecture, component design, "
            "state management, and UI/UX patterns. Focus on accessibility, "
            "performance, and maintainable component hierarchies."
        ],
    ),
    SpecialistMapping(
        tag="data",
        name="Oracle",
        role_description="Data & Validation Expert",
        extra_instructions=[
            "You specialize in data pipelines, validation schemas, "
            "data integrity, and ETL patterns. Focus on data contracts, "
            "schema evolution, and validation completeness."
        ],
    ),
    SpecialistMapping(
        tag="security",
        name="Sentinel",
        role_description="Security Expert",
        extra_instructions=[
            "You specialize in application security, threat modeling, "
            "authentication/authorization patterns, and OWASP top 10. "
            "Focus on identifying vulnerabilities and secure design patterns."
        ],
    ),
]


def _build_tag_index(
    mappings: list[SpecialistMapping],
) -> dict[str, SpecialistMapping]:
    """Build a tag → SpecialistMapping index for O(1) lookup."""
    return {m.tag: m for m in mappings}


def resolve_design_members(
    team: Any,
    session_state: dict[str, Any],
    *,
    config: ModelsConfig | None = None,
    project: str | None = None,
    tag_mappings: list[SpecialistMapping] | None = None,
) -> list[dict[str, Any]]:
    """Dynamically resolve Design Team members based on project tags.

    Callable factory compatible with Agno's `Team(members=...)` F7 pattern.
    Always includes an Architect; conditionally adds domain Specialists
    based on `session_state["project_tags"]`.

    Args:
        team: Team instance (passed by Agno, unused in current impl).
        session_state: Dict containing project metadata. Reads
            ``session_state["project_tags"]`` for tag-based specialist loading.
        config: Optional ModelsConfig for model resolution.
        project: Optional project name for project-level model overrides.
        tag_mappings: Custom tag-to-specialist mappings. Defaults to
            DEFAULT_TAG_MAPPINGS.

    Returns:
        List of agent config dicts (compatible with Agent(**cfg) construction).
    """
    active_mappings = tag_mappings if tag_mappings is not None else DEFAULT_TAG_MAPPINGS
    tag_index = _build_tag_index(active_mappings)

    # Always include the Architect
    architect_cfg = create_agent(
        AgentRole.ARCHITECT,
        config=config,
        project=project,
    )
    members = [architect_cfg]

    # Conditionally add specialists based on project tags
    project_tags = session_state.get("project_tags", [])
    if not isinstance(project_tags, list):
        logger.warning(
            "project_tags should be a list, got %s. Treating as empty.",
            type(project_tags).__name__,
        )
        project_tags = []

    seen_tags: set[str] = set()
    for tag in project_tags:
        if not isinstance(tag, str):
            logger.warning("Skipping non-string tag: %r", tag)
            continue

        tag_lower = tag.lower().strip()
        if tag_lower in seen_tags:
            continue
        seen_tags.add(tag_lower)

        mapping = tag_index.get(tag_lower)
        if mapping is None:
            logger.info("Unknown project tag '%s' — no specialist mapped.", tag)
            continue

        specialist_cfg = create_agent(
            AgentRole.SPECIALIST,
            config=config,
            project=project,
            extra_instructions=mapping.extra_instructions,
        )
        # Override name and role for the specific specialist domain
        specialist_cfg["name"] = mapping.name
        specialist_cfg["role"] = mapping.role_description
        members.append(specialist_cfg)

    return members


def create_member_factory(
    *,
    config: ModelsConfig | None = None,
    project: str | None = None,
    tag_mappings: list[SpecialistMapping] | None = None,
) -> Any:
    """Create a callable factory pre-configured with model config.

    Returns a callable with signature ``(team, session_state) -> list[dict]``
    compatible with Agno's ``Team(members=...)`` parameter.

    Args:
        config: Optional ModelsConfig for model resolution.
        project: Optional project name for project-level overrides.
        tag_mappings: Custom tag-to-specialist mappings.

    Returns:
        Callable factory for use as ``Team(members=factory)``.
    """

    def factory(team: Any, session_state: dict[str, Any]) -> list[dict[str, Any]]:
        return resolve_design_members(
            team,
            session_state,
            config=config,
            project=project,
            tag_mappings=tag_mappings,
        )

    return factory


def list_available_tags(
    tag_mappings: list[SpecialistMapping] | None = None,
) -> list[str]:
    """Return all registered project tags."""
    mappings = tag_mappings if tag_mappings is not None else DEFAULT_TAG_MAPPINGS
    return [m.tag for m in mappings]
