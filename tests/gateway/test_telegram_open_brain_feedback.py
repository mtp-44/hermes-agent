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
async def test_send_attaches_feedback_buttons_from_metadata():
    adapter = _make_adapter()
    mock_msg = MagicMock()
    mock_msg.message_id = 101
    adapter._bot.send_message = AsyncMock(return_value=mock_msg)

    result = await adapter.send(
        "12345",
        "Open Brain says the boiler was mentioned last Tuesday.",
        metadata={
            "open_brain_feedback": {
                "query_id": "q-1",
                "query_text": "When did I last mention the boiler?",
                "source": "hermes",
            },
        },
    )

    assert result.success is True
    kwargs = adapter._bot.send_message.call_args.kwargs
    assert kwargs["reply_markup"] is not None
    assert len(adapter._feedback_entries) == 1


@pytest.mark.asyncio
async def test_feedback_callback_records_vote_and_clears_markup():
    adapter = _make_adapter()
    token = adapter._register_feedback_context({
        "query_id": "q-2",
        "query_text": "How far did I ride last month?",
        "result_kind": "life_items",
        "result_id": "ride-1",
        "response_verdict": "answer",
        "source": "hermes",
    })

    query = AsyncMock()
    query.data = f"obf:g:{token}"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.from_user = MagicMock()
    query.from_user.id = "12345"
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    with patch("gateway.platforms.telegram.record_query_feedback", new=AsyncMock(return_value={"id": "fb-1"})) as mock_record:
        await adapter._handle_callback_query(update, context)

    mock_record.assert_awaited_once()
    kwargs = mock_record.await_args.kwargs
    assert kwargs["verdict"] == "good"
    assert kwargs["query_id"] == "q-2"
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
