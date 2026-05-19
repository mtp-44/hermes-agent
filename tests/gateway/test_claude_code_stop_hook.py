from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _hook_module():
    import importlib.util
    hook_path = Path(__file__).parent.parent.parent / "scripts" / "claude_code_stop_hook.py"
    spec = importlib.util.spec_from_file_location("claude_code_stop_hook", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hook():
    return _hook_module()


def _write_transcript(path: Path, messages: list[dict]) -> None:
    with path.open("w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


# --- _read_transcript ---

def test_read_transcript_extracts_role_and_content(hook, tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"role": "user", "content": "What is the rollout plan?"},
        {"role": "assistant", "content": "We deploy on Thursday with the feature flag on."},
    ])
    messages = hook._read_transcript(str(transcript))
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert "rollout" in messages[0]["content"]
    assert messages[1]["role"] == "assistant"


def test_read_transcript_handles_real_claude_code_format(hook, tmp_path):
    """Real Claude Code JSONL wraps messages under a 'message' key."""
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"type": "queue-operation", "operation": "start", "sessionId": "s1"},
        {"type": "user", "message": {"role": "user", "content": "What is the rollout plan?"}, "uuid": "u1", "sessionId": "s1"},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "We deploy on Thursday."}]}, "uuid": "u2", "sessionId": "s1"},
        {"type": "system", "subtype": "result", "sessionId": "s1"},
    ])
    messages = hook._read_transcript(str(transcript))
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert "rollout" in messages[0]["content"]
    assert messages[1]["role"] == "assistant"
    assert "Thursday" in messages[1]["content"]


def test_read_transcript_normalises_human_to_user(hook, tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"role": "human", "content": "Explain this."},
        {"role": "ai", "content": "Sure."},
    ])
    messages = hook._read_transcript(str(transcript))
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


def test_read_transcript_skips_unknown_roles(hook, tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello."},
    ])
    messages = hook._read_transcript(str(transcript))
    assert len(messages) == 1
    assert messages[0]["role"] == "user"


def test_read_transcript_flattens_list_content(hook, tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write_transcript(transcript, [
        {"role": "user", "content": [{"text": "First part."}, {"text": "Second part."}]},
    ])
    messages = hook._read_transcript(str(transcript))
    assert len(messages) == 1
    assert "First part" in messages[0]["content"]
    assert "Second part" in messages[0]["content"]


def test_read_transcript_returns_empty_for_missing_file(hook):
    messages = hook._read_transcript("/nonexistent/path/session.jsonl")
    assert messages == []


def test_read_transcript_skips_malformed_lines(hook, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('not json\n{"role":"user","content":"Valid"}\n{broken\n')
    messages = hook._read_transcript(str(transcript))
    assert len(messages) == 1
    assert messages[0]["content"] == "Valid"


# --- main skip paths ---

def test_main_skips_when_no_capture_env(hook, monkeypatch):
    monkeypatch.setenv("HERMES_NO_CAPTURE", "1")
    result = hook.main()
    assert result == 0


def test_main_skips_when_payload_missing_fields(hook, monkeypatch):
    monkeypatch.delenv("HERMES_NO_CAPTURE", raising=False)
    with patch.object(hook, "_read_payload", return_value={"cwd": "/some/dir"}):
        result = hook.main()
    assert result == 0


def test_main_skips_when_transcript_empty(hook, monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_NO_CAPTURE", raising=False)
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("")
    with patch.object(hook, "_read_payload", return_value={
        "session_id": "sess-test",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
    }):
        result = hook.main()
    assert result == 0


# --- capture path ---

@pytest.mark.asyncio
async def test_run_capture_calls_save_session_summary(hook, tmp_path):
    with patch("gateway.open_brain.save_session_summary", new_callable=AsyncMock) as mock_save:
        mock_save.return_value = {"record_id": "ob-123", "message_count": 4, "deduplicated": False}
        # Temporarily add hermes root to sys.path
        hermes_root = str(Path(__file__).parent.parent.parent)
        if hermes_root not in sys.path:
            sys.path.insert(0, hermes_root)
        await hook._run_capture("sess-abc", [
            {"role": "user", "content": "Plan the sprint."},
            {"role": "assistant", "content": "Here is the sprint plan."},
        ], "/tmp/project")
    mock_save.assert_awaited_once()
    call_kwargs = mock_save.await_args.kwargs
    assert call_kwargs["session_id"] == "sess-abc"
    assert call_kwargs["reason"] == "stop"
    assert len(call_kwargs["messages"]) == 2
