#!/usr/bin/env python3
"""Focused Hermes/Open Brain conformance smoke.

Runs the update guard plus the local tests that cover the Open Brain adapter
surface: command registration, query formatting and feedback, generic message
actions, Telegram and Signal feedback delivery, boundary capture, and the Open
Brain memory provider. Live Open Brain tools/list probing is opt-in with --live-smoke;
--oauth-smoke additionally drives the hosted endpoint's OAuth 2.1 path
(checklist A4) via open_brain/scripts/oauth_smoke.py, so both auth paths —
legacy x-brain-key and bearer token — are covered.  The real Signal adapter
smoke is a separate, explicitly guarded opt-in via --signal-e2e; the default
offline run remains deterministic and never contacts signal-cli.
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
    # These tests load plugins by path, so a green run does not prove the
    # deployed tree carries the wiring (WP3, 2026-07-27). The discovered-plugin
    # guard tests pin the check that closes that gap.
    "tests/scripts/test_hermes_update_guard_discovered_plugin.py",
    "tests/plugins/test_openbrain_commands_plugin.py",
    "tests/plugins/test_openbrain_query_brain_format_plugin.py",
    "tests/plugins/memory/test_openbrain_provider.py",
    "tests/gateway/test_message_actions.py",
    "tests/gateway/test_telegram_open_brain_feedback.py",
    # Signal reaction feedback is deterministic here: its adapter tests cover
    # durable timestamp correlation, strict authorization, replay/replacement,
    # expiry, and dispatch into the real Open Brain feedback handler.
    "tests/gateway/test_signal.py",
    # The digest's commitment actions are multi-choice, so on Signal they are
    # numbered reply commands rather than reactions (WP4, 2026-07-28). Same
    # generic action seam, same fail-closed authorization.
    "tests/gateway/test_signal_numbered_actions.py",
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
    parser.add_argument(
        "--signal-e2e",
        action="store_true",
        help="Run the guarded real Signal adapter smoke after offline checks.",
    )
    parser.add_argument(
        "--signal-e2e-secrets-file",
        help="Mode-0600 Signal identifier handoff required by --signal-e2e.",
    )
    parser.add_argument(
        "--signal-e2e-hermes-home",
        help="Isolated non-production HERMES_HOME required by --signal-e2e.",
    )
    parser.add_argument(
        "--signal-e2e-timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for the Signal reply (default: 180).",
    )
    args = parser.parse_args(argv)

    if args.skip_guard and args.skip_tests:
        parser.error("--skip-guard and --skip-tests cannot both be set")
    if args.signal_e2e and (
        not args.signal_e2e_secrets_file or not args.signal_e2e_hermes_home
    ):
        parser.error(
            "--signal-e2e requires --signal-e2e-secrets-file and "
            "--signal-e2e-hermes-home"
        )

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

    if args.signal_e2e:
        cmd = [
            _repo_python(),
            "scripts/signal_e2e_smoke.py",
            "--live",
            "--secrets-file",
            args.signal_e2e_secrets_file,
            "--hermes-home",
            args.signal_e2e_hermes_home,
            "--timeout",
            str(args.signal_e2e_timeout),
        ]
        rc = _run("Guarded Signal adapter E2E", cmd)
        if rc:
            return rc

    print("\nOpen Brain/Hermes conformance smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
