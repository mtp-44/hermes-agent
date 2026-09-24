"""The update guard's Open Brain live smoke checks tool NAMES, not a count.

Context (2026-09-24, HA-0006): the smoke required at least 30 tools. The
2026-08-31 simplify-in-place plan narrowed Open Brain's default surface to
three, so the check failed every post-update run from 2026-09-01 while Open
Brain was healthy — and a count would still pass if a core tool were swapped
out. It now requires the three tools Hermes depends on, probed with the same
headers the gateway sends.
"""

import json
from pathlib import Path

import pytest

import scripts.hermes_update_guard as guard_mod
from scripts.hermes_update_guard import (
    REQUIRED_OPENBRAIN_TOOLS,
    HermesUpdateGuard,
    missing_openbrain_tools,
    openbrain_probe_headers,
)


def _tools(*names):
    return [{"name": n, "description": "x"} for n in names]


def test_missing_tools_are_reported_by_name():
    assert missing_openbrain_tools(_tools(*REQUIRED_OPENBRAIN_TOOLS)) == []
    assert missing_openbrain_tools(_tools("query_brain", "get_record_document", "x", "y")) == [
        "analyze_brain_query",
        "capture_thought",
    ]


def test_probe_headers_expand_env_and_drop_unresolved():
    server = {
        "headers": {
            "x-brain-key": "${OPENBRAIN_MCP_KEY}",
            "x-brain-profile": "hermes",
            "x-brain-client": "${UNSET_CLIENT_VAR}",
        }
    }
    headers = openbrain_probe_headers(server, {"OPENBRAIN_MCP_KEY": "k-123"})
    assert headers == {"x-brain-key": "k-123", "x-brain-profile": "hermes"}


def _guard(tmp_path: Path, headers: dict) -> HermesUpdateGuard:
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        "mcp_servers:\n"
        "  open_brain:\n"
        "    url: http://127.0.0.1:1/mcp\n"
        "    headers:\n"
        + "".join(f"      {k}: {v!r}\n" for k, v in headers.items())
    )
    (home / ".env").write_text("OPENBRAIN_MCP_KEY=k-123\n")
    return HermesUpdateGuard(hermes_home=home, prod_branch="main", allow_dirty=True, live_smoke=True)


class _Response:
    def __init__(self, tools):
        self._body = json.dumps({"jsonrpc": "2.0", "id": "x", "result": {"tools": tools}}).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def served(monkeypatch):
    """Stub urlopen; record the headers each probe sent."""
    sent = []
    state = {"tools": _tools(*REQUIRED_OPENBRAIN_TOOLS)}

    def fake_urlopen(request, timeout=None):
        sent.append({k.lower(): v for k, v in request.header_items()})
        return _Response(state["tools"])

    monkeypatch.setattr(guard_mod.urllib.request, "urlopen", fake_urlopen)
    return sent, state


def _smoke(guard):
    guard._check_openbrain_live()
    return next(c for c in guard.checks if c.name == "openbrain-live-smoke")


def test_three_required_tools_pass(tmp_path, served):
    check = _smoke(_guard(tmp_path, {"x-brain-key": "${OPENBRAIN_MCP_KEY}"}))
    assert check.status == "pass", check.detail


def test_a_missing_core_tool_fails_even_with_many_tools(tmp_path, served):
    _, state = served
    state["tools"] = _tools("query_brain", "analyze_brain_query", *[f"t{i}" for i in range(40)])
    check = _smoke(_guard(tmp_path, {"x-brain-key": "${OPENBRAIN_MCP_KEY}"}))
    assert check.status == "fail"
    assert "capture_thought" in check.detail


def test_probe_sends_the_gateways_own_headers(tmp_path, served):
    sent, _ = served
    _smoke(_guard(tmp_path, {"x-brain-key": "${OPENBRAIN_MCP_KEY}", "x-brain-profile": "hermes"}))
    assert sent[0]["x-brain-key"] == "k-123"
    assert sent[0]["x-brain-profile"] == "hermes"


def test_no_key_is_a_failure_not_a_probe(tmp_path, served):
    sent, _ = served
    check = _smoke(_guard(tmp_path, {"x-brain-profile": "hermes"}))
    assert check.status == "fail"
    assert sent == []
