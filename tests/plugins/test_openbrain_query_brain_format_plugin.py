from __future__ import annotations

import json
from pathlib import Path

import pytest


PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "openbrain-query-brain-format" / "__init__.py"


def _load_plugin_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("openbrain_query_brain_format_plugin", PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wrap_payload(payload: dict) -> str:
    return json.dumps({"result": json.dumps(payload)}, ensure_ascii=False)


def test_rewrite_result_returns_direct_aggregate_summary():
    plugin = _load_plugin_module()
    payload = {
        "query": "How far did I ride last month?",
        "results": [
            {
                "table": "records",
                "id": "aggregate:last_month:distance",
                "score": 0.99,
                "content_summary": "Ride summary for last month: 27 records totaling 1903.24 km.",
                "metadata": {"aggregate": True},
            },
        ],
        "warnings": [],
    }

    rewritten = plugin._rewrite_result(_wrap_payload(payload))

    assert json.loads(rewritten) == {"result": "Ride summary for last month: 27 records totaling 1903.24 km."}


def test_rewrite_result_returns_honest_empty_reply():
    plugin = _load_plugin_module()
    payload = {"query": "When did I last mention Sam?", "results": [], "warnings": []}

    rewritten = plugin._rewrite_result(_wrap_payload(payload))

    assert json.loads(rewritten) == {"result": "I couldn't find a confident answer in your brain for that."}


def test_rewrite_result_mentions_limited_retrieval():
    plugin = _load_plugin_module()
    payload = {
        "query": "When did I last mention Sam?",
        "results": [],
        "warnings": ["semantic_unavailable"],
    }

    rewritten = plugin._rewrite_result(_wrap_payload(payload))

    assert "semantic retrieval was unavailable" in json.loads(rewritten)["result"]


def test_rewrite_result_names_leads_when_low_confidence_and_limited():
    plugin = _load_plugin_module()
    payload = {
        "query": "give me a quick summary of sprints 98 and 99",
        "results": [
            {
                "table": "thoughts",
                "id": "thought-sprint",
                "score": 0.74,
                "content_summary": "Amnia AMP — Sprint 97, 98 & 99 End-of-Sprint Reports.",
                "metadata": {},
            },
        ],
        "warnings": ["semantic_unavailable"],
    }

    rewritten = json.loads(plugin._rewrite_result(_wrap_payload(payload)))["result"]

    assert "Amnia AMP — Sprint 97, 98 & 99 End-of-Sprint Reports." in rewritten
    assert "tentatively" in rewritten
    assert "semantic retrieval was unavailable" in rewritten


def test_rewrite_result_low_confidence_limited_without_summaries():
    plugin = _load_plugin_module()
    payload = {
        "query": "anything about project X?",
        "results": [
            {"table": "thoughts", "id": "t1", "score": 0.6, "content_summary": "", "metadata": {}},
        ],
        "warnings": ["semantic_unavailable"],
    }

    rewritten = json.loads(plugin._rewrite_result(_wrap_payload(payload)))["result"]

    assert "couldn't find a confident answer" in rewritten
    assert "semantic retrieval was unavailable" in rewritten


def test_rewrite_result_leaves_normal_results_unchanged():
    plugin = _load_plugin_module()
    payload = {
        "query": "What did I say about the garage trainer?",
        "results": [
            {
                "table": "thoughts",
                "id": "thought-1",
                "score": 0.91,
                "content_summary": "You said the garage trainer setup was noisy but usable.",
                "metadata": {},
            },
        ],
        "warnings": [],
    }
    original = _wrap_payload(payload)

    rewritten = plugin._rewrite_result(original)

    assert rewritten == original


def _register_and_capture(plugin):
    captured: list[tuple[str, object]] = []

    class DummyCtx:
        def register_hook(self, hook_name: str, callback):
            captured.append((hook_name, callback))

    plugin.register(DummyCtx())
    return captured


def test_register_adds_post_tool_call_and_transform_hooks():
    plugin = _load_plugin_module()

    captured = _register_and_capture(plugin)

    assert [name for name, _ in captured] == ["post_tool_call", "transform_tool_result"]


def test_post_tool_call_captures_query_brain_candidate(monkeypatch):
    plugin = _load_plugin_module()
    calls: list[str] = []
    monkeypatch.setattr(
        plugin, "capture_query_brain_feedback_candidate", lambda **_: calls.append("query")
    )
    monkeypatch.setattr(
        plugin, "capture_analyze_brain_feedback_candidate", lambda **_: calls.append("analyze")
    )

    post_tool_call = dict(_register_and_capture(plugin))["post_tool_call"]
    post_tool_call(
        tool_name="mcp_open_brain_query_brain",
        args={"query": "x"},
        result="{}",
        session_id="s",
        tool_call_id="t",
    )

    assert calls == ["query"]


def test_post_tool_call_captures_analyze_brain_candidate(monkeypatch):
    plugin = _load_plugin_module()
    calls: list[str] = []
    monkeypatch.setattr(
        plugin, "capture_query_brain_feedback_candidate", lambda **_: calls.append("query")
    )
    monkeypatch.setattr(
        plugin, "capture_analyze_brain_feedback_candidate", lambda **_: calls.append("analyze")
    )

    post_tool_call = dict(_register_and_capture(plugin))["post_tool_call"]
    post_tool_call(
        tool_name="mcp_open_brain_analyze_brain_query",
        args={"question": "x"},
        result="{}",
        session_id="s",
        tool_call_id="t",
    )

    assert calls == ["analyze"]


# --- Query feedback via the generic message-action seam (Phase 5c.3 step 2) ---

def test_decorate_outbound_skips_non_telegram_and_missing():
    plugin = _load_plugin_module()
    assert plugin._decorate_outbound({"platform": "slack", "session_id": "s"}) is None
    assert plugin._decorate_outbound({"platform": "telegram", "session_id": ""}) is None


def test_decorate_outbound_attaches_feedback_actions(monkeypatch):
    plugin = _load_plugin_module()
    candidate = {"query_id": "q1", "query_text": "hi", "source": "hermes"}
    monkeypatch.setattr(plugin, "pop_feedback_candidate", lambda session_id, since=None: candidate)

    actions = plugin._decorate_outbound(
        {"platform": "telegram", "session_id": "s", "since": 1.0}
    )
    assert [a["action_id"] for a in actions] == ["obg", "obb"]
    tok = actions[0]["token"]
    assert actions[1]["token"] == tok
    # Token resolves once to the candidate (one-shot).
    assert plugin._resolve_feedback(tok) == candidate
    assert plugin._resolve_feedback(tok) is None


def test_decorate_outbound_none_when_no_candidate(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setattr(plugin, "pop_feedback_candidate", lambda session_id, since=None: None)
    assert plugin._decorate_outbound({"platform": "telegram", "session_id": "s"}) is None


@pytest.mark.asyncio
async def test_handle_feedback_records_and_acks(monkeypatch):
    plugin = _load_plugin_module()
    import gateway.open_brain as ob

    recorded = {}

    async def _record(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(ob, "record_query_feedback", _record)

    token = plugin._register_feedback(
        {"query_id": "q9", "query_text": "ride?", "result_kind": "life_items",
         "result_id": "r1", "response_verdict": "answer", "source": "hermes"}
    )
    msg = await plugin._handle_feedback("obg", token, {})
    assert msg == "Logged 👍"
    assert recorded["verdict"] == "good" and recorded["query_id"] == "q9"
    # One-shot: a second press finds nothing.
    assert await plugin._handle_feedback("obg", token, {}) == "This feedback prompt expired."


@pytest.mark.asyncio
async def test_handle_feedback_bad_verdict(monkeypatch):
    plugin = _load_plugin_module()
    import gateway.open_brain as ob
    seen = {}
    async def _record(**kwargs):
        seen.update(kwargs)
    monkeypatch.setattr(ob, "record_query_feedback", _record)
    token = plugin._register_feedback({"query_id": "q", "query_text": "t", "source": "hermes"})
    assert await plugin._handle_feedback("obb", token, {}) == "Logged 👎"
    assert seen["verdict"] == "bad"


def test_register_wires_decorator_and_action_handlers():
    plugin = _load_plugin_module()

    class RichCtx:
        def __init__(self):
            self.hooks = {}
            self.decorators = []
            self.action_handlers = {}

        def register_hook(self, name, cb):
            self.hooks[name] = cb

        def register_outbound_decorator(self, cb):
            self.decorators.append(cb)

        def register_action_handler(self, action_id, cb):
            self.action_handlers[action_id] = cb

    ctx = RichCtx()
    plugin.register(ctx)
    assert ctx.decorators == [plugin._decorate_outbound]
    assert set(ctx.action_handlers) == {"obg", "obb"}
    assert ctx.action_handlers["obg"] is plugin._handle_feedback
