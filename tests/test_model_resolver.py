"""Tests for P0-04: 6-level model resolution chain.

Covers all 6 priority levels, edge cases, and failure paths.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orchestra.model_resolver import (
    HARDCODED_FALLBACK,
    ModelsConfig,
    ProjectConfig,
    get_provider,
    resolve_adversarial_reviewer,
    resolve_model,
)


# ── Fixtures ──


@pytest.fixture
def full_config() -> ModelsConfig:
    """Config matching DESIGN.md §3.2 orchestra.yaml example."""
    return ModelsConfig(
        global_default="claude-sonnet-4-6",
        roles={
            "architect": "claude-opus-4-6",
            "plan_critic": "gemini-pro",
            "design_expert": "claude-sonnet-4-6",
            "implementer": "codex-gpt-5.3",
            "scout": "claude-haiku-4-5",
            "adv_reviewer": "gemini-pro",
            "pr_shepherd": "claude-sonnet-4-6",
            "watchdog": "claude-haiku-4-5",
        },
        projects={
            "cadence": ProjectConfig(
                roles={
                    "implementer": "claude-sonnet-4-6",
                    "adv_reviewer": "claude-opus-4-6",
                },
                default="claude-sonnet-4-6",
            ),
        },
    )


@pytest.fixture
def empty_config() -> ModelsConfig:
    return ModelsConfig()


@pytest.fixture
def minimal_config() -> ModelsConfig:
    return ModelsConfig(global_default="claude-sonnet-4-6")


# ── L1: Persisted agent config ──


def test_l1_persisted_model_wins_over_everything(full_config: ModelsConfig):
    """L1 has highest priority — even with all other levels populated."""
    result = resolve_model(
        "architect",
        config=full_config,
        project="cadence",
        spawn_override="gpt-4o",
        persisted_model="gemini-flash",
    )
    assert result == "gemini-flash"


# ── L2: Spawn-time override ──


def test_l2_spawn_override_wins_over_config(full_config: ModelsConfig):
    result = resolve_model(
        "architect",
        config=full_config,
        project="cadence",
        spawn_override="gpt-4o",
    )
    assert result == "gpt-4o"


def test_l2_spawn_override_without_project(full_config: ModelsConfig):
    result = resolve_model(
        "architect",
        config=full_config,
        spawn_override="gpt-4o",
    )
    assert result == "gpt-4o"


# ── L3a: Project-level role config ──


def test_l3a_project_role_override(full_config: ModelsConfig):
    """cadence.roles.implementer overrides global roles.implementer."""
    result = resolve_model("implementer", config=full_config, project="cadence")
    assert result == "claude-sonnet-4-6"  # not codex-gpt-5.3


def test_l3a_project_role_not_overridden_falls_to_l3b(full_config: ModelsConfig):
    """Role not in project.roles falls to project.default (L3b)."""
    result = resolve_model("scout", config=full_config, project="cadence")
    assert result == "claude-sonnet-4-6"  # cadence.default


# ── L3b: Project-level agent default ──


def test_l3b_project_default(full_config: ModelsConfig):
    """Unknown role in known project falls to project.default."""
    result = resolve_model("unknown_role", config=full_config, project="cadence")
    assert result == "claude-sonnet-4-6"  # cadence.default


def test_l3b_project_without_default_falls_to_l4():
    """Project exists but has no default — falls through to L4."""
    config = ModelsConfig(
        roles={"architect": "claude-opus-4-6"},
        projects={"myproj": ProjectConfig(roles={}, default=None)},
    )
    result = resolve_model("architect", config=config, project="myproj")
    assert result == "claude-opus-4-6"  # L4


# ── L4: Global role config ──


def test_l4_global_role(full_config: ModelsConfig):
    """No project specified — uses global role config."""
    result = resolve_model("architect", config=full_config)
    assert result == "claude-opus-4-6"


def test_l4_unknown_project_falls_to_global_role(full_config: ModelsConfig):
    """Unknown project name — skips L3, falls to L4."""
    result = resolve_model("architect", config=full_config, project="nonexistent")
    assert result == "claude-opus-4-6"


# ── L5: global_default ──


def test_l5_global_default(minimal_config: ModelsConfig):
    """Unknown role with only global_default set."""
    result = resolve_model("unknown_role", config=minimal_config)
    assert result == "claude-sonnet-4-6"


# ── L6: Hardcoded fallback ──


def test_l6_hardcoded_fallback(empty_config: ModelsConfig):
    """Completely empty config — uses hardcoded fallback."""
    result = resolve_model("anything", config=empty_config)
    assert result == HARDCODED_FALLBACK


def test_l6_fallback_is_claude_sonnet():
    assert HARDCODED_FALLBACK == "claude-sonnet-4-6"


# ── Priority chain integration ──


def test_full_chain_l1_to_l6():
    """Walk the full chain by removing one level at a time."""
    config = ModelsConfig(
        global_default="L5-model",
        roles={"testrole": "L4-model"},
        projects={
            "proj": ProjectConfig(
                roles={"testrole": "L3a-model"},
                default="L3b-model",
            )
        },
    )

    # L1 wins
    assert resolve_model("testrole", config=config, project="proj",
                         spawn_override="L2-model", persisted_model="L1-model") == "L1-model"
    # L2 wins (no L1)
    assert resolve_model("testrole", config=config, project="proj",
                         spawn_override="L2-model") == "L2-model"
    # L3a wins (no L1, L2)
    assert resolve_model("testrole", config=config, project="proj") == "L3a-model"
    # L3b wins (no L1, L2, L3a)
    assert resolve_model("otherrole", config=config, project="proj") == "L3b-model"
    # L4 wins (no project)
    assert resolve_model("testrole", config=config) == "L4-model"
    # L5 wins (unknown role, no project)
    assert resolve_model("unknown", config=config) == "L5-model"
    # L6 wins (empty config)
    assert resolve_model("unknown", config=ModelsConfig()) == HARDCODED_FALLBACK


# ── ModelsConfig.from_dict ──


def test_from_dict_full():
    raw = {
        "global_default": "claude-sonnet-4-6",
        "roles": {"architect": "claude-opus-4-6"},
        "projects": {
            "cadence": {
                "roles": {"implementer": "claude-sonnet-4-6"},
                "default": "claude-sonnet-4-6",
            }
        },
    }
    config = ModelsConfig.from_dict(raw)
    assert config.global_default == "claude-sonnet-4-6"
    assert config.roles["architect"] == "claude-opus-4-6"
    assert config.projects["cadence"].roles["implementer"] == "claude-sonnet-4-6"
    assert config.projects["cadence"].default == "claude-sonnet-4-6"


def test_from_dict_empty():
    config = ModelsConfig.from_dict({})
    assert config.global_default is None
    assert config.roles == {}
    assert config.projects == {}


def test_from_dict_partial():
    config = ModelsConfig.from_dict({"global_default": "test-model"})
    assert config.global_default == "test-model"
    assert config.roles == {}


# ── ModelsConfig.from_dict malformed input ──


def test_from_dict_projects_as_list():
    """projects is a list instead of dict — raises ValueError."""
    with pytest.raises(ValueError, match="models.projects.*expected dict.*got list"):
        ModelsConfig.from_dict({"projects": []})


def test_from_dict_project_entry_is_none():
    """Single project entry is None — raises ValueError."""
    with pytest.raises(ValueError, match="models.projects.p.*expected dict.*got NoneType"):
        ModelsConfig.from_dict({"projects": {"p": None}})


def test_from_dict_project_entry_is_string():
    """Single project entry is a string — raises ValueError."""
    with pytest.raises(ValueError, match="models.projects.p.*expected dict.*got str"):
        ModelsConfig.from_dict({"projects": {"p": "x"}})


def test_from_dict_projects_null():
    """projects: null in YAML — degrades to empty projects."""
    config = ModelsConfig.from_dict({"projects": None})
    assert config.projects == {}


def test_from_dict_roles_null():
    """roles: null in YAML — degrades to empty roles."""
    config = ModelsConfig.from_dict({"roles": None})
    assert config.roles == {}


def test_from_dict_roles_null_resolve_model():
    """roles: null doesn't crash resolve_model — falls to L6."""
    config = ModelsConfig.from_dict({"roles": None})
    result = resolve_model("anything", config=config)
    assert result == HARDCODED_FALLBACK


