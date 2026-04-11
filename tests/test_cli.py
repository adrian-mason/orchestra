"""Tests for orchestra.cli (P1-13 WorkflowAgent CLI entry point)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from orchestra.cli import (
    build_arg_parser,
    build_workflow,
    load_config,
    main,
    resolve_conductor_model,
)
from orchestra.model_resolver import ModelsConfig


class TestArgParser:
    def test_task_argument(self):
        parser = build_arg_parser()
        args = parser.parse_args(["Design a caching layer"])
        assert args.task == "Design a caching layer"

    def test_model_override(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--model", "claude-opus-4-6", "some task"])
        assert args.model_override == "claude-opus-4-6"

    def test_project_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--project", "myproject", "some task"])
        assert args.project == "myproject"

    def test_config_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--config", "/path/to/config.yaml", "task"])
        assert args.config == "/path/to/config.yaml"

    def test_data_dir_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["--data-dir", "/tmp/orchestra", "task"])
        assert args.data_dir == "/tmp/orchestra"

    def test_verbose_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["-v", "task"])
        assert args.verbose is True

    def test_defaults(self):
        parser = build_arg_parser()
        args = parser.parse_args(["task"])
        assert args.model_override is None
        assert args.project is None
        assert args.config == "orchestra.yaml"
        assert args.data_dir == ".orchestra"
        assert args.verbose is False

    def test_no_task_gives_none(self):
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.task is None


class TestLoadConfig:
    def test_missing_file_returns_empty_config(self, tmp_path):
        config = load_config(str(tmp_path / "nonexistent.yaml"))
        assert config.global_default is None
        assert config.roles == {}
        assert config.projects == {}

    def test_loads_valid_yaml(self, tmp_path):
        yaml_path = tmp_path / "orchestra.yaml"
        yaml_path.write_text(
            "models:\n"
            "  global_default: claude-sonnet-4-6\n"
            "  roles:\n"
            "    conductor: claude-opus-4-6\n"
        )
        config = load_config(str(yaml_path))
        assert config.global_default == "claude-sonnet-4-6"
        assert config.roles["conductor"] == "claude-opus-4-6"


class TestResolveConductorModel:
    def test_uses_spawn_override(self):
        config = ModelsConfig()
        result = resolve_conductor_model(
            config, model_override="claude-opus-4-6",
        )
        assert result == "claude-opus-4-6"

    def test_uses_yaml_role_config(self):
        config = ModelsConfig(roles={"conductor": "gemini-pro"})
        result = resolve_conductor_model(config)
        assert result == "gemini-pro"

    def test_uses_project_role_config(self):
        from orchestra.model_resolver import ProjectConfig

        config = ModelsConfig(
            projects={"myproj": ProjectConfig(roles={"conductor": "gpt-4o"})},
        )
        result = resolve_conductor_model(config, project="myproj")
        assert result == "gpt-4o"

    def test_falls_back_to_hardcoded(self):
        config = ModelsConfig()
        result = resolve_conductor_model(config)
        assert result == "claude-sonnet-4-6"

    def test_spawn_override_beats_yaml(self):
        config = ModelsConfig(roles={"conductor": "gemini-pro"})
        result = resolve_conductor_model(
            config, model_override="claude-opus-4-6",
        )
        assert result == "claude-opus-4-6"


class TestBuildWorkflow:
    @patch("orchestra.cli.Workflow")
    @patch("orchestra.cli.WorkflowAgent")
    @patch("orchestra.cli.initialize_databases")
    @patch("orchestra.cli.instantiate_model")
    def test_constructs_workflow_with_db(
        self, mock_inst_model, mock_init_dbs, mock_wf_agent, mock_workflow, tmp_path,
    ):
        mock_inst_model.return_value = MagicMock()
        mock_dbs = MagicMock()
        mock_dbs.traces = MagicMock()
        mock_init_dbs.return_value = mock_dbs

        config = ModelsConfig()
        build_workflow(config=config, data_dir=str(tmp_path / "data"))

        mock_init_dbs.assert_called_once_with(base_dir=str(tmp_path / "data"))
        mock_workflow.assert_called_once()
        call_kwargs = mock_workflow.call_args[1]
        assert call_kwargs["name"] == "Orchestra Pipeline"
        assert call_kwargs["db"] is mock_dbs.traces

    @patch("orchestra.cli.Workflow")
    @patch("orchestra.cli.WorkflowAgent")
    @patch("orchestra.cli.initialize_databases")
    @patch("orchestra.cli.instantiate_model")
    def test_passes_model_override(
        self, mock_inst_model, mock_init_dbs, mock_wf_agent, mock_workflow, tmp_path,
    ):
        mock_inst_model.return_value = MagicMock()
        mock_init_dbs.return_value = MagicMock(traces=MagicMock())

        config = ModelsConfig()
        build_workflow(
            config=config,
            model_override="claude-opus-4-6",
            data_dir=str(tmp_path / "data"),
        )

        mock_inst_model.assert_called_once_with("claude-opus-4-6")

    @patch("orchestra.cli.Workflow")
    @patch("orchestra.cli.WorkflowAgent")
    @patch("orchestra.cli.initialize_databases")
    @patch("orchestra.cli.instantiate_model")
    def test_passes_project_config(
        self, mock_inst_model, mock_init_dbs, mock_wf_agent, mock_workflow, tmp_path,
    ):
        from orchestra.model_resolver import ProjectConfig

        mock_inst_model.return_value = MagicMock()
        mock_init_dbs.return_value = MagicMock(traces=MagicMock())

        config = ModelsConfig(
            projects={"myproj": ProjectConfig(roles={"conductor": "gpt-4o"})},
        )
        build_workflow(
            config=config,
            project="myproj",
            data_dir=str(tmp_path / "data"),
        )

        mock_inst_model.assert_called_once_with("gpt-4o")

    @patch("orchestra.cli.Workflow")
    @patch("orchestra.cli.WorkflowAgent")
    @patch("orchestra.cli.initialize_databases")
    @patch("orchestra.cli.instantiate_model")
    def test_workflow_agent_constructed_with_history(
        self, mock_inst_model, mock_init_dbs, mock_wf_agent, mock_workflow, tmp_path,
    ):
        mock_inst_model.return_value = MagicMock()
        mock_init_dbs.return_value = MagicMock(traces=MagicMock())

        config = ModelsConfig()
        build_workflow(config=config, data_dir=str(tmp_path / "data"))

        mock_wf_agent.assert_called_once()
        call_kwargs = mock_wf_agent.call_args[1]
        assert call_kwargs["num_history_runs"] == 4


class TestMain:
    @patch("orchestra.cli.build_workflow")
    @patch("orchestra.cli.load_config")
    def test_no_task_returns_1(self, mock_load, mock_build):
        result = main([])
        assert result == 1
        mock_build.assert_not_called()

    @patch("orchestra.cli.run_task")
    @patch("orchestra.cli.build_workflow")
    @patch("orchestra.cli.load_config")
    def test_runs_task_successfully(self, mock_load, mock_build, mock_run):
        mock_load.return_value = ModelsConfig()
        mock_wf = MagicMock()
        mock_build.return_value = mock_wf

        result = main(["Design a caching layer"])
        assert result == 0
        mock_run.assert_called_once_with(mock_wf, "Design a caching layer")

    @patch("orchestra.cli.run_task", side_effect=Exception("API error"))
    @patch("orchestra.cli.build_workflow")
    @patch("orchestra.cli.load_config")
    def test_exception_returns_1(self, mock_load, mock_build, mock_run):
        mock_load.return_value = ModelsConfig()
        mock_build.return_value = MagicMock()

        result = main(["some task"])
        assert result == 1

    @patch("orchestra.cli.run_task", side_effect=KeyboardInterrupt)
    @patch("orchestra.cli.build_workflow")
    @patch("orchestra.cli.load_config")
    def test_keyboard_interrupt_returns_130(self, mock_load, mock_build, mock_run):
        mock_load.return_value = ModelsConfig()
        mock_build.return_value = MagicMock()

        result = main(["some task"])
        assert result == 130

    @patch("orchestra.cli.run_task")
    @patch("orchestra.cli.build_workflow")
    @patch("orchestra.cli.load_config")
    def test_model_override_passed_through(self, mock_load, mock_build, mock_run):
        mock_load.return_value = ModelsConfig()
        mock_build.return_value = MagicMock()

        main(["--model", "claude-opus-4-6", "task"])

        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["model_override"] == "claude-opus-4-6"

    @patch("orchestra.cli.run_task")
    @patch("orchestra.cli.build_workflow")
    @patch("orchestra.cli.load_config")
    def test_project_passed_through(self, mock_load, mock_build, mock_run):
        mock_load.return_value = ModelsConfig()
        mock_build.return_value = MagicMock()

        main(["--project", "myproj", "task"])

        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["project"] == "myproj"
