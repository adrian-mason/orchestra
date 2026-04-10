"""Tests for P0-04: 6-level model resolution chain.

Covers all 6 priority levels, edge cases, and failure paths.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orchestra.model_resolver import (
    ADVERSARIAL_REVIEW_RUBRIC,
    HARDCODED_FALLBACK,
    ModelsConfig,
    ProjectConfig,
    _MODEL_REGISTRY,
    create_fresh_adversarial_reviewers,
    get_provider,
    instantiate_model,
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


# ── L4.5: Code-level role default (from RoleConfig.default_model) ──


def test_l4_5_role_default(empty_config: ModelsConfig):
    """role_default is used when no YAML role config exists."""
    result = resolve_model("scout", config=empty_config, role_default="claude-haiku-4-5")
    assert result == "claude-haiku-4-5"


def test_l4_5_role_default_loses_to_l4(full_config: ModelsConfig):
    """YAML role config (L4) takes priority over code-level role_default (L4.5)."""
    result = resolve_model("architect", config=full_config, role_default="fallback-model")
    assert result == "claude-opus-4-6"  # L4 from YAML wins


def test_l4_5_role_default_beats_l5():
    """role_default (L4.5) takes priority over global_default (L5)."""
    config = ModelsConfig(global_default="global-model")
    result = resolve_model("scout", config=config, role_default="role-model")
    assert result == "role-model"


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
    # L4.5 wins (unknown role with role_default, no project)
    assert resolve_model("unknown", config=config, role_default="L4.5-model") == "L4.5-model"
    # L5 wins (unknown role, no role_default, no project)
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


# ── instantiate_model ──


class TestInstantiateModel:
    """P1-06: Model instantiation from registry."""

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown model ID"):
            instantiate_model("nonexistent-model-9000")

    def test_all_registry_entries_have_valid_structure(self) -> None:
        for model_id, entry in _MODEL_REGISTRY.items():
            assert len(entry) == 3, f"{model_id}: registry entry must be (module, class, id)"
            module_path, class_name, actual_id = entry
            assert isinstance(module_path, str)
            assert isinstance(class_name, str)
            assert isinstance(actual_id, str)

    def test_registry_covers_all_review_matrix_models(self) -> None:
        """Every model referenced in the review matrix must be in the registry."""
        matrix_models = {"gemini-pro", "claude-sonnet-4-6", "codex-gpt-5.3"}
        for model_id in matrix_models:
            assert model_id in _MODEL_REGISTRY, f"{model_id} missing from _MODEL_REGISTRY"

    def test_instantiate_calls_correct_class(self) -> None:
        """Verify instantiate_model imports the right module and calls the right class."""
        mock_cls = MagicMock()
        mock_module = MagicMock()
        mock_module.Claude = mock_cls

        with patch("importlib.import_module", return_value=mock_module) as mock_import:
            result = instantiate_model("claude-sonnet-4-6")

        mock_import.assert_called_once_with("agno.models.anthropic")
        mock_cls.assert_called_once_with(id="claude-sonnet-4-6")
        assert result == mock_cls.return_value

    def test_instantiate_gemini(self) -> None:
        mock_cls = MagicMock()
        mock_module = MagicMock()
        mock_module.Gemini = mock_cls

        with patch("importlib.import_module", return_value=mock_module):
            result = instantiate_model("gemini-pro")

        mock_cls.assert_called_once_with(id="gemini-2.5-pro")
        assert result == mock_cls.return_value

    def test_instantiate_openai(self) -> None:
        mock_cls = MagicMock()
        mock_module = MagicMock()
        mock_module.OpenAIChat = mock_cls

        with patch("importlib.import_module", return_value=mock_module):
            result = instantiate_model("codex-gpt-5.3")

        mock_cls.assert_called_once_with(id="codex-gpt-5.3")
        assert result == mock_cls.return_value


# ── create_fresh_adversarial_reviewers ──


class TestCreateFreshAdversarialReviewers:
    """P1-06: Fresh reviewer instances are stateless and cross-model."""

    @patch("orchestra.model_resolver.instantiate_model")
    @patch("orchestra.model_resolver.Agent")
    def _make_reviewers(self, implementer: str, mock_agent_cls: MagicMock,
                        mock_instantiate: MagicMock) -> tuple[list, MagicMock, MagicMock]:
        """Helper: create reviewers with mocked Agent and instantiate_model."""
        mock_instantiate.side_effect = lambda mid: MagicMock(name=f"model-{mid}")
        mock_agent_cls.side_effect = lambda **kw: MagicMock(**kw)

        # Patch the import inside the function
        with patch.dict("sys.modules", {"agno.agent": MagicMock(Agent=mock_agent_cls)}):
            reviewers = create_fresh_adversarial_reviewers(implementer)
        return reviewers, mock_agent_cls, mock_instantiate

    def test_returns_two_reviewers_for_anthropic(self) -> None:
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock()

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("orchestra.model_resolver.Agent", mock_agent, create=True):
            # Need to re-import the function to use the module-level mock
            from orchestra.model_resolver import create_fresh_adversarial_reviewers as create_fn
            with patch("agno.agent.Agent", mock_agent):
                reviewers = create_fn("claude-sonnet-4-6")

        assert len(reviewers) == 2

    def test_returns_two_reviewers_for_openai(self) -> None:
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock()

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            reviewers = create_fresh_adversarial_reviewers("codex-gpt-5.3")

        assert len(reviewers) == 2

    def test_returns_two_reviewers_for_google(self) -> None:
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock()

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            reviewers = create_fresh_adversarial_reviewers("gemini-pro")

        assert len(reviewers) == 2

    def test_reviewers_have_unique_names(self) -> None:
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            reviewers = create_fresh_adversarial_reviewers("claude-sonnet-4-6")

        names = [call.kwargs["name"] for call in mock_agent.call_args_list]
        assert len(set(names)) == 2  # unique
        assert all("AdversarialReviewer" in n for n in names)
        assert any("AdversarialReviewer1-" in n for n in names)
        assert any("AdversarialReviewer2-" in n for n in names)

    def test_reviewers_are_stateless_no_session_id(self) -> None:
        """Fresh reviewers must not have session_id or learning — DESIGN.md §3.3."""
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            create_fresh_adversarial_reviewers("claude-sonnet-4-6")

        for call in mock_agent.call_args_list:
            # session_id must NOT be passed
            assert "session_id" not in call.kwargs
            # learning must NOT be passed
            assert "learning" not in call.kwargs

    def test_reviewers_use_cross_model_providers(self) -> None:
        """Reviewer models must be from different providers than implementer."""
        instantiated_ids: list[str] = []
        mock_instantiate = MagicMock(side_effect=lambda mid: (instantiated_ids.append(mid) or MagicMock()))
        mock_agent = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            create_fresh_adversarial_reviewers("claude-sonnet-4-6")

        # Anthropic implementer → reviewers should be openai + google
        providers = {get_provider(mid) for mid in instantiated_ids}
        assert "anthropic" not in providers

    def test_unknown_provider_still_returns_reviewers(self) -> None:
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock()

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            reviewers = create_fresh_adversarial_reviewers("llama-3-unknown")

        assert len(reviewers) == 2

    def test_reviewers_carry_adversarial_instructions(self) -> None:
        """DESIGN.md §4.7: fresh reviewers must have adversarial review rubric."""
        mock_instantiate = MagicMock(side_effect=lambda mid: MagicMock(name=f"model-{mid}"))
        mock_agent = MagicMock(side_effect=lambda **kw: MagicMock(**kw))

        with patch("orchestra.model_resolver.instantiate_model", mock_instantiate), \
             patch("agno.agent.Agent", mock_agent):
            create_fresh_adversarial_reviewers("claude-sonnet-4-6")

        for call in mock_agent.call_args_list:
            assert "instructions" in call.kwargs
            instructions = call.kwargs["instructions"]
            assert isinstance(instructions, list)
            assert len(instructions) == 1
            assert instructions[0] == ADVERSARIAL_REVIEW_RUBRIC
            assert "adversarial" in instructions[0].lower()
            assert "AC-01" in instructions[0]
