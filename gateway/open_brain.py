from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform as py_platform
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from agent.redact import redact_sensitive_text
from hermes_cli.config import load_config


class OpenBrainConfigError(RuntimeError):
    """Raised when Hermes is missing a usable Openbrain MCP configuration."""


_HERMES_CAPTURE_SCHEMA_VERSION = 1
_HERMES_CAPTURE_SOURCE_APP = "hermes_gateway"
_HERMES_BRIEF_RECORD_TYPES = {"meeting_note", "session_summary"}
_FINANCE_CHECK_DAYS_DEFAULT = 30
_FINANCE_CATEGORY_THRESHOLD_PCT = 0.5
_FINANCE_LARGE_TRANSACTION_DEFAULT = 500.0
_STALE_ACTION_DAYS_DEFAULT = 14
_STALE_CONTACT_RECENT_DAYS = 14
_STALE_CONTACT_ABSENT_DAYS = 30
_DIGEST_ACTION_KEYWORDS = (
    "follow up",
    "follow-up",
    "todo",
    "to do",
    "need to",
    "remember to",
    "check ",
    "send ",
    "schedule ",
    "review ",
    "decide ",
    "next step",
    "action",
)
_DIGEST_DECISION_KEYWORDS = (
    "decided",
    "decision",
    "agreed",
    "implemented",
    "switched",
    "completed",
    "rolled out",
    "enabled",
    "disabled",
    "shipped",
)


def _resolve_open_brain_server() -> tuple[str, dict[str, str]]:
    config = load_config()
    servers = config.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        raise OpenBrainConfigError("No MCP servers are configured.")

    server = servers.get("open_brain")
    if not isinstance(server, dict):
        raise OpenBrainConfigError("The `open_brain` MCP server is not configured.")

    url = str(server.get("url") or "").strip()
    if not url:
        raise OpenBrainConfigError("The `open_brain` MCP server is missing its URL.")

    headers = server.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}

    normalized_headers = {
        str(key): str(value)
        for key, value in headers.items()
        if str(value).strip()
    }
    normalized_headers.setdefault("content-type", "application/json")
    normalized_headers.setdefault("accept", "application/json")
    return url, normalized_headers


def _parse_jsonrpc_http_body(raw_text: str) -> dict[str, Any]:
    stripped = raw_text.strip()
    if not stripped:
        raise RuntimeError("Empty HTTP response body from Openbrain MCP.")

    if stripped.startswith("{"):
        return json.loads(stripped)

    if "data:" in stripped:
        data_lines = []
        for line in stripped.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if not data_lines:
            raise RuntimeError(f"Could not find SSE data lines in response: {raw_text}")
        return json.loads("\n".join(data_lines))

    raise RuntimeError(f"Unsupported Openbrain MCP response format: {raw_text}")


def _extract_text_content(response_body: dict[str, Any]) -> str:
    result = response_body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Missing JSON-RPC result payload: {response_body}")

    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise RuntimeError(f"Missing MCP content payload: {result}")

    first = content[0]
    if not isinstance(first, dict) or not isinstance(first.get("text"), str):
        raise RuntimeError(f"Missing MCP text content: {first}")

    return first["text"]


