"""Command-line interface for read-only Development Loop foundation inspection."""

import argparse
from pathlib import Path
import sys
from typing import Sequence

from .foundation_query import (
    FoundationQueryOutcome,
    FoundationQueryService,
    exit_code_for,
    render_error,
    render_json,
    render_text,
)
from .project_policy_reader import ProjectPolicyReader
from .sqlite_repositories import (
    SqliteApprovalBindingRepository,
    SqliteDevelopmentRunInspectionRepository,
    SqliteMilestoneContractRepository,
    SqlitePhaseContractRepository,
    SqliteProjectPolicyRepository,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Development Loop foundation inspection.")
    parser.add_argument("--db", required=True, metavar="DATABASE_PATH")
    parser.add_argument("--json", action="store_true", dest="as_json")
    commands = parser.add_subparsers(dest="command", required=True)

    phase = commands.add_parser("phase")
    phase.add_argument("project_id")
    phase.add_argument("phase_id")
    _add_json_option(phase)

    milestone = commands.add_parser("milestone")
    milestone.add_argument("project_id")
    milestone.add_argument("phase_id")
    milestone.add_argument("milestone_id")
    _add_json_option(milestone)

    approval = commands.add_parser("approval")
    approval.add_argument("approval_id")
    _add_json_option(approval)

    run = commands.add_parser("run")
    run.add_argument("run_id")
    _add_json_option(run)

    project_policy = commands.add_parser("project-policy")
    project_policy.add_argument("project_id")
    _add_json_option(project_policy)
    return parser


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json", default=argparse.SUPPRESS)


def _service(database_path: Path) -> FoundationQueryService:
    return FoundationQueryService(
        SqlitePhaseContractRepository(database_path),
        SqliteMilestoneContractRepository(database_path),
        SqliteApprovalBindingRepository(database_path),
        SqliteDevelopmentRunInspectionRepository(database_path),
        ProjectPolicyReader(SqliteProjectPolicyRepository(database_path)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    service = _service(Path(arguments.db))
    if arguments.command == "phase":
        result = service.phase(arguments.project_id, arguments.phase_id)
    elif arguments.command == "milestone":
        result = service.milestone(
            arguments.project_id,
            arguments.phase_id,
            arguments.milestone_id,
        )
    elif arguments.command == "approval":
        result = service.approval(arguments.approval_id)
    elif arguments.command == "run":
        result = service.run(arguments.run_id)
    else:
        result = service.project_policy(arguments.project_id)

    if result.outcome is FoundationQueryOutcome.SUCCESS:
        output = render_json(result) if arguments.as_json else render_text(result)
        sys.stdout.write(output)
    else:
        sys.stderr.write(render_error(result.outcome, as_json=arguments.as_json))
    return exit_code_for(result.outcome)


if __name__ == "__main__":
    raise SystemExit(main())
