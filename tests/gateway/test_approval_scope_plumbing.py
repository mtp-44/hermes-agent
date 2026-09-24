"""The gateway must forward an approval's scope flags to what the user sees.

``tools.approval`` stamps every gateway approval with ``allow_permanent`` (a
pure-tirith prompt cannot be allowlisted permanently) and, for the protected
agent-instruction gate, ``allow_session=False`` (one operation, nothing
persisted). ``gateway/run.py`` dropped both: button adapters were never told,
and the text fallback (Signal, or any adapter whose buttons fail) always
listed session and always. Ported from upstream a3297bd232 and the gateway /
text-fallback half of 02d8cbadec.
"""

import ast
import inspect
import textwrap

import pytest

import gateway.run as gateway_run


def _choices_text(**flags):
    return gateway_run._format_exec_approval_fallback(
        "rm -rf /tmp/x", "recursive delete", "/", **flags
    )


class TestTextFallback:
    def test_default_lists_every_scope(self):
        text = _choices_text()
        assert "`/approve`" in text
        assert "`/approve session`" in text
        assert "`/approve always`" in text
        assert "`/deny`" in text
        assert "rm -rf /tmp/x" in text and "recursive delete" in text

    def test_protected_file_prompt_lists_only_once_and_deny(self):
        text = _choices_text(allow_permanent=False, allow_session=False)
        assert "`/approve`" in text
        assert "`/deny`" in text
        assert "approve session" not in text
        assert "approve always" not in text

    def test_tirith_only_prompt_hides_always_keeps_session(self):
        text = _choices_text(allow_permanent=False)
        assert "`/approve session`" in text
        assert "approve always" not in text

    def test_uses_the_adapter_prefix(self):
        text = gateway_run._format_exec_approval_fallback(
            "ls", "d", "!", allow_permanent=False, allow_session=False,
        )
        assert "`!approve`" in text and "`!deny`" in text
        assert "`/" not in text

    def test_long_command_is_previewed(self):
        text = gateway_run._format_exec_approval_fallback("x" * 500, "d", "/")
        assert "x" * 200 + "..." in text
        assert "x" * 201 not in text


class _Modern:
    async def send_exec_approval(self, chat_id, command, session_key,
                                 description="d", metadata=None,
                                 allow_permanent=True, allow_session=True,
                                 request_id=None):
        pass


class _Kwargs:
    async def send_exec_approval(self, chat_id, command, session_key, **kwargs):
        pass


class _Legacy:
    async def send_exec_approval(self, chat_id, command, session_key,
                                 description="d", metadata=None):
        pass


class _PermanentOnly:
    async def send_exec_approval(self, chat_id, command, session_key,
                                 description="d", metadata=None,
                                 allow_permanent=True):
        pass


class TestScopeKwargs:
    def test_adapter_that_accepts_both_gets_both(self):
        data = {"allow_permanent": False, "allow_session": False}
        assert gateway_run._exec_approval_scope_kwargs(_Modern(), data) == {
            "allow_permanent": False, "allow_session": False,
        }

    def test_missing_flags_default_to_allowed(self):
        assert gateway_run._exec_approval_scope_kwargs(_Modern(), {}) == {
            "allow_permanent": True, "allow_session": True,
        }

    def test_var_kwargs_adapter_gets_both(self):
        data = {"allow_permanent": False, "allow_session": False}
        assert gateway_run._exec_approval_scope_kwargs(_Kwargs(), data) == {
            "allow_permanent": False, "allow_session": False,
        }

    def test_legacy_adapter_is_called_exactly_as_before(self):
        data = {"allow_permanent": False, "allow_session": False}
        assert gateway_run._exec_approval_scope_kwargs(_Legacy(), data) == {}

    def test_only_accepted_flags_are_passed(self):
        data = {"allow_permanent": False, "allow_session": False}
        assert gateway_run._exec_approval_scope_kwargs(_PermanentOnly(), data) == {
            "allow_permanent": False,
        }

    def test_non_bool_values_are_coerced(self):
        data = {"allow_permanent": 0, "allow_session": None}
        out = gateway_run._exec_approval_scope_kwargs(_Modern(), data)
        assert out == {"allow_permanent": False, "allow_session": False}

    def test_request_id_binding_is_unchanged(self):
        """P1 (HA-0009) request binding stays a separate, exact helper."""
        assert gateway_run._exec_approval_request_kwargs(
            _Modern(), {"request_id": "r", "allow_session": False}
        ) == {"request_id": "r"}


def _notify_sync_source() -> ast.AST:
    src = textwrap.dedent(inspect.getsource(gateway_run))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_approval_notify_sync":
            return node
    raise AssertionError("_approval_notify_sync not found in gateway/run.py")


class TestNotifyWiring:
    """Guard the production closure: both transports must use the scope."""

    def test_button_path_passes_scope_kwargs(self):
        fn = _notify_sync_source()
        names = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_exec_approval_scope_kwargs" in names

    def test_text_path_renders_from_the_approval_data(self):
        fn = _notify_sync_source()
        calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_format_exec_approval_fallback"
        ]
        assert calls, "text fallback no longer rendered from scope flags"
        kw = {k.arg for k in calls[0].keywords}
        assert {"allow_permanent", "allow_session"} <= kw
