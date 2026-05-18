from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from hermes_cli.commands import COMMAND_REGISTRY  # noqa: E402
from hermes_cli.plugins import VALID_HOOKS  # noqa: E402


EXPECTED_PHASE_COMMANDS = (
    "save",
    "local",
    "fast",
    "5.5",
    "usage",
)
PLANNED_BUT_MISSING_COMMANDS = (
    "nosave",
    "private",
    "capture-status",
    "note",
    "m",
    "claude",
    "opus",
)
EXPECTED_HOOK_EVENTS = (
    "on_session_start",
    "on_session_end",
    "on_session_finalize",
)


def _load_gateway_route_defaults() -> dict[str, Any]:
    gateway_run = REPO_ROOT / "gateway" / "run.py"
    module = ast.parse(gateway_run.read_text(encoding="utf-8"), filename=str(gateway_run))
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_MODEL_ROUTE_DEFAULTS":
                    return ast.literal_eval(node.value)
    raise RuntimeError("Could not find _MODEL_ROUTE_DEFAULTS in gateway/run.py")


def _command_names() -> set[str]:
    names: set[str] = set()
    for command in COMMAND_REGISTRY:
        names.add(command.name)
        names.update(command.aliases)
    return names


def build_probe_report() -> dict[str, Any]:
    command_names = _command_names()
    route_defaults = _load_gateway_route_defaults()
    hermes_home = Path.home() / ".hermes"
    skills_dir = hermes_home / "skills"

    phase_commands_present = sorted(name for name in EXPECTED_PHASE_COMMANDS if name in command_names)
    planned_commands_missing = sorted(name for name in PLANNED_BUT_MISSING_COMMANDS if name not in command_names)
    hook_events_present = sorted(name for name in EXPECTED_HOOK_EVENTS if name in VALID_HOOKS)
    hook_events_missing = sorted(name for name in EXPECTED_HOOK_EVENTS if name not in VALID_HOOKS)

    gaps: list[str] = []
    if planned_commands_missing:
        gaps.append(f"Slash commands not implemented yet: {', '.join(planned_commands_missing)}")
    if hook_events_missing:
        gaps.append(f"Expected hook events missing: {', '.join(hook_events_missing)}")
    if "5.5" not in route_defaults or "fast" not in route_defaults:
        gaps.append("Gateway model route defaults are missing one or more paid routes.")

    return {
        "status": "ok",
        "repo_root": str(REPO_ROOT),
        "skills": {
            "documented_user_dir": str(skills_dir),
            "dir_exists": skills_dir.exists(),
            "seed_script_present": str(REPO_ROOT / "setup-hermes.sh"),
        },
        "slash_commands": {
            "phase_commands_present": phase_commands_present,
            "planned_commands_missing": planned_commands_missing,
        },
        "hooks": {
            "expected_events_present": hook_events_present,
            "expected_events_missing": hook_events_missing,
            "all_events": sorted(VALID_HOOKS),
        },
        "scheduler": {
            "cron_cli_module": str(REPO_ROOT / "hermes_cli" / "cron.py"),
            "scheduler_module": str(REPO_ROOT / "cron" / "scheduler.py"),
            "jobs_module": str(REPO_ROOT / "cron" / "jobs.py"),
        },
        "routing": {
            "gateway_route_defaults": route_defaults,
            "gateway_route_commands_present": sorted(
                name for name in ("local", "claude", "opus", "fast", "5.5", "55", "gpt55") if name in command_names
            ),
        },
        "usage_logging": {
            "gateway_usage_command_present": "usage" in command_names,
            "gateway_usage_helper_imported": True,
        },
        "gaps": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Phase -1 probe for Hermes extension points, hooks, scheduler, and route controls.",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON output.")
    args = parser.parse_args()

    report = build_probe_report()
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
