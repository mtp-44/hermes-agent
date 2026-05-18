from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import Platform


def _make_source(*, message_id: str = "m-123") -> SimpleNamespace:
    return SimpleNamespace(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        user_id="user-1",
        message_id=message_id,
        thread_id="thread-1",
    )


@pytest.mark.asyncio
@patch("gateway.open_brain.call_open_brain_tool", new_callable=AsyncMock)
async def test_capture_meeting_note_adds_provenance_and_dedup_metadata(mock_call):
    from gateway.open_brain import capture_meeting_note

    mock_call.return_value = {"id": "note-123"}

    await capture_meeting_note("Remember the rollout checklist.", source=_make_source())

    assert mock_call.await_count == 1
    _, payload = mock_call.await_args.args
    metadata = payload["metadata"]
    assert metadata["record_type"] == "meeting_note"
    assert metadata["source"] == "telegram"
    assert metadata["source_app"] == "hermes_gateway"
    assert metadata["source_id"] == "m-123"
    assert metadata["semantic_key"] == "hermes:meeting_note:m-123"
    assert metadata["schema_version"] == 1
    assert metadata["content_hash"]
    assert "Explicit Hermes /note capture" in metadata["provenance"]


@pytest.mark.asyncio
@patch("gateway.open_brain.call_open_brain_tool", new_callable=AsyncMock)
async def test_save_session_summary_adds_provenance_and_dedup_metadata(mock_call):
    from gateway.open_brain import save_session_summary

    mock_call.return_value = {"id": "sum-123"}
    messages = [
        {"role": "user", "content": "Need a rollout summary"},
        {"role": "assistant", "content": "We added the capture controls."},
    ]

    await save_session_summary(
        session_id="sess-123",
        source=_make_source(message_id="m-999"),
        messages=messages,
        reason="reset",
    )

    assert mock_call.await_count == 1
    _, payload = mock_call.await_args.args
    metadata = payload["metadata"]
    assert metadata["record_type"] == "session_summary"
    assert metadata["source"] == "telegram"
    assert metadata["source_app"] == "hermes_gateway"
    assert metadata["source_id"] == "sess-123"
    assert metadata["session_id"] == "sess-123"
    assert metadata["semantic_key"] == "hermes:session_summary:sess-123"
    assert metadata["session_finalize_reason"] == "reset"
    assert metadata["schema_version"] == 1
    assert metadata["content_hash"]
    assert "session-end summary" in metadata["provenance"]


@pytest.mark.asyncio
@patch("gateway.open_brain.call_open_brain_tool", new_callable=AsyncMock)
async def test_save_session_summary_surfaces_deduplicated_flag(mock_call):
    from gateway.open_brain import save_session_summary

    mock_call.return_value = {"id": "sum-456", "deduplicated": True}
    messages = [
        {"role": "user", "content": "Checking dedup behavior"},
        {"role": "assistant", "content": "Already captured this session."},
    ]

    result = await save_session_summary(
        session_id="sess-456",
        source=_make_source(),
        messages=messages,
        reason="reset",
    )

    assert result is not None
    assert result["record_id"] == "sum-456"
    assert result["deduplicated"] is True


@pytest.mark.asyncio
@patch("gateway.open_brain.call_open_brain_tool", new_callable=AsyncMock)
async def test_save_session_summary_deduplicated_false_for_new_writes(mock_call):
    from gateway.open_brain import save_session_summary

    mock_call.return_value = {"id": "sum-789"}
    messages = [
        {"role": "user", "content": "Fresh session content"},
        {"role": "assistant", "content": "New write path."},
    ]

    result = await save_session_summary(
        session_id="sess-789",
        source=_make_source(),
        messages=messages,
        reason="idle",
    )

    assert result is not None
    assert result["record_id"] == "sum-789"
    assert result["deduplicated"] is False


def test_build_stale_report_flags_old_action_items():
    from gateway.open_brain import build_stale_report

    old_date = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    recent_date = datetime.now(timezone.utc).isoformat()

    items = [
        {
            "id": "t-1",
            "record_type": "session_summary",
            "created_at": old_date,
            "content": "Need to follow up on the rollout plan with the team.",
            "citation": "ob:t-1",
            "metadata": {"source_app": "hermes_gateway"},
        },
        {
            "id": "t-2",
            "record_type": "meeting_note",
            "created_at": recent_date,
            "content": "Discussed the quarterly roadmap.",
            "citation": "ob:t-2",
            "metadata": {"source_app": "hermes_gateway"},
        },
    ]
    report = build_stale_report(items, action_days=14)

    assert len(report["stale_actions"]) == 1
    assert "follow up" in report["stale_actions"][0]["text"].lower()
    assert report["stale_actions"][0]["age_days"] >= 20
    assert report["stale_actions"][0]["citation"] == "ob:t-1"
    assert report["stale_contacts"] == []


