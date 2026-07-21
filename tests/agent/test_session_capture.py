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
    # Provenance refs are built for every boundary regardless of eligibility.
    # A bare "hello"/"hi there" exchange carries no durable user-owned signal,
    # so under the signal-gate it is (correctly) ineligible and emits nothing.
    assert context["eligible"] is False
    assert context["capture_skip_reason"] == "no_durable_signal"
    assert context["capture_records"] == []


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


# --- Signal-gate behavior (2026-07-21) ---

def test_health_check_session_is_skipped():
    context = build_capture_context(
        session_id="sess-ping",
        boundary_reason="gateway_shutdown",
        platform="telegram",
        messages=[
            {"role": "user", "content": "back up?"},
            {"role": "assistant", "content": "Yes, I'm fully back up and running."},
        ],
    )
    assert context["eligible"] is False
    assert context["capture_skip_reason"] == "health_check"
    assert context["capture_records"] == []


def test_smoke_test_session_is_skipped():
    context = build_capture_context(
        session_id="sess-smoke",
        boundary_reason="cli_close",
        platform="cli",
        messages=[
            {"role": "user", "content": "Reply with exactly: HERMES OK"},
            {"role": "assistant", "content": "HERMES OK"},
        ],
    )
    assert context["eligible"] is False
    assert context["capture_skip_reason"] == "smoke_test"


def test_pure_query_session_is_skipped():
    context = build_capture_context(
        session_id="sess-query",
        boundary_reason="ws_orphan_reap",
        platform="telegram",
        messages=[
            {"role": "user", "content": "how many people live in Munich?"},
            {"role": "assistant", "content": "About 1.5 million people live in Munich."},
        ],
    )
    assert context["eligible"] is False
    assert context["capture_skip_reason"] == "no_durable_signal"
    assert context["capture_records"] == []


def test_assistant_commitment_alone_does_not_make_session_eligible():
    # Assistant "I'll check…" is in-session task chatter, not a durable user
    # commitment. The old code tagged it owner="user" at 0.88 canonical.
    context = build_capture_context(
        session_id="sess-asstchatter",
        boundary_reason="gateway_shutdown",
        platform="cli",
        messages=[
            {"role": "user", "content": "check the current working folder"},
            {"role": "assistant", "content": "I'll check your backup system now."},
        ],
    )
    assert context["eligible"] is False
    assert context["capture_skip_reason"] == "no_canonical_signal"


def test_user_commitment_alone_is_not_eligible():
    # A user asking the assistant to do something now ("I need to X") is not a
    # six-month-durable commitment; action items must not gate on their own.
    context = build_capture_context(
        session_id="sess-commit",
        boundary_reason="new_session",
        platform="cli",
        messages=[
            {"role": "user", "content": "I need to migrate the gateway to the new endpoint."},
            {"role": "assistant", "content": "I'll help you plan that."},
        ],
    )
    assert context["eligible"] is False


def test_action_item_owner_reflects_speaker_and_stays_pending():
    # Owner must be the real speaker (the old code faked owner="user" for any
    # "I'll" match); action items ride along as pending context only.
    context = build_capture_context(
        session_id="sess-owner",
        boundary_reason="new_session",
        platform="cli",
        messages=[
            {
                "role": "user",
                "content": "I prefer Signal. I need to migrate the gateway to the new endpoint.",
            },
            {"role": "assistant", "content": "I'll help you plan that migration."},
        ],
    )
    assert context["eligible"] is True  # made eligible by the durable "I prefer" fact
    action_items = [r for r in context["capture_records"] if r["type"] == "action_item"]
    user_items = [r for r in action_items if r["owner"] == "user"]
    hermes_items = [r for r in action_items if r["owner"] == "hermes"]
    assert user_items and hermes_items, "both speakers' commitments should be attributed correctly"
    assert all(r["routing"] == "pending" for r in action_items)
    # The assistant "I'll help" must NOT be attributed to the user.
    assert all(
        not (r["owner"] == "user" and "help you plan" in r["title"].lower())
        for r in action_items
    )


def test_injected_compaction_block_does_not_create_a_decision():
    context = build_capture_context(
        session_id="sess-compaction",
        boundary_reason="compression",
        platform="cli",
        messages=[
            {
                "role": "user",
                "content": "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns confirmed the plan and accepted the approach.",
            },
            {"role": "assistant", "content": "Continuing from the summary."},
        ],
    )
    assert context["eligible"] is False
    assert not any(r["type"] == "decision_record" for r in context["capture_records"])


def test_no_auto_record_exceeds_deliberate_capture_confidence():
    context = build_capture_context(
        session_id="sess-conf",
        boundary_reason="new_session",
        platform="cli",
        messages=[
            {"role": "user", "content": "I prefer uv. I need to ship the fix. Let's use the hosted endpoint."},
            {"role": "assistant", "content": "Understood."},
        ],
    )
    assert context["eligible"] is True
    assert context["capture_records"], "session with durable signal should emit records"
    for record in context["capture_records"]:
        assert record["confidence"] <= 0.85, (
            f"{record['type']} confidence {record['confidence']} outranks deliberate captures"
        )


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
