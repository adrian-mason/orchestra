"""Tests for orchestra.agents.definitions (P1-01).

Tests cover:
- 6 agent role definitions with correct defaults
- Model resolution integration (6-level chain)
- Role config lookup by enum and string
- Agent creation with overrides
- System prompt content
- AC-07 compliance (team_mode as string, not boolean)
"""

import pytest

from orchestra.agents.definitions import (
    ROLE_REGISTRY,
    AgentRole,
    RoleConfig,
    create_agent,
    get_role_config,
    list_roles,
)
from orchestra.model_resolver import ModelsConfig


class TestAgentRoleEnum:
    def test_has_six_roles(self):
        assert len(AgentRole) == 6

    def test_role_values(self):
        expected = {
            "scout", "architect", "specialist",
            "plan_critic", "design_expert", "implementer",
        }
        actual = {r.value for r in AgentRole}
        assert expected == actual

    def test_role_is_string_enum(self):
        assert isinstance(AgentRole.SCOUT, str)
        assert AgentRole.SCOUT == "scout"


class TestRoleConfig:
    def test_frozen_dataclass(self):
        cfg = get_role_config(AgentRole.SCOUT)
        with pytest.raises(AttributeError):
            cfg.name = "Changed"  # type: ignore[misc]

    def test_get_full_instructions_without_extras(self):
        cfg = get_role_config(AgentRole.SCOUT)
        assert cfg.get_full_instructions() == cfg.system_prompt

    def test_get_full_instructions_with_extras(self):
        cfg = RoleConfig(
            role=AgentRole.SCOUT,
            name="Scout",
            default_model="claude-haiku-4-5",
            system_prompt="Base prompt",
            description="Test",
            extra_instructions=["Extra 1", "Extra 2"],
        )
        full = cfg.get_full_instructions()
        assert "Base prompt" in full
        assert "Extra 1" in full
        assert "Extra 2" in full


class TestRoleRegistry:
    def test_registry_has_six_entries(self):
        assert len(ROLE_REGISTRY) == 6

    def test_all_roles_present(self):
        for role in AgentRole:
            assert role in ROLE_REGISTRY

    def test_scout_defaults(self):
        cfg = ROLE_REGISTRY[AgentRole.SCOUT]
        assert cfg.name == "Scout"
        assert cfg.default_model == "claude-haiku-4-5"
        assert cfg.team_mode is None
        assert "exploration" in cfg.description.lower() or "codebase" in cfg.description.lower()

    def test_architect_defaults(self):
        cfg = ROLE_REGISTRY[AgentRole.ARCHITECT]
        assert cfg.name == "Architect"
        assert cfg.default_model == "claude-opus-4-6"
        assert cfg.team_mode == "coordinate"

    def test_specialist_defaults(self):
        cfg = ROLE_REGISTRY[AgentRole.SPECIALIST]
        assert cfg.name == "Specialist"
        assert cfg.default_model == "claude-sonnet-4-6"
        assert cfg.team_mode == "coordinate"

    def test_plan_critic_defaults(self):
        cfg = ROLE_REGISTRY[AgentRole.PLAN_CRITIC]
        assert cfg.name == "Plan Critic"
        assert cfg.default_model == "gemini-pro"
        assert cfg.team_mode == "broadcast"

    def test_design_expert_defaults(self):
        cfg = ROLE_REGISTRY[AgentRole.DESIGN_EXPERT]
        assert cfg.name == "Design Expert"
        assert cfg.default_model == "claude-sonnet-4-6"
        assert cfg.team_mode == "broadcast"

    def test_implementer_defaults(self):
        cfg = ROLE_REGISTRY[AgentRole.IMPLEMENTER]
        assert cfg.name == "Implementer"
        assert cfg.default_model == "codex-gpt-5.3"
        assert cfg.team_mode == "route"


class TestGetRoleConfig:
    def test_by_enum(self):
        cfg = get_role_config(AgentRole.ARCHITECT)
        assert cfg.name == "Architect"

    def test_by_string(self):
        cfg = get_role_config("architect")
        assert cfg.name == "Architect"

    def test_unknown_string_raises(self):
        with pytest.raises(KeyError, match="Unknown agent role"):
            get_role_config("nonexistent_role")

    def test_error_message_lists_valid_roles(self):
        with pytest.raises(KeyError, match="scout"):
            get_role_config("bad_role")


class TestListRoles:
    def test_returns_all_roles(self):
        roles = list_roles()
        assert len(roles) == 6
        assert AgentRole.SCOUT in roles
        assert AgentRole.IMPLEMENTER in roles


