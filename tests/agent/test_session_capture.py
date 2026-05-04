import json
import tempfile
from pathlib import Path

from agent.session_capture import build_capture_context, persist_capture_artifact


def test_build_capture_context_includes_provenance_refs():
    context = build_capture_context(
        session_id="sess-123",
        boundary_reason="compression",
        platform="cli",
        parent_session_id="sess-122",
        user_id="user-1",
        chat_id="chat-1",
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "tool", "tool_name": "memory_write", "tool_call_id": "call-1"},
        ],
    )

    assert context["session_id"] == "sess-123"
    assert context["boundary_reason"] == "compression"
    assert context["platform"] == "cli"
    assert context["parent_session_id"] == "sess-122"
    assert context["user_id"] == "user-1"
    assert context["chat_id"] == "chat-1"
    assert context["source_layer"] == "session_memory"
    assert context["captured_by"] == "hermes_auto_capture"
    assert context["message_count"] == 3
    assert context["message_refs"] == [
        {"index": 0, "role": "user"},
        {"index": 1, "role": "assistant"},
        {"index": 2, "role": "tool", "tool_name": "memory_write", "tool_call_id": "call-1"},
    ]
    assert context["eligible"] is True
    assert context["capture_record_counts"]["session_summary"] == 1
    assert context["capture_records"][0]["type"] == "session_summary"


def test_build_capture_context_extracts_structured_records():
    context = build_capture_context(
        session_id="sess-200",
        boundary_reason="new_session",
        platform="cli",
        user_id="user-1",
        messages=[
            {
                "role": "user",
                "content": (
                    "I prefer uv for Python projects. "
                    "I need to add an Open Brain provider next. "
                    "Let's use the hosted MCP endpoint."
                ),
            },
            {
                "role": "assistant",
                "content": "We should use the hosted MCP endpoint first, then wire retries after that.",
            },
        ],
    )

    types = [record["type"] for record in context["capture_records"]]
    assert "session_summary" in types
    assert "action_item" in types
    assert "decision_record" in types
    assert "durable_fact" in types
    assert context["capture_record_counts"]["action_item"] >= 1
    assert any(record.get("routing") == "pending" for record in context["capture_records"])


def test_persist_capture_artifact_writes_boundary_file():
    context = build_capture_context(
        session_id="sess-300",
        boundary_reason="cli_close",
        platform="cli",
        messages=[{"role": "user", "content": "I prefer concise summaries."}],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        path = persist_capture_artifact(tmpdir, context)
        assert path is not None
        written = Path(path)
        assert written.exists()
        payload = json.loads(written.read_text(encoding="utf-8"))
        assert payload["boundary_id"] == context["boundary_id"]
        assert payload["session_id"] == "sess-300"
