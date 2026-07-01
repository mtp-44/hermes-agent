"""Tests for the routing-classifier plugin."""

import importlib.util
from pathlib import Path
import sys

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "routing-classifier"
_spec = importlib.util.spec_from_file_location(
    "routing_classifier_plugin", _PLUGIN_DIR / "__init__.py"
)
rc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rc
_spec.loader.exec_module(rc)


class _Ctx:
    def __init__(self):
        self.commands = {}
        self.hooks = {}

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = {
            "handler": handler,
            "description": description,
            "args_hint": args_hint,
        }

    def register_hook(self, name, handler):
        self.hooks[name] = handler


def test_register_adds_route_command():
    ctx = _Ctx()
    rc.register(ctx)
    assert set(ctx.commands) == {"route"}
    assert callable(ctx.commands["route"]["handler"])
    assert ctx.commands["route"]["args_hint"] == "<task text>"
    assert set(ctx.hooks) == {"pre_llm_call"}
    assert callable(ctx.hooks["pre_llm_call"])


def test_empty_route_command_shows_usage():
    assert rc._handle_route("") == "Usage: /route <task text>"


def test_commit_message_routes_local():
    decision = rc.classify_message("write a conventional commit message for this diff")
    assert decision.route == "local_by_default"
    assert decision.task_type == "commit_msg"
    assert decision.target == "local"
    assert decision.confidence == "high"
    assert decision.requires_confirmation is False


def test_docstring_routes_local():
    decision = rc.classify_message("add a docstring for confirm_new_contact")
    assert decision.route == "local_by_default"
    assert decision.task_type == "docstring"


def test_doc_edit_routes_local():
    decision = rc.classify_message("tighten the wording in the README")
    assert decision.route == "local_by_default"
    assert decision.task_type == "doc_edit"


def test_code_trace_routes_local():
    decision = rc.classify_message("where is run_mcp_server used?")
    assert decision.route == "local_by_default"
    assert decision.task_type == "code_trace"


def test_diff_summary_routes_local_with_flag():
    decision = rc.classify_message("what changed?", has_diff=True)
    assert decision.route == "local_by_default"
    assert decision.task_type == "diff_summary"


def test_test_scaffold_stays_on_hold():
    decision = rc.classify_message("scaffold tests for confirm_new_contact")
    assert decision.route == "uncertain"
    assert decision.task_type == "test_scaffold"
    assert decision.target == "none"
    assert "on hold" in decision.reason


def test_auth_change_hard_escalates():
    decision = rc.classify_message("change the OAuth token handling")
    assert decision.route == "hard_escalate"
    assert decision.target == "opus"
    assert decision.requires_confirmation is True


def test_database_migration_hard_escalates():
    decision = rc.classify_message("add a migration for work_sessions")
    assert decision.route == "hard_escalate"
    assert "Database" in decision.reason


def test_public_api_contract_hard_escalates():
    decision = rc.classify_message("modify the MCP contract for session_create")
    assert decision.route == "hard_escalate"
    assert "Public API" in decision.reason


def test_concurrency_hard_escalates():
    decision = rc.classify_message("fix the scheduler race in cron dispatch")
    assert decision.route == "hard_escalate"
    assert "Concurrency" in decision.reason


def test_cross_repo_hard_escalates():
    decision = rc.classify_message("implement the Open Brain and Hermes architecture change")
    assert decision.route == "hard_escalate"
    assert "Cross-repo" in decision.reason


def test_ambiguous_coding_request_is_uncertain():
    decision = rc.classify_message("fix the thing in the gateway")
    assert decision.route == "uncertain"
    assert decision.confidence == "low"


def test_auto_preflight_default_off(monkeypatch):
    monkeypatch.delenv("HERMES_ROUTING_PREFLIGHT", raising=False)
    monkeypatch.setattr(rc, "_auto_preflight_enabled", lambda: False)
    assert rc.on_pre_llm_call(user_message="add a migration") is None


def test_auto_preflight_env_on_injects_context(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTING_PREFLIGHT", "1")
    result = rc.on_pre_llm_call(user_message="add a migration for work_sessions")
    assert result is not None
    context = result["context"]
    assert "[Routing preflight]" in context
    assert "Route: hard_escalate" in context
    assert "Mode: propose-only" in context
    assert "recommend a confirmed frontier call" in context


def test_auto_preflight_ignores_slash_commands(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTING_PREFLIGHT", "1")
    assert rc.on_pre_llm_call(user_message="/help") is None


def test_auto_preflight_local_context(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTING_PREFLIGHT", "true")
    result = rc.on_pre_llm_call(user_message="write a commit message for this diff")
    assert result is not None
    context = result["context"]
    assert "Route: local_by_default" in context
    assert "handle locally" in context


def test_auto_preflight_env_off_overrides(monkeypatch):
    monkeypatch.setenv("HERMES_ROUTING_PREFLIGHT", "off")
    assert rc._auto_preflight_enabled() is False