class TestCreateAgent:
    def test_basic_creation(self):
        agent = create_agent(AgentRole.SCOUT)
        assert agent["name"] == "Scout"
        assert agent["model"] is not None
        assert "instructions" in agent
        assert len(agent["instructions"]) > 0

    def test_model_resolution_uses_role_default(self):
        """Without config, resolve_model uses RoleConfig.default_model (L4.5)."""
        agent = create_agent(AgentRole.SCOUT)
        assert agent["model"] == "claude-haiku-4-5"  # Scout's default_model

    def test_each_role_gets_its_own_default_model(self):
        """Each role resolves to its own default_model, not L6 fallback."""
        expected = {
            AgentRole.SCOUT: "claude-haiku-4-5",
            AgentRole.ARCHITECT: "claude-opus-4-6",
            AgentRole.SPECIALIST: "claude-sonnet-4-6",
            AgentRole.PLAN_CRITIC: "gemini-pro",
            AgentRole.DESIGN_EXPERT: "claude-sonnet-4-6",
            AgentRole.IMPLEMENTER: "codex-gpt-5.3",
        }
        for role, expected_model in expected.items():
            agent = create_agent(role)
            assert agent["model"] == expected_model, (
                f"{role.value} should default to {expected_model}, got {agent['model']}"
            )

    def test_model_resolution_with_config(self):
        config = ModelsConfig(
            roles={"scout": "claude-haiku-4-5"},
        )
        agent = create_agent(AgentRole.SCOUT, config=config)
        assert agent["model"] == "claude-haiku-4-5"

    def test_model_resolution_with_project_override(self):
        from orchestra.model_resolver import ProjectConfig

        config = ModelsConfig(
            roles={"architect": "claude-opus-4-6"},
            projects={
                "myproject": ProjectConfig(
                    roles={"architect": "gemini-pro"},
                ),
            },
        )
        agent = create_agent(
            AgentRole.ARCHITECT,
            config=config,
            project="myproject",
        )
        assert agent["model"] == "gemini-pro"

    def test_spawn_override(self):
        agent = create_agent(AgentRole.SCOUT, spawn_override="test-model-123")
        assert agent["model"] == "test-model-123"

    def test_persisted_model(self):
        agent = create_agent(AgentRole.SCOUT, persisted_model="retry-model-456")
        assert agent["model"] == "retry-model-456"

    def test_persisted_model_takes_priority(self):
        """L1 (persisted) beats L2 (spawn_override)."""
        agent = create_agent(
            AgentRole.SCOUT,
            persisted_model="persisted",
            spawn_override="override",
        )
        assert agent["model"] == "persisted"

    def test_extra_instructions_appended(self):
        agent = create_agent(
            AgentRole.SCOUT,
            extra_instructions=["Focus on Rust files only."],
        )
        assert "Focus on Rust files only." in agent["instructions"]

    def test_team_mode_not_in_agent_dict(self):
        """team_mode is accessed via get_role_config(), not in agent dict.
        This ensures Agent(**create_agent(...)) won't raise TypeError."""
        agent = create_agent(AgentRole.ARCHITECT)
        assert "team_mode" not in agent

    def test_team_mode_via_role_config(self):
        """team_mode is available from get_role_config() for Team construction."""
        cfg = get_role_config(AgentRole.ARCHITECT)
        assert cfg.team_mode == "coordinate"
        cfg_scout = get_role_config(AgentRole.SCOUT)
        assert cfg_scout.team_mode is None

    def test_kwargs_pass_through(self):
        agent = create_agent(AgentRole.SCOUT, custom_param="value")
        assert agent["custom_param"] == "value"

    def test_create_by_string_role(self):
        agent = create_agent("implementer")
        assert agent["name"] == "Implementer"

    def test_unknown_role_raises(self):
        with pytest.raises(KeyError, match="Unknown agent role"):
            create_agent("nonexistent")


class TestSystemPrompts:
    """Verify system prompts contain essential role-defining content."""

    def test_scout_prompt_mentions_exploration(self):
        cfg = get_role_config(AgentRole.SCOUT)
        assert "exploration" in cfg.system_prompt.lower() or "explore" in cfg.system_prompt.lower()

    def test_scout_prompt_mentions_read_only(self):
        cfg = get_role_config(AgentRole.SCOUT)
        assert "read-only" in cfg.system_prompt.lower() or "read only" in cfg.system_prompt.lower()

    def test_architect_prompt_mentions_design(self):
        cfg = get_role_config(AgentRole.ARCHITECT)
        assert "design" in cfg.system_prompt.lower()

    def test_plan_critic_prompt_mentions_adversarial(self):
        cfg = get_role_config(AgentRole.PLAN_CRITIC)
        assert "adversarial" in cfg.system_prompt.lower() or "challenge" in cfg.system_prompt.lower()

    def test_implementer_prompt_mentions_code(self):
        cfg = get_role_config(AgentRole.IMPLEMENTER)
        assert "code" in cfg.system_prompt.lower() or "implement" in cfg.system_prompt.lower()

    def test_all_prompts_nonempty(self):
        for role in AgentRole:
            cfg = get_role_config(role)
            assert len(cfg.system_prompt.strip()) > 50, (
                f"System prompt for {role.value} is too short"
            )


class TestAC07Compliance:
    """AC-07: team_mode is a string identifier, not a boolean flag."""

    def test_team_modes_are_strings_or_none(self):
        for role in AgentRole:
            cfg = get_role_config(role)
            assert cfg.team_mode is None or isinstance(cfg.team_mode, str), (
                f"AC-07 violation: {role.value} team_mode must be str or None, "
                f"got {type(cfg.team_mode)}"
            )

    def test_team_mode_values_are_valid(self):
        valid_modes = {"coordinate", "broadcast", "route", None}
        for role in AgentRole:
            cfg = get_role_config(role)
            assert cfg.team_mode in valid_modes, (
                f"AC-07: {role.value} has invalid team_mode '{cfg.team_mode}'"
            )
