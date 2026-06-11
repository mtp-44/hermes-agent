from __future__ import annotations

import json
import threading
import time
from typing import Any

_LOCK = threading.Lock()
_LATEST_BY_SESSION: dict[str, dict[str, Any]] = {}


def _parse_mcp_tool_result(result: str) -> dict[str, Any] | None:
    try:
        outer = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(outer, dict) or "error" in outer:
        return None

    inner_text = outer.get("result")
    if not isinstance(inner_text, str):
        return None

    try:
        payload = json.loads(inner_text)
    except (json.JSONDecodeError, TypeError):
        return None

    return payload if isinstance(payload, dict) else None


def capture_query_brain_feedback_candidate(
    *,
    session_id: str,
    tool_call_id: str,
    args: dict[str, Any] | None,
    result: str,
    source: str = "hermes",
) -> None:
    if not session_id:
        return

    payload = _parse_mcp_tool_result(result)
    if not payload:
        return

    results = payload.get("results")
    top = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
    query_text = str((args or {}).get("query") or payload.get("query") or "").strip()
    if not query_text:
        return

    query_id = str(payload.get("correlation_id") or "").strip()
    if not query_id:
        query_id = f"hermes:{session_id}:{tool_call_id or 'query_brain'}"

    candidate = {
        "query_id": query_id,
        "query_text": query_text,
        "result_kind": str(top.get("table")) if top.get("table") else None,
        "result_id": str(top.get("id")) if top.get("id") else None,
        "response_verdict": str(payload.get("verdict") or "").strip() or None,
        "source": source,
        "observed_at": time.time(),
    }

    with _LOCK:
        _LATEST_BY_SESSION[session_id] = candidate


def capture_analyze_brain_feedback_candidate(
    *,
    session_id: str,
    tool_call_id: str,
    args: dict[str, Any] | None,
    result: str,
    source: str = "hermes",
) -> None:
    """Feedback candidate for analyze_brain_query answers.

    Only real analytical answers are rateable: recall routes are followed by a
    query_brain call (captured separately), and clarification/unsupported/error
    routes carry no answer worth a verdict.
    """
    if not session_id:
        return

    payload = _parse_mcp_tool_result(result)
    if not payload:
        return

    route = str(payload.get("route") or "").strip()
    if route not in ("analytical", "hybrid"):
        return

    query_text = str((args or {}).get("question") or "").strip()
    if not query_text:
        return

    candidate = {
        "query_id": f"hermes:{session_id}:{tool_call_id or 'analyze_brain_query'}",
        "query_text": query_text,
        "result_kind": None,
        "result_id": None,
        "response_verdict": "analytical",
        "source": source,
        "observed_at": time.time(),
    }

    with _LOCK:
        _LATEST_BY_SESSION[session_id] = candidate


def pop_feedback_candidate(
    session_id: str,
    *,
    since: float | None = None,
) -> dict[str, Any] | None:
    if not session_id:
        return None

    with _LOCK:
        candidate = _LATEST_BY_SESSION.get(session_id)
        if not candidate:
            return None
        observed_at = float(candidate.get("observed_at") or 0.0)
        if since is not None and observed_at < since:
            return None
        return _LATEST_BY_SESSION.pop(session_id, None)


def clear_feedback_candidate(session_id: str) -> None:
    if not session_id:
        return
    with _LOCK:
        _LATEST_BY_SESSION.pop(session_id, None)
