from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from typing import Any
from uuid import uuid4

from gateway.open_brain_feedback import (
    capture_analyze_brain_feedback_candidate,
    capture_query_brain_feedback_candidate,
    pop_feedback_candidate,
)

logger = logging.getLogger(__name__)

_QUERY_BRAIN_TOOL_NAME = "mcp_open_brain_query_brain"
_ANALYZE_BRAIN_TOOL_NAME = "mcp_open_brain_analyze_brain_query"
_DIRECT_REPLY_SCORE_THRESHOLD = 0.75

# Generic message-action ids for query-answer feedback (kept short — they travel
# in the bounded ``act:<action_id>:<token>`` callback id).
_FEEDBACK_GOOD = "obg"
_FEEDBACK_BAD = "obb"

# Plugin-owned token registry: opaque token -> feedback candidate. The token is
# minted when buttons are attached (outbound decorator) and resolved when a
# button is pressed (action handler). Replaces the old telegram-adapter
# ``_feedback_entries`` registry — the adapter no longer knows about feedback.
_FEEDBACK_BY_TOKEN: "OrderedDict[str, dict]" = OrderedDict()
_FEEDBACK_MAX_ENTRIES = 200
_FEEDBACK_LOCK = threading.Lock()


def _register_feedback(candidate: dict) -> str:
    token = uuid4().hex[:12]
    with _FEEDBACK_LOCK:
        _FEEDBACK_BY_TOKEN[token] = candidate
        while len(_FEEDBACK_BY_TOKEN) > _FEEDBACK_MAX_ENTRIES:
            _FEEDBACK_BY_TOKEN.popitem(last=False)
    return token


def _resolve_feedback(token: str) -> dict | None:
    with _FEEDBACK_LOCK:
        return _FEEDBACK_BY_TOKEN.pop(token, None)


def _humanize_warning(warning: str) -> str:
    if warning == "semantic_unavailable":
        return "semantic retrieval was unavailable"
    if warning.startswith("embedding_error:"):
        return "semantic retrieval had an embedding error"
    return warning.replace("_", " ")


def _warning_summary(warnings: list[str]) -> str:
    if not warnings:
        return ""
    return "; ".join(_humanize_warning(warning) for warning in warnings[:2])


def _is_aggregate_result(result: dict[str, Any]) -> bool:
    metadata = result.get("metadata") or {}
    return metadata.get("aggregate") is True


def _lead_summaries(results: list[dict[str, Any]], limit: int = 3) -> str:
    summaries = []
    for result in results[:limit]:
        summary = str(result.get("content_summary") or "").strip()
        if summary:
            summaries.append(summary[:200])
    return " | ".join(summaries)


def _direct_retrieval_reply(payload: dict[str, Any]) -> str | None:
    warnings = payload.get("warnings") or []
    results = payload.get("results") or []

    if not results:
        if warnings:
            return (
                "I couldn't find a confident answer in your brain for that, "
                f"and retrieval was limited because {_warning_summary(warnings)}."
            )
        return "I couldn't find a confident answer in your brain for that."

    top = results[0]
    top_summary = str(top.get("content_summary") or "").strip()
    top_score = float(top.get("score") or 0)
    only_aggregate_results = all(_is_aggregate_result(result) for result in results)

    if _is_aggregate_result(top) and top_summary:
        if warnings:
            return f"{top_summary} Retrieval was a bit limited because {_warning_summary(warnings)}."
        return top_summary

    if top_score < _DIRECT_REPLY_SCORE_THRESHOLD and warnings:
        leads = _lead_summaries(results)
        if leads:
            return (
                "Nothing scored confidently and retrieval was limited "
                f"({_warning_summary(warnings)}). Closest matches, to present "
                f"tentatively rather than as 'not found': {leads}"
            )
        return (
            "I couldn't find a confident answer in your brain for that, and "
            f"retrieval was limited because {_warning_summary(warnings)}."
        )

    if only_aggregate_results and top_summary:
        return top_summary

    return None


