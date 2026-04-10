"""Agent definitions, role management, and callable factory (P1-01/P1-02).

Re-exports core agent types, the role registry, and the callable factory
for dynamic role loading.
"""

from orchestra.agents.definitions import (
    ROLE_REGISTRY,
    AgentRole,
    RoleConfig,
    create_agent,
    get_role_config,
    list_roles,
)
from orchestra.agents.factory import (
    DEFAULT_TAG_MAPPINGS,
    SpecialistMapping,
    create_member_factory,
    list_available_tags,
    resolve_design_members,
)

__all__ = [
    "DEFAULT_TAG_MAPPINGS",
    "ROLE_REGISTRY",
    "AgentRole",
    "RoleConfig",
    "SpecialistMapping",
    "create_agent",
    "create_member_factory",
    "get_role_config",
    "list_available_tags",
    "list_roles",
    "resolve_design_members",
]
