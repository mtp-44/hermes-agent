import json
import tempfile
from pathlib import Path

import pytest

from agent.session_capture import (
    _shorten,
    build_capture_context,
    build_message_refs,
    persist_capture_artifact,
)


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


# --- Edge cases ---

def test_empty_messages_produces_ineligible_context():
    context = build_capture_context(
        session_id="sess-empty",
        boundary_reason="new_session",
        platform="cli",
        messages=[],
    )
    assert context["eligible"] is False
    assert context["capture_records"] == []


def test_none_messages_produces_ineligible_context():
    context = build_capture_context(
        session_id="sess-none",
        boundary_reason="new_session",
        platform="cli",
        messages=None,
    )
    assert context["eligible"] is False
    assert context["capture_records"] == []


def test_trivial_only_messages_produces_ineligible_context():
    context = build_capture_context(
        session_id="sess-trivial",
        boundary_reason="new_session",
        platform="telegram",
        messages=[
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "sure"},
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "ok"},
        ],
    )
    assert context["eligible"] is False


def test_assistant_only_messages_produces_ineligible_context():
    context = build_capture_context(
        session_id="sess-nouser",
        boundary_reason="session_expiry",
        platform="cli",
        messages=[
            {"role": "assistant", "content": "I can help you with that."},
            {"role": "assistant", "content": "Let me know if you need anything else."},
        ],
    )
    assert context["eligible"] is False


def test_message_with_none_content_is_skipped():
    context = build_capture_context(
        session_id="sess-nonecontent",
        boundary_reason="new_session",
        platform="cli",
        messages=[
            {"role": "user", "content": None},
            {"role": "user", "content": "I prefer dark mode."},
            {"role": "assistant", "content": "Got it."},
        ],
    )
    assert context["eligible"] is True
    types = [r["type"] for r in context["capture_records"]]
    assert "durable_fact" in types


def test_message_with_list_content_is_normalized():
    context = build_capture_context(
        session_id="sess-listcontent",
        boundary_reason="new_session",
        platform="cli",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "I prefer uv for Python."},
                    {"type": "image_url", "url": "http://example.com/img.png"},
                ],
            },
            {"role": "assistant", "content": "Noted."},
        ],
    )
    assert context["eligible"] is True
    types = [r["type"] for r in context["capture_records"]]
    assert "durable_fact" in types


def test_very_long_message_is_truncated_in_records():
    long_text = "I prefer " + ("x" * 500)
    context = build_capture_context(
        session_id="sess-long",
        boundary_reason="new_session",
        platform="cli",
        messages=[
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "Understood."},
        ],
    )
    assert context["eligible"] is True
    for record in context["capture_records"]:
        for field in ("summary_text", "title", "details", "decision", "value"):
            value = record.get(field, "")
            if value:
                assert len(value) <= 230, f"{field} in {record['type']} exceeds truncation limit"


def test_boundary_id_is_stable_for_same_inputs():
    kwargs = dict(
        session_id="sess-stable",
        boundary_reason="compression",
        platform="tui",
        messages=[{"role": "user", "content": "I prefer short answers."}],
        captured_at="2026-05-04T12:00:00+00:00",
    )
    first = build_capture_context(**kwargs)
    second = build_capture_context(**kwargs)
    assert first["boundary_id"] == second["boundary_id"]


def test_boundary_id_differs_for_different_sessions():
    base = dict(boundary_reason="new_session", platform="cli", captured_at="2026-05-04T12:00:00+00:00")
    a = build_capture_context(session_id="sess-A", messages=[{"role": "user", "content": "I prefer X."}], **base)
    b = build_capture_context(session_id="sess-B", messages=[{"role": "user", "content": "I prefer X."}], **base)
    assert a["boundary_id"] != b["boundary_id"]


def test_build_message_refs_skips_roleless_messages():
    refs = build_message_refs([
        {"role": "user", "content": "hello"},
        {"content": "no role here"},
        {"role": "assistant", "content": "hi"},
    ])
    assert len(refs) == 2
    assert refs[0]["role"] == "user"
    assert refs[1]["role"] == "assistant"


def test_shorten_does_not_break_mid_word():
    text = "This is a sentence with many words in it and goes beyond the limit set"
    result = _shorten(text, limit=30)
    assert len(result) <= 33  # limit + "..."
    assert not result[:-3].endswith(" ")  # no trailing space before ellipsis


def test_persist_artifact_returns_none_without_boundary_id():
    result = persist_capture_artifact("/tmp", {})
    assert result is None


@pytest.mark.parametrize("boundary_reason", [
    "new_session", "session_reset", "compression", "session_expiry", "cli_close", "tui_close",
])
def test_all_boundary_reasons_produce_valid_context(boundary_reason):
    context = build_capture_context(
        session_id="sess-br",
        boundary_reason=boundary_reason,
        platform="cli",
        messages=[
            {"role": "user", "content": "I prefer concise answers."},
            {"role": "assistant", "content": "Noted, I will keep it brief."},
        ],
    )
    assert context["boundary_reason"] == boundary_reason
    assert "eligible" in context
    assert "boundary_id" in context
