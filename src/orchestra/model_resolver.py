"""P0-04: 6-level model resolution chain.

Resolves which LLM model to use for a given agent role, respecting a 6-level
priority chain from persisted config down to hardcoded fallback.

DESIGN.md §3.2 — orchestra.yaml schema and resolve_model() specification.

Priority (high → low):
  L1: Persisted agent config (retry consistency)
  L2: Spawn-time override (--model CLI flag)
  L3a: Project-level role config (projects.{name}.roles.{role})
  L3b: Project-level agent default (projects.{name}.default)
  L4: Global role config (roles.{role})
  L5: global_default
  L6: Hardcoded fallback
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# L6: Hardcoded fallback — safety net when all config is missing.
HARDCODED_FALLBACK = "claude-sonnet-4-6"


@dataclass
class ProjectConfig:
    """Per-project model configuration."""

    roles: dict[str, str] = field(default_factory=dict)
    default: str | None = None


@dataclass
class ModelsConfig:
    """Parsed orchestra.yaml models section."""

    global_default: str | None = None
    roles: dict[str, str] = field(default_factory=dict)
    projects: dict[str, ProjectConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelsConfig:
        """Parse the 'models' section of orchestra.yaml.

        Malformed project entries (non-dict values) are skipped with a
        ValueError for clear diagnostics. Missing/empty sections degrade
        gracefully to empty defaults.
        """
        projects: dict[str, ProjectConfig] = {}
        raw_projects = data.get("projects", {})
        if isinstance(raw_projects, dict):
            for name, proj_data in raw_projects.items():
                if not isinstance(proj_data, dict):
                    raise ValueError(
                        f"models.projects.{name}: expected dict, "
                        f"got {type(proj_data).__name__}"
                    )
                projects[name] = ProjectConfig(
                    roles=proj_data.get("roles", {}),
                    default=proj_data.get("default"),
                )
        elif raw_projects is not None:
            raise ValueError(
                f"models.projects: expected dict, got {type(raw_projects).__name__}"
            )
        return cls(
            global_default=data.get("global_default"),
            roles=data.get("roles") or {},
            projects=projects,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> ModelsConfig:
        """Load config from orchestra.yaml file."""
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path) as f:
            raw = yaml.safe_load(f)
        if not raw or "models" not in raw:
            return cls()
        models_data = raw["models"]
        if not isinstance(models_data, dict):
            return cls()
        return cls.from_dict(models_data)


def resolve_model(
    role: str,
    *,
    config: ModelsConfig,
    project: str | None = None,
    spawn_override: str | None = None,
    persisted_model: str | None = None,
) -> str:
    """6-level model resolution chain.

    Returns the model ID string to use for the given role.
    YAML paths correspond strictly to this function's resolution order.

    Args:
        role: Agent role name (e.g. "architect", "implementer").
        config: Parsed ModelsConfig from orchestra.yaml.
        project: Optional project name for project-level overrides.
        spawn_override: Optional runtime override (e.g. --model CLI flag).
        persisted_model: Optional model from persisted agent config (retry consistency).

    Returns:
        Model ID string (e.g. "claude-sonnet-4-6").
    """
    # L1: Persisted agent config (ensures retry consistency)
    if persisted_model and persisted_model.strip():
        return persisted_model.strip()

    # L2: Spawn-time override
    if spawn_override and spawn_override.strip():
        return spawn_override.strip()

    # L3: Project-level config
    if project and project in config.projects:
        proj_cfg = config.projects[project]

        # L3a: Project-level role config
        proj_role_model = proj_cfg.roles.get(role)
        if proj_role_model:
            return proj_role_model

        # L3b: Project-level agent default
        if proj_cfg.default:
            return proj_cfg.default

    # L4: Global role config
    role_model = config.roles.get(role)
    if role_model:
        return role_model

    # L5: global_default
    if config.global_default:
        return config.global_default

    # L6: Hardcoded fallback
    return HARDCODED_FALLBACK


def get_provider(model_id: str) -> str:
    """Extract provider name from model ID.

    Uses prefix heuristics matching DESIGN.md §3.3 cross-model review matrix.
    """
    model_lower = model_id.lower()
    if any(k in model_lower for k in ("claude", "haiku", "sonnet", "opus")):
        return "anthropic"
    if any(k in model_lower for k in ("gemini",)):
        return "google"
    if any(k in model_lower for k in ("gpt", "codex", "o1", "o3", "o4")):
        return "openai"
    return "unknown"


def resolve_adversarial_reviewer(implementer_model: str) -> list[str]:
    """Ensure reviewers use different model providers than the implementer.

    DESIGN.md §3.3 cross-model review matrix.
    """
    REVIEW_MATRIX = {
        "openai": ["gemini-pro", "claude-sonnet-4-6"],
        "google": ["codex-gpt-5.3", "claude-sonnet-4-6"],
        "anthropic": ["codex-gpt-5.3", "gemini-pro"],
    }
    provider = get_provider(implementer_model)
    return REVIEW_MATRIX.get(provider, REVIEW_MATRIX["anthropic"])
