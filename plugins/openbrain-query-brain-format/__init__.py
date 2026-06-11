from __future__ import annotations

import json
from typing import Any

from gateway.open_brain_feedback import (
    capture_analyze_brain_feedback_candidate,
    capture_query_brain_feedback_candidate,
)

_QUERY_BRAIN_TOOL_NAME = "mcp_open_brain_query_brain"
_ANALYZE_BRAIN_TOOL_NAME = "mcp_open_brain_analyze_brain_query"
_DIRECT_REPLY_SCORE_THRESHOLD = 0.75


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
        return (
            "I found a possible lead, but I wouldn't present it confidently because "
            f"retrieval was limited ({_warning_summary(warnings)})."
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