async def call_open_brain_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Call a hosted Openbrain MCP tool over HTTP and parse its JSON text result."""
    url, headers = _resolve_open_brain_server()

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": f"open-brain-{tool_name}",
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            },
        )
        response.raise_for_status()
        body = _parse_jsonrpc_http_body(response.text)

    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Malformed Openbrain MCP response: {body}")

    payload_text = _extract_text_content(body)
    if result.get("isError") is True:
        raise RuntimeError(payload_text)

    try:
        return json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Openbrain MCP returned non-JSON text: {payload_text[:500]!r}") from exc


async def capture_meeting_note(
    note_text: str,
    *,
    source: Any,
) -> dict[str, Any]:
    """Persist a meeting note as an explicit thought capture in Openbrain."""
    source_id = str(getattr(source, "message_id", "") or "").strip() or None
    metadata = _build_hermes_capture_metadata(
        record_type="meeting_note",
        content=note_text,
        source=source,
        source_id=source_id,
        semantic_key=f"hermes:meeting_note:{source_id}" if source_id else None,
    )
    return await call_open_brain_tool(
        "capture_thought",
        {
            "content": note_text,
            "metadata": metadata,
        },
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts)
    return ""


def _normalize_line(text: str, *, limit: int = 280) -> str:
    clean = " ".join(redact_sensitive_text(text).split())
    clean = re.sub(r"^/[\w.-]+\s*", "", clean)
    if len(clean) > limit:
        clean = clean[: limit - 3].rstrip() + "..."
    return clean


def _normalize_capture_content(text: str) -> str:
    lowered = redact_sensitive_text(text).lower().strip()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(lowered.split())


def _content_hash(text: str) -> str:
    normalized = _normalize_capture_content(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_machine() -> str:
    for value in (
        os.getenv("HERMES_SOURCE_MACHINE"),
        os.getenv("HOSTNAME"),
        py_platform.node(),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return "unknown"


def _source_platform_name(source: Any) -> str:
    return getattr(getattr(source, "platform", None), "value", None) or "unknown"


def _build_provenance(record_type: str, *, source: Any, source_id: str | None) -> str:
    platform_name = _source_platform_name(source)
    if record_type == "meeting_note":
        if source_id:
            return f"Explicit Hermes /note capture from {platform_name} message {source_id}."
        return f"Explicit Hermes /note capture from {platform_name}."
    session_id = getattr(source, "session_id", None)
    if session_id:
        return f"Hermes session-end summary captured from {platform_name} session {session_id}."
    return f"Hermes session-end summary captured from {platform_name}."


def _build_hermes_capture_metadata(
    *,
    record_type: str,
    content: str,
    source: Any,
    source_id: str | None = None,
    semantic_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "record_type": record_type,
        "source": _source_platform_name(source),
        "source_app": _HERMES_CAPTURE_SOURCE_APP,
        "source_id": source_id,
        "source_chat_id": getattr(source, "chat_id", None),
        "source_user_id": getattr(source, "user_id", None),
        "source_message_id": getattr(source, "message_id", None),
        "source_thread_id": getattr(source, "thread_id", None),
        "source_machine": _source_machine(),
        "content_hash": _content_hash(content),
        "semantic_key": semantic_key,
        "provenance": _build_provenance(record_type, source=source, source_id=source_id),
        "visibility": "normal",
        "schema_version": _HERMES_CAPTURE_SCHEMA_VERSION,
    }
    if extra:
        metadata.update(extra)
    return {key: value for key, value in metadata.items() if value is not None}


def distill_session_transcript(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Create a small deterministic session summary from transcript messages."""
    conversational = []
    for message in messages:
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = _normalize_line(_message_text(message))
        if not text:
            continue
        conversational.append({"role": role, "text": text})

    if len(conversational) < 2:
        return None

    user_points: list[str] = []
    assistant_points: list[str] = []
    seen = set()

    for item in conversational:
        text = item["text"]
        key = (item["role"], text.lower())
        if key in seen:
            continue
        seen.add(key)
        if item["role"] == "user" and len(user_points) < 4:
            user_points.append(text)
        elif item["role"] == "assistant":
            assistant_points.append(text)

    recent_assistant = assistant_points[-3:]
    first_user = next((item["text"] for item in conversational if item["role"] == "user"), "")
    last_user = next((item["text"] for item in reversed(conversational) if item["role"] == "user"), "")

    lines = ["Session summary"]
    if first_user:
        lines.append(f"Started with: {first_user}")
    if last_user and last_user != first_user:
        lines.append(f"Ended with: {last_user}")
    if user_points:
        lines.append("Key user points:")
        lines.extend(f"- {point}" for point in user_points)
    if recent_assistant:
        lines.append("Key outcomes:")
        lines.extend(f"- {point}" for point in recent_assistant)

    return {
        "content": "\n".join(lines).strip(),
        "message_count": len(conversational),
        "user_turns": sum(1 for item in conversational if item["role"] == "user"),
        "assistant_turns": sum(1 for item in conversational if item["role"] == "assistant"),
    }