def test_build_stale_report_surfaces_absent_contacts():
    from gateway.open_brain import build_stale_report

    old_date = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    recent_date = datetime.now(timezone.utc).isoformat()

    items = [
        {
            "id": "t-old",
            "record_type": "meeting_note",
            "created_at": old_date,
            "content": "Met with Alex about the project timeline.",
            "citation": "ob:t-old",
            "metadata": {"source_app": "hermes_gateway", "people": ["Alex", "Jordan"]},
        },
        {
            "id": "t-new",
            "record_type": "session_summary",
            "created_at": recent_date,
            "content": "Checked in with Jordan on delivery.",
            "citation": "ob:t-new",
            "metadata": {"source_app": "hermes_gateway", "people": ["Jordan"]},
        },
    ]
    report = build_stale_report(items, action_days=14)

    contact_names = [c["name"] for c in report["stale_contacts"]]
    assert "Alex" in contact_names
    assert "Jordan" not in contact_names


def test_build_stale_report_caps_at_five():
    from gateway.open_brain import build_stale_report

    old_date = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    items = [
        {
            "id": f"t-{i}",
            "record_type": "meeting_note",
            "created_at": old_date,
            "content": f"Need to follow up on item {i} with the stakeholder.",
            "citation": f"ob:t-{i}",
            "metadata": {"source_app": "hermes_gateway"},
        }
        for i in range(10)
    ]
    report = build_stale_report(items, action_days=14)
    assert len(report["stale_actions"]) <= 5


def test_build_digest_extracts_decisions_actions_and_highlights():
    from gateway.open_brain import build_digest

    digest = build_digest(
        [
            {
                "id": "sum-1",
                "record_type": "session_summary",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": "\n".join(
                    [
                        "Session summary",
                        "Started with: Route rollout work",
                        "Key user points:",
                        "- Need to validate readback formatting.",
                        "Key outcomes:",
                        "- Implemented the Claude/Opus route commands.",
                    ]
                ),
                "citation": "ob:sum-1",
                "session_id": "sess-1",
            },
            {
                "id": "note-1",
                "record_type": "meeting_note",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "content": "Remember to follow up on scheduled push routines.",
                "citation": "ob:note-1",
                "source_id": "m-1",
            },
        ],
        days=7,
    )

    assert digest["total_items"] == 2
    assert digest["session_summaries"] == 1
    assert digest["meeting_notes"] == 1
    assert any("implemented the claude/opus route commands" in item["text"].lower() for item in digest["decisions"])
    assert any("follow up" in item["text"].lower() or "validate readback" in item["text"].lower() for item in digest["actions"])
    assert digest["highlights"]


def test_build_finance_report_flags_category_spike():
    from gateway.open_brain import build_finance_report

    current = [
        {"amount": 900, "category": "dining", "date": "2026-05-01"},
        {"amount": 100, "category": "transport", "date": "2026-05-02"},
    ]
    prior = [
        {"amount": 300, "category": "dining", "date": "2026-04-01"},
        {"amount": 120, "category": "transport", "date": "2026-04-02"},
    ]
    report = build_finance_report(current, prior, days=30)

    assert report["has_anomalies"] is True
    anomaly_categories = [a["category"] for a in report["category_anomalies"]]
    assert "dining" in anomaly_categories
    assert "transport" not in anomaly_categories
    dining = next(a for a in report["category_anomalies"] if a["category"] == "dining")
    assert dining["current"] == 900.0
    assert dining["prior"] == 300.0
    assert "+200%" in dining["reason"]


def test_build_finance_report_flags_large_transaction():
    from gateway.open_brain import build_finance_report

    current = [{"amount": 1200, "description": "Annual software subscription", "date": "2026-05-10"}]
    prior = [{"amount": 1200, "description": "Annual software subscription", "date": "2026-04-10"}]
    report = build_finance_report(current, prior, days=30, large_transaction_threshold=500.0)

    assert report["has_anomalies"] is True
    assert len(report["large_transactions"]) == 1
    assert report["large_transactions"][0]["amount"] == 1200.0


def test_build_finance_report_clean_returns_no_anomalies():
    from gateway.open_brain import build_finance_report

    current = [{"amount": 100, "category": "groceries"}]
    prior = [{"amount": 95, "category": "groceries"}]
    report = build_finance_report(current, prior, days=30, large_transaction_threshold=500.0)

    assert report["has_anomalies"] is False
    assert report["category_anomalies"] == []
    assert report["large_transactions"] == []


def test_build_finance_report_caps_anomalies_at_five():
    from gateway.open_brain import build_finance_report

    current = [{"amount": 1000, "category": f"cat{i}"} for i in range(10)]
    prior = []
    report = build_finance_report(
        current, prior, days=30, large_transaction_threshold=500.0
    )

    assert len(report["category_anomalies"]) <= 5
    assert len(report["large_transactions"]) <= 5
