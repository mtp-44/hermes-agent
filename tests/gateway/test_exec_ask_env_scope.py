"""``HERMES_EXEC_ASK`` belongs to a running gateway, not to importing its module.

``gateway/run.py`` used to set ``HERMES_EXEC_ASK=1`` at import time. The CLI
imports it incidentally (``send_message`` reaches ``_gateway_runner_ref``), and
from then on every dangerous command in that CLI process took the ask/gateway
branch and returned ``pending_approval`` with no notifier and no panel: a
lockout. Ported from upstream e37a0321eb.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_importing_gateway_run_does_not_set_exec_ask(tmp_path):
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("HERMES_EXEC_ASK", "HERMES_GATEWAY_SESSION", "HERMES_INTERACTIVE")
    }
    env["HERMES_HOME"] = str(tmp_path / "hermes_home")
    env["HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    (tmp_path / "hermes_home").mkdir()
    probe = (
        "import os, gateway.run\n"
        "print('EXEC_ASK=' + repr(os.environ.get('HERMES_EXEC_ASK')))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "EXEC_ASK=None" in out.stdout, out.stdout[-2000:]


def test_start_gateway_sets_exec_ask_before_anything_else(monkeypatch):
    import gateway.code_skew
    import gateway.run as gateway_run

    class _Stop(Exception):
        pass

    def _stop():
        raise _Stop()

    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    monkeypatch.setattr(gateway.code_skew, "record_boot_fingerprint", _stop)

    with pytest.raises(_Stop):
        asyncio.run(gateway_run.start_gateway())

    assert os.environ.get("HERMES_EXEC_ASK") == "1"


def test_tui_gateway_still_enables_its_own_prompts(monkeypatch):
    from tui_gateway import server

    monkeypatch.delenv("HERMES_EXEC_ASK", raising=False)
    server._enable_gateway_prompts()
    assert os.environ.get("HERMES_EXEC_ASK") == "1"
