"""Tests for Open Brain feedback buttons on the Telegram adapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from gateway.open_brain_feedback import (
    capture_analyze_brain_feedback_candidate,
    capture_query_brain_feedback_candidate,
    clear_feedback_candidate,
    pop_feedback_candidate,
)
from gateway.platforms.telegram import TelegramAdapter


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def test_capture_and_pop_feedback_candidate():
    session_id = "agent:main:telegram:dm:42"
    clear_feedback_candidate(session_id)

    capture_query_brain_feedback_candidate(
        session_id=session_id,
        tool_call_id="tool-1",
        args={"query": "When did I last mention the boiler?"},
        result='{"result":"{\\"query\\":\\"When did I last mention the boiler?\\",\\"verdict\\":\\"answer\\",\\"results\\":[{\\"id\\":\\"row-1\\",\\"table\\":\\"thoughts\\",\\"score\\":0.91}]}"}',
    )

    candidate = pop_feedback_candidate(session_id)
    assert candidate is not None
    assert candidate["query_text"] == "When did I last mention the boiler?"
    assert candidate["result_kind"] == "thoughts"
    assert candidate["result_id"] == "row-1"
    assert candidate["response_verdict"] == "answer"


def test_capture_analyze_brain_candidate_for_analytical_route():
    session_id = "agent:main:telegram:dm:42"
    clear_feedback_candidate(session_id)

    capture_analyze_brain_feedback_candidate(
        session_id=session_id,
        tool_call_id="tool-2",
        args={"question": "How long was my longest ride in July 2023?"},
        result='{"result":"{\\"route\\":\\"analytical\\",\\"answer\\":\\"98.2 km\\",\\"status\\":\\"exact\\"}"}',
    )

    candidate = pop_feedback_candidate(session_id)
    assert candidate is not None
    assert candidate["query_text"] == "How long was my longest ride in July 2023?"
    assert candidate["result_kind"] is None
    assert candidate["result_id"] is None
    assert candidate["response_verdict"] == "analytical"


def test_capture_analyze_brain_skips_recall_and_unsupported_routes():
    session_id = "agent:main:telegram:dm:42"
    clear_feedback_candidate(session_id)

    for route in ("recall", "unsupported", "needs_clarification", "error"):
        capture_analyze_brain_feedback_candidate(
            session_id=session_id,
            tool_call_id="tool-3",
            args={"question": "How many conversations did I have?"},
            result='{"result":"{\\"route\\":\\"' + route + '\\",\\"answer\\":\\"n/a\\"}"}',
        )
        assert pop_feedback_candidate(session_id) is None


@pytest.mark.asyncio
async def test_send_attaches_action_buttons_from_metadata():
    # Post-migration (Phase 5c.3 step 2): the send path renders the generic
    # ``metadata["actions"]`` produced by the Open Brain adapter's outbound
    # decorator, not the old ``open_brain_feedback`` key.
    adapter = _make_adapter()
    mock_msg = MagicMock()
    mock_msg.message_id = 101
    adapter._bot.send_message = AsyncMock(return_value=mock_msg)

    result = await adapter.send(
        "12345",
        "Open Brain says the boiler was mentioned last Tuesday.",
        metadata={
            "actions": [
                {"label": "👍 Good", "action_id": "obg", "token": "tok1"},
                {"label": "👎 Bad", "action_id": "obb", "token": "tok1"},
            ],
        },
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.call_args.kwargs
    # A keyboard was attached from metadata["actions"]. The precise act: wire
    # format is asserted in test_message_actions (the harness stubs the telegram
    # module, so button internals aren't introspectable here).
    assert kwargs["reply_markup"] is not None


# NOTE: query-answer (👍/👎) feedback moved off the bespoke obf: branch onto the
# generic act: message-action seam in Phase 5c.3 step 2. The recording behavior is
# now covered by tests/plugins/test_openbrain_query_brain_format_plugin.py
# (_handle_feedback) and the dispatch/auth by tests/gateway/test_message_actions.py.


@pytest.mark.asyncio
async def test_proactive_callback_records_useful_and_clears_markup():
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = MagicMock(return_value=True)

    query = AsyncMock()
    query.data = "prx:a:00000000-0000-0000-0000-000000000001"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.from_user = MagicMock()
    query.from_user.id = "12345"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    with patch("gateway.platforms.telegram.record_proactive_feedback", new=AsyncMock(return_value={"id": "surface-1"})) as mock_record:
        await adapter._handle_callback_query(update, context)

    mock_record.assert_awaited_once_with(
        surface_id="00000000-0000-0000-0000-000000000001",
        status="acted_on",
    )
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


@pytest.mark.asyncio
async def test_proactive_callback_records_dismissed():
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = MagicMock(return_value=True)

    query = AsyncMock()
    query.data = "prx:d:00000000-0000-0000-0000-000000000002"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.from_user = MagicMock()
    query.from_user.id = "12345"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    with patch("gateway.platforms.telegram.record_proactive_feedback", new=AsyncMock(return_value={"id": "surface-2"})) as mock_record:
        await adapter._handle_callback_query(update, context)

    mock_record.assert_awaited_once_with(
        surface_id="00000000-0000-0000-0000-000000000002",
        status="dismissed",
    )