async def save_session_summary(
    *,
    session_id: str,
    source: Any,
    messages: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any] | None:
    """Distill and persist a session summary to Openbrain."""
    summary = distill_session_transcript(messages)
    if summary is None:
        return None

    summary_source = type("SummarySource", (), {})()
    summary_source.platform = getattr(source, "platform", None)
    summary_source.chat_id = getattr(source, "chat_id", None)
    summary_source.user_id = getattr(source, "user_id", None)
    summary_source.thread_id = getattr(source, "thread_id", None)
    summary_source.session_id = session_id
    metadata = _build_hermes_capture_metadata(
        record_type="session_summary",
        content=summary["content"],
        source=summary_source,
        source_id=session_id,
        semantic_key=f"hermes:session_summary:{session_id}",
        extra={
            "session_id": session_id,
            "session_finalize_reason": reason,
            "message_count": summary["message_count"],
            "user_turns": summary["user_turns"],
            "assistant_turns": summary["assistant_turns"],
        },
    )
    payload = await call_open_brain_tool(
        "capture_thought",
        {
            "content": summary["content"],
            "metadata": metadata,
        },
    )
    return {
        "record_id": payload.get("id"),
        "content": summary["content"],
        "message_count": summary["message_count"],
        "deduplicated": bool(payload.get("deduplicated")),
    }


def _brief_excerpt(text: str, *, limit: int = 220) -> str:
    single_line = " ".join(str(text or "").split())
    if len(single_line) > limit:
        return single_line[: limit - 3].rstrip() + "..."
    return single_line


def _is_hermes_brief_candidate(metadata: dict[str, Any]) -> bool:
    record_type = str(metadata.get("record_type") or "")
    if record_type not in _HERMES_BRIEF_RECORD_TYPES:
        return False
    source_app = str(metadata.get("source_app") or "")
    return source_app == _HERMES_CAPTURE_SOURCE_APP


def _normalize_brief_item(
    *,
    record_id: Any,
    content: str,
    created_at: Any,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": record_id,
        "record_type": metadata.get("record_type") or "thought",
        "created_at": created_at,
        "content": content,
        "excerpt": _brief_excerpt(content),
        "citation": f"ob:{record_id}" if record_id else None,
        "source_id": metadata.get("source_id"),
        "session_id": metadata.get("session_id"),
        "provenance": metadata.get("provenance"),
        "metadata": metadata,
    }


