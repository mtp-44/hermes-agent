from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
        message_id="m1",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._busy_input_mode = "interrupt"
    runner._draining = False
    runner._boundary_capturer = MagicMock()
    runner._boundary_capturer.enabled = True

    session_key = build_session_key(_make_source())
    session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-old",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=_make_source(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    new_session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-new",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        origin=_make_source(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.reset_session.return_value = new_session_entry
    runner.session_store._entries = {session_key: session_entry}
    runner.session_store._generate_session_key.return_value = session_key
    runner.session_store.load_transcript.return_value = [
        {"role": "user", "content": "Need rollout summary"},
        {"role": "assistant", "content": "We implemented the capture controls."},
    ]
    runner.session_store.set_capture_controls.side_effect = (
        lambda _key, *, nosave=None, private=None: _mutate_capture_flags(session_entry, nosave, private)
    )
    runner._agent_cache_lock = None
    runner._agent_cache = {}
    runner._cleanup_agent_resources = MagicMock()
    runner._evict_cached_agent = MagicMock()
    runner._format_session_info = lambda: ""
    runner._clear_session_boundary_security_state = MagicMock()
    runner._invalidate_session_run_generation = MagicMock()
    runner._session_key_for_source = lambda _source: session_key
    return runner


def _mutate_capture_flags(entry: SessionEntry, nosave, private):
    if nosave is not None:
        entry.capture_nosave = bool(nosave)
    if private is not None:
        entry.capture_private = bool(private)
    return entry


@pytest.mark.asyncio
async def test_nosave_turns_on_session_flag():
    runner = _make_runner()

    result = await runner._handle_nosave_command(_make_event("/nosave"))

    assert "disabled" in result.lower()
    assert runner.session_store.get_or_create_session.return_value.capture_nosave is True


@pytest.mark.asyncio
async def test_private_toggle_updates_session_flag():
    runner = _make_runner()

    result = await runner._handle_private_command(_make_event("/private"))

    assert "private mode is on" in result.lower()
    assert runner.session_store.get_or_create_session.return_value.capture_private is True


@pytest.mark.asyncio
async def test_capture_status_reports_blockers():
    runner = _make_runner()
    entry = runner.session_store.get_or_create_session.return_value
    entry.capture_nosave = True
    entry.capture_private = True

    result = await runner._handle_capture_status_command(_make_event("/capture-status"))

    assert "disabled" in result.lower()
    assert "private mode is on" in result.lower()
    assert "/nosave" in result.lower()


@pytest.mark.asyncio
@patch("gateway.open_brain.capture_meeting_note", new_callable=AsyncMock)
async def test_note_command_saves_directly(mock_capture):
    runner = _make_runner()
    mock_capture.return_value = {"id": "note-123"}

    result = await runner._handle_note_command(_make_event("/note discuss rollout risk"))

    assert "note-123" in result
    mock_capture.assert_awaited_once()


@pytest.mark.asyncio
@patch("gateway.open_brain.capture_meeting_note", new_callable=AsyncMock)
async def test_note_command_mentions_private_mode_but_still_saves(mock_capture):
    runner = _make_runner()
    runner.session_store.get_or_create_session.return_value.capture_private = True
    mock_capture.return_value = {"id": "note-123"}

    result = await runner._handle_note_command(_make_event("/note explicit private capture"))

    assert "saved" in result.lower()
    assert "private mode stays on" in result.lower()


@pytest.mark.asyncio
@patch("gateway.open_brain.capture_meeting_note", new_callable=AsyncMock)
async def test_note_command_reports_deduplicated_replay(mock_capture):
    runner = _make_runner()
    mock_capture.return_value = {"id": "note-123", "deduplicated": True}

    result = await runner._handle_note_command(_make_event("/note discuss rollout risk"))

    assert "already saved" in result.lower()
    assert "note-123" in result


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_briefing", new_callable=AsyncMock)
async def test_brief_command_lists_recent_captures(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = [
        {
            "id": "sum-123",
            "record_type": "session_summary",
            "created_at": "2026-05-18T10:30:00+00:00",
            "excerpt": "Session summary Started with rollout work.",
            "citation": "ob:sum-123",
            "session_id": "sess-1",
        },
        {
            "id": "note-456",
            "record_type": "meeting_note",
            "created_at": "2026-05-18T10:45:00+00:00",
            "excerpt": "Remember to validate readback formatting.",
            "citation": "ob:note-456",
            "source_id": "m-456",
        },
    ]

    result = await runner._handle_brief_command(_make_event("/brief"))

    assert "brief" in result.lower()
    assert "session summary" in result.lower()
    assert "meeting note" in result.lower()
    assert "ob:sum-123" in result
    assert "ob:note-456" in result
    mock_fetch.assert_awaited_once_with(query=None, limit=3)


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_briefing", new_callable=AsyncMock)
async def test_brief_command_passes_query(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = []

    result = await runner._handle_brief_command(_make_event("/brief rollout summary"))

    assert "matched" in result.lower()
    assert "rollout summary" in result
    mock_fetch.assert_awaited_once_with(query="rollout summary", limit=3)


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_digest", new_callable=AsyncMock)
async def test_digest_command_formats_sections(mock_fetch_digest):
    runner = _make_runner()
    mock_fetch_digest.return_value = {
        "total_items": 3,
        "meeting_notes": 2,
        "session_summaries": 1,
        "decisions": [
            {"text": "Implemented the Claude/Opus route commands.", "reference": " [ob:sum-123, sess-1]"},
        ],
        "actions": [
            {"text": "Remember to validate readback formatting.", "reference": " [ob:note-456, m-456]"},
        ],
        "highlights": [
            {"text": "Session summary Started with rollout work.", "reference": " [ob:sum-123, sess-1]"},
        ],
    }

    result = await runner._handle_digest_command(_make_event("/digest"))

    assert "digest" in result.lower()
    assert "decisions & outcomes" in result.lower()
    assert "open loops" in result.lower()
    assert "highlights" in result.lower()
    mock_fetch_digest.assert_awaited_once_with(query=None, days=7)


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_digest", new_callable=AsyncMock)
async def test_digest_command_reports_query_empty_state(mock_fetch_digest):
    runner = _make_runner()
    mock_fetch_digest.return_value = {
        "total_items": 0,
        "meeting_notes": 0,
        "session_summaries": 0,
        "decisions": [],
        "actions": [],
        "highlights": [],
    }

    result = await runner._handle_digest_command(_make_event("/digest rollout"))

    assert "no hermes captures matched" in result.lower()
    assert "rollout" in result
    mock_fetch_digest.assert_awaited_once_with(query="rollout", days=7)


@pytest.mark.asyncio
@patch("gateway.jira_mcp.fetch_current_sprint_issues", new_callable=AsyncMock)
async def test_jira_command_lists_current_sprint_issues(mock_fetch_issues):
    runner = _make_runner()
    mock_fetch_issues.return_value = [
        {
            "key": "PROJ-101",
            "summary": "Ship Jira MCP readback",
            "status": "In Progress",
            "priority": "High",
            "assignee": "Mark",
        },
        {
            "key": "PROJ-102",
            "summary": "Review follow-up tests",
            "status": "To Do",
            "priority": "",
            "assignee": "",
        },
    ]

    result = await runner._handle_jira_command(_make_event("/jira"))

    assert "jira" in result.lower()
    assert "source: jira" in result.lower()
    assert "PROJ-101" in result
    assert "Ship Jira MCP readback" in result
    assert "/fast" in result
    assert "/claude" in result
    mock_fetch_issues.assert_awaited_once_with(query=None, limit=8)


@pytest.mark.asyncio
@patch("gateway.jira_mcp.fetch_current_sprint_issues", new_callable=AsyncMock)
async def test_jira_command_passes_filter(mock_fetch_issues):
    runner = _make_runner()
    mock_fetch_issues.return_value = []

    result = await runner._handle_jira_command(_make_event("/jira auth"))

    assert "matched" in result.lower()
    assert "auth" in result
    mock_fetch_issues.assert_awaited_once_with(query="auth", limit=8)


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_reset_captures_session_summary_when_eligible(mock_invoke_hook):
    runner = _make_runner()

    await runner._handle_reset_command(_make_event("/reset"))

    runner._boundary_capturer.capture.assert_called_once()
    kwargs = runner._boundary_capturer.capture.call_args.kwargs
    assert kwargs["session_id"] == "sess-old"
    assert kwargs["boundary_reason"] == "reset"
    assert kwargs["eligible"] is True
    assert kwargs["messages"] == runner.session_store.load_transcript.return_value


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_reset_skips_session_summary_when_nosave_enabled(mock_invoke_hook):
    runner = _make_runner()
    runner.session_store.get_or_create_session.return_value.capture_nosave = True
    runner.session_store._entries[next(iter(runner.session_store._entries))].capture_nosave = True

    await runner._handle_reset_command(_make_event("/reset"))

    runner._boundary_capturer.capture.assert_not_called()


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_reset_notifies_provider_ineligible_when_private(mock_invoke_hook):
    # /private still notifies the provider (so it can track the boundary) but
    # with eligible=False, so a flag-honoring provider performs no durable write.
    runner = _make_runner()
    runner.session_store.get_or_create_session.return_value.capture_private = True
    runner.session_store._entries[next(iter(runner.session_store._entries))].capture_private = True

    await runner._handle_reset_command(_make_event("/reset"))

    runner._boundary_capturer.capture.assert_called_once()
    assert runner._boundary_capturer.capture.call_args.kwargs["eligible"] is False


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_reset_skips_session_summary_without_transcript(mock_invoke_hook):
    runner = _make_runner()
    runner.session_store.load_transcript.return_value = []

    await runner._handle_reset_command(_make_event("/reset"))

    runner._boundary_capturer.capture.assert_not_called()


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_stale_items", new_callable=AsyncMock)
async def test_stale_command_surfaces_open_loops(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = {
        "action_days": 14,
        "stale_actions": [
            {
                "text": "Need to follow up on the rollout plan.",
                "citation": "ob:t-1",
                "age_days": 20,
            }
        ],
        "stale_contacts": [],
    }

    result = await runner._handle_stale_command(_make_event("/stale"))

    assert "Stale" in result
    assert "follow up" in result.lower()
    assert "ob:t-1" in result
    assert "20d ago" in result


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_stale_items", new_callable=AsyncMock)
async def test_stale_command_surfaces_absent_contacts(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = {
        "action_days": 14,
        "stale_actions": [],
        "stale_contacts": [
            {
                "name": "Alex",
                "excerpt": "Met with Alex about the project.",
                "citation": "ob:t-old",
            }
        ],
    }

    result = await runner._handle_stale_command(_make_event("/stale"))

    assert "Alex" in result
    assert "ob:t-old" in result


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_finance_anomalies", new_callable=AsyncMock)
async def test_finance_check_reports_clean(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = {
        "days": 30,
        "current_total": 1200.0,
        "prior_total": 1100.0,
        "has_anomalies": False,
        "category_anomalies": [],
        "large_transactions": [],
    }

    result = await runner._handle_finance_check_command(_make_event("/finance-check"))

    assert "No finance anomalies" in result
    assert "1200" in result


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_finance_anomalies", new_callable=AsyncMock)
async def test_finance_check_surfaces_category_spike(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = {
        "days": 30,
        "current_total": 2000.0,
        "prior_total": 1000.0,
        "has_anomalies": True,
        "category_anomalies": [
            {
                "category": "dining",
                "current": 900.0,
                "prior": 300.0,
                "reason": "+200% vs prior period (300 → 900)",
            }
        ],
        "large_transactions": [],
    }

    result = await runner._handle_finance_check_command(_make_event("/finance-check"))

    assert "Finance check" in result
    assert "dining" in result
    assert "+200%" in result


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_finance_anomalies", new_callable=AsyncMock)
async def test_finance_check_surfaces_large_transaction(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = {
        "days": 30,
        "current_total": 1500.0,
        "prior_total": 300.0,
        "has_anomalies": True,
        "category_anomalies": [],
        "large_transactions": [
            {
                "date": "2026-05-10",
                "description": "Annual software subscription",
                "amount": 1200.0,
                "category": "software",
            }
        ],
    }

    result = await runner._handle_finance_check_command(_make_event("/finance-check"))

    assert "Large transactions" in result
    assert "Annual software subscription" in result
    assert "1200" in result


@pytest.mark.asyncio
@patch("gateway.open_brain.fetch_stale_items", new_callable=AsyncMock)
async def test_stale_command_reports_nothing_stale(mock_fetch):
    runner = _make_runner()
    mock_fetch.return_value = {
        "action_days": 14,
        "stale_actions": [],
        "stale_contacts": [],
    }

    result = await runner._handle_stale_command(_make_event("/stale"))

    assert "Nothing stale" in result or "nothing stale" in result.lower()
