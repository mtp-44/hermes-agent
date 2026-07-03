#!/usr/bin/env python3
"""Focused Hermes/Open Brain conformance smoke.

Runs the update guard plus the local tests that cover the Open Brain adapter
surface: command registration, query formatting and feedback, generic message
actions, Telegram rendering, boundary capture, and the Open Brain memory
provider. Live Open Brain tools/list probing is opt-in with --live-smoke;
--oauth-smoke additionally drives the hosted endpoint's OAuth 2.1 path
(checklist A4) via open_brain/scripts/oauth_smoke.py, so both auth paths —
legacy x-brain-key and bearer token — are covered.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_OPEN_BRAIN_ROOT = Path(
    os.environ.get("OPEN_BRAIN_ROOT", Path.home() / "ai" / "open_brain")
)

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
        "--oauth-smoke",
        action="store_true",
        help="Also run the OAuth 2.1 auth-path smoke against the live hosted "
        "endpoint (open_brain/scripts/oauth_smoke.py).",
    )
    parser.add_argument(
        "--open-brain-root",
        default=str(DEFAULT_OPEN_BRAIN_ROOT),
        help="Path to the open_brain repo for --oauth-smoke "
        "(default: $OPEN_BRAIN_ROOT or ~/ai/open_brain).",
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

    if args.oauth_smoke:
        open_brain_root = Path(args.open_brain_root).expanduser()
        smoke = open_brain_root / "scripts" / "oauth_smoke.py"
        if not smoke.exists():
            print(f"\nOAuth smoke script not found: {smoke}")
            return 1
        cmd = ["uv", "run", "python", str(smoke)]
        print(f"\n== Open Brain OAuth path smoke ==\n{' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=str(open_brain_root), check=False)
        if proc.returncode:
            print(f"\nOAuth path smoke failed with exit code {proc.returncode}.")
            return proc.returncode

    print("\nOpen Brain/Hermes conformance smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
