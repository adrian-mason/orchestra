"""Agent definitions and role management (P1-01, DESIGN.md §3.1).

Re-exports core agent types and the role registry for convenient access.
"""

from orchestra.agents.definitions import (
    ROLE_REGISTRY,
    AgentRole,
    RoleConfig,
    create_agent,
    get_role_config,
    list_roles,
)

__all__ = [
    "ROLE_REGISTRY",
    "AgentRole",
    "RoleConfig",
    "create_agent",
    "get_role_config",
    "list_roles",
]
