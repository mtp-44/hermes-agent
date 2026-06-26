#!/usr/bin/env python3
"""Focused Hermes/Open Brain conformance smoke.

Runs the update guard plus the local tests that cover the Open Brain adapter
surface: command registration, query formatting and feedback, generic message
actions, Telegram rendering, boundary capture, and the Open Brain memory
provider. Live Open Brain tools/list probing is opt-in with --live-smoke.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

FOCUSED_TESTS = (
    "tests/test_conftest_session_home.py",
    "tests/scripts/test_hermes_update_guard_feedback_wiring.py",
    "tests/plugins/test_openbrain_commands_plugin.py",
    "tests/plugins/test_openbrain_query_brain_format_plugin.py",
    "tests/plugins/memory/test_openbrain_provider.py",
    "tests/gateway/test_message_actions.py",
    "tests/gateway/test_telegram_open_brain_feedback.py",
    "tests/gateway/test_boundary_capture.py",
    "tests/gateway/test_capture_commands.py",
    "tests/gateway/test_session_boundary_hooks.py",
)


def _repo_python() -> str:
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _run(label: str, cmd: list[str]) -> int:
    print(f"\n== {label} ==")
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    if proc.returncode:
        print(f"\n{label} failed with exit code {proc.returncode}.")
    return proc.returncode


def _guard_args(args: argparse.Namespace) -> list[str]:
    cmd = [_repo_python(), "scripts/hermes_update_guard.py", f"--{args.phase}"]
    if args.allow_dirty:
        cmd.append("--allow-dirty")
    if args.live_smoke:
        cmd.append("--live-smoke")
    return cmd


def _pytest_args(args: argparse.Namespace) -> list[str]:
    cmd = [_repo_python(), "-m", "pytest", *FOCUSED_TESTS]
    for extra_arg in args.pytest_arg:
        cmd.append(extra_arg)
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("pre", "post", "full"),
        default="post",
        help="Which hermes_update_guard phase to run before the focused tests.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Pass --allow-dirty to hermes_update_guard.",
    )
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help="Ask hermes_update_guard to probe live Open Brain tools/list.",
    )
    parser.add_argument(
        "--skip-guard",
        action="store_true",
        help="Run only the focused pytest suite.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Run only hermes_update_guard.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Extra argument to append to the pytest command; repeat as needed.",
    )
    args = parser.parse_args(argv)

    if args.skip_guard and args.skip_tests:
        parser.error("--skip-guard and --skip-tests cannot both be set")

    if not args.skip_guard:
        rc = _run("Hermes update guard", _guard_args(args))
        if rc:
            return rc

    if not args.skip_tests:
        rc = _run("Focused Open Brain/Hermes tests", _pytest_args(args))
        if rc:
            return rc

    print("\nOpen Brain/Hermes conformance smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
