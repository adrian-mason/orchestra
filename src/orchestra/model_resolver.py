"""P0-04 + P1-06: Model resolution and cross-model adversarial review.

P0-04: 6-level model resolution chain (resolve_model).
P1-06: Cross-model adversarial review matrix (instantiate_model,
       create_fresh_adversarial_reviewers).

DESIGN.md §3.2 — orchestra.yaml schema and resolve_model() specification.
DESIGN.md §3.3 — Cross-model review matrix.

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
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.models.base import Model


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
    role_default: str | None = None,
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
        role_default: Optional code-level default model for the role (from RoleConfig).
            Sits between L4 (YAML role config) and L5 (global_default).

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

    # L4: Global role config (from YAML)
    role_model = config.roles.get(role)
    if role_model:
        return role_model

    # L4.5: Code-level role default (from RoleConfig.default_model)
    if role_default and role_default.strip():
        return role_default.strip()

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


# ---------------------------------------------------------------------------
# P1-06: Model instantiation and fresh adversarial reviewers
# ---------------------------------------------------------------------------

# Registry mapping model ID → (module_path, class_name, actual_model_id).
# Lazy imports avoid requiring all provider SDKs at import time.
_MODEL_REGISTRY: dict[str, tuple[str, str, str]] = {
    "claude-opus-4-6": ("agno.models.anthropic", "Claude", "claude-opus-4-6"),
    "claude-sonnet-4-6": ("agno.models.anthropic", "Claude", "claude-sonnet-4-6"),
    "claude-haiku-4-5": ("agno.models.anthropic", "Claude", "claude-haiku-4-5"),
    "gemini-pro": ("agno.models.google", "Gemini", "gemini-2.5-pro"),
    "codex-gpt-5.3": ("agno.models.openai", "OpenAIChat", "codex-gpt-5.3"),
}


def instantiate_model(model_id: str) -> Model:
    """Construct an Agno Model object from a model ID.

    DESIGN.md §3.3 — used by create_fresh_adversarial_reviewers().
    Bypasses resolve_model(); the review matrix dictates model selection.
    """
    entry = _MODEL_REGISTRY.get(model_id)
    if entry is None:
        raise ValueError(f"Unknown model ID: {model_id}")

    module_path, class_name, actual_id = entry
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls(id=actual_id)


ADVERSARIAL_REVIEW_RUBRIC = (
    "You are an adversarial code reviewer. Your role is to find defects, "
    "security vulnerabilities, design violations, and correctness issues. "
    "You must review code from a different model provider than the implementer "
    "to ensure diverse perspectives. Be thorough and critical — flag any "
    "deviation from the design specification, missing error handling, "
    "untested edge cases, or potential regressions. Never approve code "
    "that violates architectural constraints (AC-01 through AC-07)."
)


def create_fresh_adversarial_reviewers(implementer_model_id: str) -> list[Agent]:
    """Create two stateless reviewer instances from the cross-model review matrix.

    DESIGN.md §3.3 + §4.7 — reviewers use different providers than the implementer.
    Fresh reviewers have no session_id and no learning (completely stateless).
    Each reviewer carries adversarial review instructions per §4.7.
    Model selection is driven entirely by the review matrix, not resolve_model().
    """
    from agno.agent import Agent

    reviewer_model_ids = resolve_adversarial_reviewer(implementer_model_id)
    return [
        Agent(
            name=f"AdversarialReviewer{i + 1}-{uuid4().hex[:8]}",
            model=instantiate_model(model_id),
            instructions=[ADVERSARIAL_REVIEW_RUBRIC],
            # No session_id, no learning — completely stateless
        )
        for i, model_id in enumerate(reviewer_model_ids)
    ]
