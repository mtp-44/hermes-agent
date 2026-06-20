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
