#!/usr/bin/env python3
"""
Claude Code Stop hook: capture session summary to Openbrain.

Registered in ~/.claude/settings.json under hooks.Stop.
Receives JSON payload on stdin:
  {session_id, transcript_path, cwd, permission_mode, effort, hook_event_name}

The transcript JSONL at transcript_path has one record per line.
Exits 0 always to avoid blocking Claude Code.
Set HERMES_NO_CAPTURE=1 to skip capture without unregistering the hook.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOOK_LOG = Path.home() / ".hermes" / "logs" / "claude_code_capture.log"
_HERMES_AGENT_ROOT = Path(__file__).parent.parent


def _log(message: str) -> None:
    try:
        _HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with _HOOK_LOG.open("a") as f:
            f.write(f"{ts} {message}\n")
    except Exception:
        pass


def _read_payload() -> dict:
    try:
        raw = sys.stdin.buffer.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception as exc:
        _log(f"ERROR reading stdin payload: {exc}")
        return {}


def _read_transcript(transcript_path: str) -> list[dict]:
    """Read the Claude Code transcript JSONL and return normalised role/content messages."""
    path = Path(transcript_path)
    if not path.exists():
        _log(f"SKIP transcript not found: {transcript_path}")
        return []

    messages: list[dict] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue

                # Real Claude Code JSONL: { "type": "user"|"assistant", "message": { "role": ..., "content": ... } }
                # Fall back to flat { "role": ..., "content": ... } for any other format.
                msg = record.get("message")
                if isinstance(msg, dict):
                    raw_role = msg.get("role") or record.get("type") or ""
                    raw_content = msg.get("content") or ""
                else:
                    raw_role = record.get("role") or record.get("type") or ""
                    raw_content = record.get("content") or record.get("text") or ""

                if isinstance(raw_content, list):
                    # Flatten list content blocks (tool use, text, etc.)
                    parts = []
                    for block in raw_content:
                        if isinstance(block, dict):
                            text = block.get("text") or block.get("content") or ""
                            if isinstance(text, str) and text.strip():
                                parts.append(text.strip())
                        elif isinstance(block, str) and block.strip():
                            parts.append(block.strip())
                    raw_content = "\n".join(parts)

                if not isinstance(raw_content, str) or not raw_content.strip():
                    continue

                role_lower = str(raw_role).lower()
                if role_lower in ("human", "user"):
                    role = "user"
                elif role_lower in ("assistant", "ai", "claude"):
                    role = "assistant"
                else:
                    continue

                messages.append({"role": role, "content": raw_content.strip()})
    except Exception as exc:
        _log(f"ERROR reading transcript {transcript_path}: {exc}")
    return messages


def _make_source(session_id: str, cwd: str) -> object:
    source = type("ClaudeCodeSource", (), {})()
    source.platform = type("Platform", (), {"value": "claude_code"})()
    source.chat_id = cwd or None
    source.user_id = None
    source.message_id = None
    source.thread_id = None
    source.session_id = session_id
    return source


async def _run_capture(session_id: str, messages: list[dict], cwd: str) -> None:
    if str(_HERMES_AGENT_ROOT) not in sys.path:
        sys.path.insert(0, str(_HERMES_AGENT_ROOT))

    try:
        from gateway.open_brain import save_session_summary
    except ImportError as exc:
        _log(f"ERROR importing save_session_summary: {exc}")
        return

    source = _make_source(session_id, cwd)
    try:
        payload = await save_session_summary(
            session_id=session_id,
            source=source,
            messages=messages,
            reason="stop",
        )
    except Exception as exc:
        _log(f"ERROR session={session_id}: {exc}")
        return

    if payload is None:
        _log(f"SKIP session={session_id} transcript too small to distill")
    elif payload.get("deduplicated"):
        _log(
            f"DEDUP session={session_id} record_id={payload.get('record_id')} "
            f"messages={payload.get('message_count', 0)}"
        )
    else:
        _log(
            f"SAVED session={session_id} record_id={payload.get('record_id')} "
            f"messages={payload.get('message_count', 0)}"
        )


def main() -> int:
    if os.environ.get("HERMES_NO_CAPTURE") == "1":
        return 0

    payload = _read_payload()
    if not payload:
        return 0

    session_id = str(payload.get("session_id") or "").strip()
    transcript_path = str(payload.get("transcript_path") or "").strip()
    cwd = str(payload.get("cwd") or "").strip()

    if not session_id or not transcript_path:
        _log("SKIP missing session_id or transcript_path")
        return 0

    messages = _read_transcript(transcript_path)
    if not messages:
        _log(f"SKIP session={session_id} no messages in transcript")
        return 0

    try:
        asyncio.run(_run_capture(session_id, messages, cwd))
    except Exception as exc:
        _log(f"ERROR asyncio.run session={session_id}: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