def _rewrite_result(result: str) -> str:
    try:
        outer = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result

    if not isinstance(outer, dict) or "error" in outer:
        return result

    inner_text = outer.get("result")
    if not isinstance(inner_text, str):
        return result

    try:
        payload = json.loads(inner_text)
    except (json.JSONDecodeError, TypeError):
        return result

    if not isinstance(payload, dict):
        return result

    direct_reply = _direct_retrieval_reply(payload)
    if not direct_reply:
        return result

    return json.dumps({"result": direct_reply}, ensure_ascii=False)


def _decorate_outbound(context: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Attach 👍/👎 query-feedback buttons to the outbound answer, if one is due.

    Generic outbound-decorator seam consumer (Phase 5c.3 step 2): pops the
    feedback candidate captured for this turn and returns the two actions. The
    platform renders them and routes a press to ``_handle_feedback``.
    Platforms: Telegram (inline keyboard) and the desktop/PWA renderer
    (message-action chips over the ``/api/ws`` gateway + ``/api/actions/
    dispatch`` round-trip).
    """
    if str(context.get("platform") or "").lower() not in {"telegram", "desktop"}:
        return None
    session_id = str(context.get("session_id") or "").strip()
    if not session_id:
        return None
    try:
        candidate = pop_feedback_candidate(session_id, since=context.get("since"))
    except Exception as exc:  # best-effort: never block delivery
        logger.debug("query-feedback candidate lookup failed: %s", exc)
        return None
    if not candidate:
        return None
    token = _register_feedback(candidate)
    return [
        {"label": "👍 Good", "action_id": _FEEDBACK_GOOD, "token": token},
        {"label": "👎 Bad", "action_id": _FEEDBACK_BAD, "token": token},
    ]


async def _handle_feedback(action_id: str, token: str, _context: dict[str, Any]) -> str:
    """Record a query-feedback button press (generic action-handler consumer)."""
    candidate = _resolve_feedback(token)
    if not candidate:
        return "This feedback prompt expired."
    verdict = "good" if action_id == _FEEDBACK_GOOD else "bad"
    try:
        from gateway.open_brain import record_query_feedback

        await record_query_feedback(
            query_id=str(candidate.get("query_id") or ""),
            query_text=str(candidate.get("query_text") or ""),
            verdict=verdict,
            source=str(candidate.get("source") or "hermes"),
            result_kind=candidate.get("result_kind"),
            result_id=candidate.get("result_id"),
            response_verdict=candidate.get("response_verdict"),
        )
    except Exception as exc:
        logger.warning("Failed to record query feedback: %s", exc)
        return "⚠️ Couldn't save feedback."
    return "Logged 👍" if verdict == "good" else "Logged 👎"


def register(ctx) -> None:
    def _post_tool_call(
        tool_name: str,
        args: dict[str, Any],
        result: str,
        session_id: str = "",
        tool_call_id: str = "",
        **_: Any,
    ) -> None:
        if tool_name == _QUERY_BRAIN_TOOL_NAME:
            capture_query_brain_feedback_candidate(
                session_id=session_id,
                tool_call_id=tool_call_id,
                args=args if isinstance(args, dict) else {},
                result=result,
            )
        elif tool_name == _ANALYZE_BRAIN_TOOL_NAME:
            capture_analyze_brain_feedback_candidate(
                session_id=session_id,
                tool_call_id=tool_call_id,
                args=args if isinstance(args, dict) else {},
                result=result,
            )

    def _transform_tool_result(tool_name: str, result: str, **_: Any) -> str | None:
        if tool_name != _QUERY_BRAIN_TOOL_NAME:
            return None
        return _rewrite_result(result)

    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
    # Query-answer feedback now rides the generic message-action seam (Phase
    # 5c.3 step 2): this adapter produces the buttons (outbound decorator) and
    # consumes the presses (action handlers); the gateway core and telegram
    # adapter no longer carry any Open Brain feedback logic.
    if hasattr(ctx, "register_outbound_decorator"):
        ctx.register_outbound_decorator(_decorate_outbound)
        ctx.register_action_handler(_FEEDBACK_GOOD, _handle_feedback)
        ctx.register_action_handler(_FEEDBACK_BAD, _handle_feedback)