# ── resolve_model whitespace handling ──


def test_l1_whitespace_persisted_model_falls_through(full_config: ModelsConfig):
    """Whitespace-only persisted_model is ignored, falls to lower levels."""
    result = resolve_model("architect", config=full_config, persisted_model="   ")
    assert result == "claude-opus-4-6"  # L4, not whitespace


def test_l2_whitespace_spawn_override_falls_through(full_config: ModelsConfig):
    """Whitespace-only spawn_override is ignored, falls to lower levels."""
    result = resolve_model("architect", config=full_config, spawn_override="  ")
    assert result == "claude-opus-4-6"  # L4, not whitespace


def test_l1_persisted_model_stripped(full_config: ModelsConfig):
    """Leading/trailing whitespace in persisted_model is stripped."""
    result = resolve_model("architect", config=full_config, persisted_model="  gemini-flash  ")
    assert result == "gemini-flash"


def test_l2_spawn_override_stripped(full_config: ModelsConfig):
    """Leading/trailing whitespace in spawn_override is stripped."""
    result = resolve_model("architect", config=full_config, spawn_override="  gpt-4o  ")
    assert result == "gpt-4o"


# ── ModelsConfig.from_yaml ──


def test_from_yaml_valid():
    yaml_content = """
models:
  global_default: claude-sonnet-4-6
  roles:
    architect: claude-opus-4-6
  projects:
    cadence:
      roles:
        implementer: claude-sonnet-4-6
      default: claude-sonnet-4-6
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        config = ModelsConfig.from_yaml(f.name)

    assert config.global_default == "claude-sonnet-4-6"
    assert config.roles["architect"] == "claude-opus-4-6"
    assert "cadence" in config.projects


def test_from_yaml_missing_file():
    """Missing file returns empty config (not an error)."""
    config = ModelsConfig.from_yaml("/nonexistent/path/orchestra.yaml")
    assert config.global_default is None
    assert config.roles == {}


def test_from_yaml_no_models_section():
    """YAML exists but has no 'models' key."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("something_else:\n  key: value\n")
        f.flush()
        config = ModelsConfig.from_yaml(f.name)

    assert config.global_default is None


