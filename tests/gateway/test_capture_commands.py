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