async def fetch_briefing(
    *,
    query: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Read back recent Hermes-originated captures from Openbrain."""
    capped_limit = max(1, min(limit, 10))
    if query and query.strip():
        payload = await call_open_brain_tool(
            "query_brain",
            {
                "query": query.strip(),
                "tables": ["thoughts"],
                "limit": max(capped_limit * 2, 6),
            },
        )
        rows = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return []
        items = []
        for row in rows:
            if not isinstance(row, dict) or row.get("table") != "thoughts":
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not _is_hermes_brief_candidate(metadata):
                continue
            content = str(row.get("content_summary") or "").strip()
            if not content:
                continue
            items.append(
                _normalize_brief_item(
                    record_id=row.get("id"),
                    content=content,
                    created_at=row.get("created_at"),
                    metadata=metadata,
                )
            )
        return items[:capped_limit]

    payload = await call_open_brain_tool("list_thoughts", {"limit": 25})
    if not isinstance(payload, list):
        return []

    items = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not _is_hermes_brief_candidate(metadata):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        items.append(
            _normalize_brief_item(
                record_id=row.get("id"),
                content=content,
                created_at=row.get("created_at"),
                metadata=metadata,
            )
        )
        if len(items) >= capped_limit:
            break
    return items


def _parse_created_at(raw_value: object) -> datetime | None:
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _line_candidates_for_digest(item: dict[str, Any]) -> list[str]:
    content = str(item.get("content") or "").strip()
    if not content:
        return []

    record_type = str(item.get("record_type") or "")
    candidates: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if record_type == "session_summary" and line in {
            "Session summary",
            "Key user points:",
            "Key outcomes:",
        }:
            continue
        if record_type == "session_summary" and line.startswith(("Started with:", "Ended with:")):
            continue
        candidates.append(line)
    if not candidates:
        candidates.append(_brief_excerpt(content, limit=220))
    return candidates


def _digest_bucket(lines: list[dict[str, str]], keywords: tuple[str, ...], *, limit: int) -> list[dict[str, str]]:
    chosen: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in lines:
        lowered = item["text"].lower()
        if not any(keyword in lowered for keyword in keywords):
            continue
        key = lowered
        if key in seen:
            continue
        seen.add(key)
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def build_digest(items: list[dict[str, Any]], *, query: str | None = None, days: int = 7) -> dict[str, Any]:
    """Build a deterministic weekly digest from Hermes-originated captures."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, days))

    recent_items = []
    for item in items:
        created_at = _parse_created_at(item.get("created_at"))
        if created_at is None or created_at < cutoff:
            continue
        normalized = dict(item)
        normalized["created_at_dt"] = created_at
        recent_items.append(normalized)

    recent_items.sort(key=lambda item: item["created_at_dt"], reverse=True)

    digest_lines: list[dict[str, str]] = []
    for item in recent_items:
        citation = str(item.get("citation") or "").strip()
        source_ref = str(item.get("source_id") or item.get("session_id") or "").strip()
        suffix_bits = [bit for bit in (citation, source_ref) if bit]
        suffix = f" [{', '.join(suffix_bits)}]" if suffix_bits else ""
        for line in _line_candidates_for_digest(item):
            digest_lines.append(
                {
                    "text": _brief_excerpt(line, limit=200),
                    "reference": suffix,
                    "record_type": str(item.get("record_type") or "thought"),
                }
            )

    decisions = _digest_bucket(digest_lines, _DIGEST_DECISION_KEYWORDS, limit=4)
    actions = _digest_bucket(digest_lines, _DIGEST_ACTION_KEYWORDS, limit=5)

    highlights: list[dict[str, str]] = []
    seen_highlights: set[str] = set()
    for item in digest_lines:
        key = item["text"].lower()
        if key in seen_highlights:
            continue
        seen_highlights.add(key)
        highlights.append(item)
        if len(highlights) >= 5:
            break

    return {
        "query": query,
        "days": days,
        "total_items": len(recent_items),
        "meeting_notes": sum(1 for item in recent_items if item.get("record_type") == "meeting_note"),
        "session_summaries": sum(1 for item in recent_items if item.get("record_type") == "session_summary"),
        "decisions": decisions,
        "actions": actions,
        "highlights": highlights,
    }


async def fetch_digest(
    *,
    query: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    """Return a deterministic digest over recent Hermes-originated captures."""
    normalized_query = query.strip() if query else None
    if normalized_query:
        payload = await call_open_brain_tool(
            "query_brain",
            {
                "query": normalized_query,
                "tables": ["thoughts"],
                "limit": 12,
            },
        )
        rows = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            return build_digest([], query=normalized_query, days=days)
        items = []
        for row in rows:
            if not isinstance(row, dict) or row.get("table") != "thoughts":
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if not _is_hermes_brief_candidate(metadata):
                continue
            content = str(row.get("content_summary") or "").strip()
            if not content:
                continue
            items.append(
                _normalize_brief_item(
                    record_id=row.get("id"),
                    content=content,
                    created_at=row.get("created_at"),
                    metadata=metadata,
                )
            )
        return build_digest(items, query=normalized_query, days=days)

    payload = await call_open_brain_tool("list_thoughts", {"limit": 100})
    if not isinstance(payload, list):
        return build_digest([], query=None, days=days)

    items = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not _is_hermes_brief_candidate(metadata):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        items.append(
            _normalize_brief_item(
                record_id=row.get("id"),
                content=content,
                created_at=row.get("created_at"),
                metadata=metadata,
            )
        )
    return build_digest(items, query=None, days=days)


def _has_action_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _DIGEST_ACTION_KEYWORDS)


def _extract_people(metadata: dict[str, Any]) -> list[str]:
    people = metadata.get("people")
    if not isinstance(people, list):
        return []
    return [str(p).strip() for p in people if isinstance(p, str) and str(p).strip()]


def build_stale_report(
    items: list[dict[str, Any]],
    *,
    action_days: int = _STALE_ACTION_DAYS_DEFAULT,
) -> dict[str, Any]:
    """Identify stale action items and stale contacts from Hermes captures."""
    now = datetime.now(timezone.utc)
    action_cutoff = now - timedelta(days=max(1, action_days))
    recent_cutoff = now - timedelta(days=_STALE_CONTACT_RECENT_DAYS)
    absent_cutoff = now - timedelta(days=_STALE_CONTACT_ABSENT_DAYS)

    stale_actions: list[dict[str, Any]] = []
    people_recent: set[str] = set()
    people_with_old_mention: dict[str, dict[str, Any]] = {}

    for item in items:
        created_at = _parse_created_at(item.get("created_at"))
        if created_at is None:
            continue

        content = str(item.get("content") or "").strip()
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

        for person in _extract_people(metadata):
            normalized = person.lower()
            if created_at >= recent_cutoff:
                people_recent.add(normalized)
            if created_at < absent_cutoff and normalized not in people_with_old_mention:
                people_with_old_mention[normalized] = {
                    "name": person,
                    "last_seen": created_at.isoformat(),
                    "citation": item.get("citation"),
                    "excerpt": _brief_excerpt(content, limit=160),
                }

        if created_at < action_cutoff and _has_action_keyword(content):
            citation = str(item.get("citation") or "").strip()
            age_days = int((now - created_at).days)
            stale_actions.append(
                {
                    "text": _brief_excerpt(content, limit=200),
                    "citation": citation or None,
                    "age_days": age_days,
                    "created_at": created_at.isoformat(),
                }
            )

    stale_actions.sort(key=lambda a: a["age_days"], reverse=True)

    stale_contacts = [
        info
        for name_lower, info in people_with_old_mention.items()
        if name_lower not in people_recent
    ]
    stale_contacts.sort(key=lambda c: str(c.get("last_seen") or ""), reverse=False)

    return {
        "action_days": action_days,
        "stale_actions": stale_actions[:5],
        "stale_contacts": stale_contacts[:5],
    }


async def fetch_stale_items(
    *,
    action_days: int = _STALE_ACTION_DAYS_DEFAULT,
) -> dict[str, Any]:
    """Return stale action items and stale contacts from Hermes-originated captures."""
    payload = await call_open_brain_tool("list_thoughts", {"limit": 100})
    if not isinstance(payload, list):
        return build_stale_report([], action_days=action_days)

    items = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if str(metadata.get("source_app") or "") != _HERMES_CAPTURE_SOURCE_APP:
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        items.append(
            _normalize_brief_item(
                record_id=row.get("id"),
                content=content,
                created_at=row.get("created_at"),
                metadata=metadata,
            )
        )
    return build_stale_report(items, action_days=action_days)


def _safe_amount(record: dict[str, Any]) -> float:
    raw = record.get("amount")
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _record_category(record: dict[str, Any]) -> str:
    for field in ("category", "description"):
        value = str(record.get(field) or "").strip()
        if value:
            return value[:60]
    return "general"


def _group_by_category(records: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for record in records:
        category = _record_category(record)
        totals[category] = totals.get(category, 0.0) + _safe_amount(record)
    return totals


def build_finance_report(
    current_records: list[dict[str, Any]],
    prior_records: list[dict[str, Any]],
    *,
    days: int = _FINANCE_CHECK_DAYS_DEFAULT,
    category_threshold_pct: float = _FINANCE_CATEGORY_THRESHOLD_PCT,
    large_transaction_threshold: float = _FINANCE_LARGE_TRANSACTION_DEFAULT,
) -> dict[str, Any]:
    """Compare current vs prior period finance records and surface anomalies."""
    current_totals = _group_by_category(current_records)
    prior_totals = _group_by_category(prior_records)

    category_anomalies: list[dict[str, Any]] = []
    all_categories = set(current_totals) | set(prior_totals)
    for category in sorted(all_categories):
        current_amt = current_totals.get(category, 0.0)
        prior_amt = prior_totals.get(category, 0.0)
        if current_amt <= 0:
            continue
        if prior_amt <= 0:
            if current_amt >= large_transaction_threshold:
                category_anomalies.append(
                    {
                        "category": category,
                        "current": round(current_amt, 2),
                        "prior": 0.0,
                        "reason": f"No prior-period spending; {current_amt:.0f} this period",
                    }
                )
        else:
            pct_increase = (current_amt - prior_amt) / prior_amt
            if pct_increase >= category_threshold_pct:
                category_anomalies.append(
                    {
                        "category": category,
                        "current": round(current_amt, 2),
                        "prior": round(prior_amt, 2),
                        "reason": (
                            f"+{pct_increase * 100:.0f}% vs prior period "
                            f"({prior_amt:.0f} → {current_amt:.0f})"
                        ),
                    }
                )

    large_transactions: list[dict[str, Any]] = []
    for record in current_records:
        amount = _safe_amount(record)
        if amount >= large_transaction_threshold:
            large_transactions.append(
                {
                    "id": record.get("id"),
                    "date": str(record.get("date") or "").strip(),
                    "description": str(record.get("description") or "").strip(),
                    "amount": round(amount, 2),
                    "category": _record_category(record),
                }
            )
    large_transactions.sort(key=lambda t: t["amount"], reverse=True)

    current_total = sum(_safe_amount(r) for r in current_records)
    prior_total = sum(_safe_amount(r) for r in prior_records)

    return {
        "days": days,
        "current_total": round(current_total, 2),
        "prior_total": round(prior_total, 2),
        "current_count": len(current_records),
        "prior_count": len(prior_records),
        "category_anomalies": category_anomalies[:5],
        "large_transactions": large_transactions[:5],
        "has_anomalies": bool(category_anomalies or large_transactions),
    }


async def fetch_finance_anomalies(
    *,
    days: int = _FINANCE_CHECK_DAYS_DEFAULT,
    large_transaction_threshold: float = _FINANCE_LARGE_TRANSACTION_DEFAULT,
) -> dict[str, Any]:
    """Return a finance anomaly report comparing current vs prior period expenses."""
    now = datetime.now(timezone.utc)
    current_from = (now - timedelta(days=days)).date().isoformat()
    prior_from = (now - timedelta(days=days * 2)).date().isoformat()
    prior_to = (now - timedelta(days=days + 1)).date().isoformat()
    current_to = now.date().isoformat()

    current_payload, prior_payload = await asyncio.gather(
        call_open_brain_tool(
            "search_finance_records",
            {"type": "expense", "date_from": current_from, "date_to": current_to, "limit": 100},
        ),
        call_open_brain_tool(
            "search_finance_records",
            {"type": "expense", "date_from": prior_from, "date_to": prior_to, "limit": 100},
        ),
    )

    current_records = current_payload if isinstance(current_payload, list) else []
    prior_records = prior_payload if isinstance(prior_payload, list) else []

    return build_finance_report(
        current_records,
        prior_records,
        days=days,
        large_transaction_threshold=large_transaction_threshold,
    )