def test_from_yaml_empty_file():
    """Empty YAML file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("")
        f.flush()
        config = ModelsConfig.from_yaml(f.name)

    assert config.global_default is None


def test_from_yaml_models_null():
    """models: null in YAML — degrades to empty config."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("models: null\n")
        f.flush()
        config = ModelsConfig.from_yaml(f.name)

    assert config.global_default is None
    assert config.roles == {}
    assert config.projects == {}


# ── get_provider ──


def test_get_provider_anthropic():
    assert get_provider("claude-sonnet-4-6") == "anthropic"
    assert get_provider("claude-opus-4-6") == "anthropic"
    assert get_provider("claude-haiku-4-5") == "anthropic"


def test_get_provider_google():
    assert get_provider("gemini-pro") == "google"
    assert get_provider("gemini-flash") == "google"


def test_get_provider_openai():
    assert get_provider("codex-gpt-5.3") == "openai"
    assert get_provider("gpt-4o") == "openai"
    assert get_provider("o4-mini") == "openai"


def test_get_provider_unknown():
    assert get_provider("llama-3") == "unknown"
    assert get_provider("mistral-large") == "unknown"


# ── resolve_adversarial_reviewer ──


def test_adversarial_reviewer_cross_model():
    """Reviewer models must always be from different providers."""
    reviewers = resolve_adversarial_reviewer("codex-gpt-5.3")
    providers = [get_provider(r) for r in reviewers]
    assert "openai" not in providers
    assert len(reviewers) == 2

    reviewers = resolve_adversarial_reviewer("gemini-pro")
    providers = [get_provider(r) for r in reviewers]
    assert "google" not in providers

    reviewers = resolve_adversarial_reviewer("claude-sonnet-4-6")
    providers = [get_provider(r) for r in reviewers]
    assert "anthropic" not in providers


def test_adversarial_reviewer_unknown_provider_fallback():
    """Unknown provider gets anthropic's matrix (safe default)."""
    reviewers = resolve_adversarial_reviewer("llama-3")
    assert len(reviewers) == 2
