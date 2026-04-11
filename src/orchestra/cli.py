"""WorkflowAgent CLI entry point (P1-13, DESIGN.md §10.5).

Provides ``orchestra`` command that accepts a task description and
triggers the full 5-stage workflow: Research → Design → Review →
Implement → Integrate.

Uses Agno's WorkflowAgent for conversational interaction with
num_history_runs=4, allowing follow-up queries about previous results
without re-running the workflow.

Gate 0 Constraints:
- AC-05: Workflow constructed with db=SqliteDb (never None)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from agno.workflow import Workflow, WorkflowAgent

from orchestra import __version__
from orchestra.model_resolver import ModelsConfig, instantiate_model, resolve_model
from orchestra.persistence.databases import initialize_databases

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "orchestra.yaml"
DEFAULT_DATA_DIR = ".orchestra"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestra",
        description="Multi-agent development orchestration system",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="Task description to execute",
    )
    parser.add_argument(
        "--model",
        dest="model_override",
        help="L2 spawn override: model ID for all agents (e.g. claude-opus-4-6)",
    )
    parser.add_argument(
        "--project",
        help="Project name for project-level config from orchestra.yaml",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to orchestra.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help=f"Data directory for databases (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def load_config(config_path: str) -> ModelsConfig:
    path = Path(config_path)
    if path.exists():
        return ModelsConfig.from_yaml(path)
    return ModelsConfig()


def resolve_conductor_model(
    config: ModelsConfig,
    *,
    project: str | None = None,
    model_override: str | None = None,
) -> str:
    return resolve_model(
        "conductor",
        config=config,
        project=project,
        spawn_override=model_override,
    )


def build_workflow(
    *,
    config: ModelsConfig,
    project: str | None = None,
    model_override: str | None = None,
    data_dir: str = DEFAULT_DATA_DIR,
) -> Workflow:
    """Assemble the Orchestra pipeline workflow.

    Initializes databases (AC-05), resolves the conductor model,
    and constructs a Workflow with WorkflowAgent as the conversational
    entry point.
    """
    dbs = initialize_databases(base_dir=data_dir)

    conductor_model_id = resolve_conductor_model(
        config, project=project, model_override=model_override,
    )
    conductor_model = instantiate_model(conductor_model_id)

    agent = WorkflowAgent(
        model=conductor_model,
        num_history_runs=4,
        instructions=(
            "You are the Orchestra conductor.\n"
            "- If the user asks about previous results, answer from history.\n"
            "- If the user requests new work, run the workflow.\n"
            "- If the user asks to modify, adjust and re-run."
        ),
    )

    workflow = Workflow(
        name="Orchestra Pipeline",
        agent=agent,
        db=dbs.traces,
    )

    return workflow


def run_task(workflow: Workflow, task: str) -> None:
    workflow.print_response(task)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.task:
        parser.print_help()
        return 1

    config = load_config(args.config)

    workflow = build_workflow(
        config=config,
        project=args.project,
        model_override=args.model_override,
        data_dir=args.data_dir,
    )

    logger.info("Starting Orchestra Pipeline...")
    try:
        run_task(workflow, args.task)
    except KeyboardInterrupt:
        logger.info("\nInterrupted.")
        return 130
    except Exception:
        logger.exception("Workflow failed")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
