"""A gate that grants one operation must not be offered a session scope.

The protected agent-instruction gate (``tools/file_tools.py``) treats every
approval as one operation and persists nothing. The CLI panel and the input()
fallback still offered "session" (and the callback was never told otherwise),
so a user who picked it was re-prompted on the next write and read the gate as
broken. ``prompt_dangerous_approval`` now takes ``allow_session``; False
collapses every CLI surface to once/deny. Ported from upstream 165d1849e2.
"""

from unittest.mock import patch

import pytest

from tools.approval import prompt_dangerous_approval


class TestPromptDangerousApprovalAllowSession:
    def test_callback_is_told_when_session_is_not_allowed(self):
        seen = {}

        def cb(command, description, **kwargs):
            seen.update(kwargs)
            return "once"

        assert prompt_dangerous_approval(
            "<write to AGENTS.md>", "protected file",
            allow_permanent=False, allow_session=False, approval_callback=cb,
        ) == "once"
        assert seen == {"allow_permanent": False, "allow_session": False}

    def test_legacy_callback_without_allow_session_still_works_by_default(self):
        def legacy_cb(command, description, *, allow_permanent=True):
            return "session"

        assert prompt_dangerous_approval(
            "rm -rf /tmp/x", "recursive delete", approval_callback=legacy_cb,
        ) == "session"

    @pytest.mark.parametrize("typed", ["s", "session", "a", "always"])
    def test_input_fallback_does_not_accept_a_session_or_always_scope(self, typed, capsys):
        with patch("builtins.input", return_value=typed):
            choice = prompt_dangerous_approval(
                "<write to AGENTS.md>", "protected file",
                allow_permanent=False, allow_session=False, timeout_seconds=5,
            )
        assert choice == "deny"
        out = capsys.readouterr().out
        assert "[s]" not in out and "[a]" not in out

    def test_input_fallback_once_still_allows(self, capsys):
        with patch("builtins.input", return_value="o"):
            choice = prompt_dangerous_approval(
                "<write to AGENTS.md>", "protected file",
                allow_permanent=False, allow_session=False, timeout_seconds=5,
            )
        assert choice == "once"
        out = capsys.readouterr().out
        assert "[o]" in out and "[d]" in out
        assert "[s]" not in out

    def test_default_prompt_still_offers_session(self, capsys):
        with patch("builtins.input", return_value="s"):
            choice = prompt_dangerous_approval(
                "rm -rf /tmp/x", "recursive delete", timeout_seconds=5,
            )
        assert choice == "session"
        assert "[s]" in capsys.readouterr().out
