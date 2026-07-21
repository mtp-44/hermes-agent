"""Shared session-capture policy and provenance helpers.

This module defines the boundary/provenance contract Hermes uses when a
session ends or rotates. It also runs a first-pass auto-capture policy so
providers can consume structured session summaries, action items, decision
records, and durable facts from one consistent surface.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_TRIVIAL_RE = re.compile(
    r"^(ok|okay|thanks|thank you|got it|sure|yes|no|yep|nope|k|ty|thx|np|cool)\.?$",
    re.IGNORECASE,
)
# System-injected reference blocks (context-compaction summaries, etc.) arrive
# as user-role messages but are not real user utterances. They must not feed
# decision/commitment/fact extraction — a compaction summary that happens to
# contain "confirmed"/"accepted" was creating phantom user decisions.
_INJECTED_RE = re.compile(
    r"^\[[^\]]*(context compaction|reference only)[^\]]*\]",
    re.IGNORECASE,
)

_STOPWORDS = {
    "about", "after", "again", "also", "been", "being", "from", "have", "just",
    "more", "need", "that", "them", "then", "they", "this", "were", "what",
    "when", "where", "which", "with", "would", "your", "we", "you", "into",
    "onto", "than", "there", "their", "will", "shall", "should", "could",
}

# Text truncation limits (chars)
_TEXT_LIMIT_SHORT = 180     # action items, durable facts, default shorten
_TEXT_LIMIT_OUTCOME = 220   # session outcome field
_TEXT_LIMIT_DECISION = 200  # decision record text

# Max topics extracted per session
_TOPIC_LIMIT = 5

# At or above this threshold a record routes to canonical; below goes to pending
_CANONICAL_CONFIDENCE_THRESHOLD = 0.8

# Confidence policy (2026-07-21 signal-gate rewrite)
# -------------------------------------------------
# Open Brain's own capture policy is "capture only six-month-durable
# decisions/facts/commitments; MOST sessions produce ZERO captures". The old
# heuristic did the opposite: it emitted a session_summary at a blanket 0.95
# for every session with a user turn, so health-checks, ops questions, smoke
# tests, and one-off queries all landed as durable 0.95 "knowledge".
#
# The rule now: NO auto-capture record may outrank a deliberate human
# capture_thought, and a record only routes canonical when it carries an
# explicit *user-owned* durable signal. Anything softer (assistant-voiced,
# collaborative, suggested) routes pending and decays. A session is only
# eligible at all if it produced >=1 canonical record (see _build_capture_policy).

# session_summary is emitted ONLY for eligible sessions and rides along as
# decaying context — never a standalone durable fact, never 0.95.
_CONFIDENCE_SESSION_SUMMARY = 0.75

# Action items are deliberately kept below the canonical threshold: a user
# saying "I need to X" mid-chat is almost always asking the assistant to do X
# *now*, not recording a six-month-durable commitment. So action items never
# route canonical and never make a session eligible on their own — they ride
# along as decaying pending context in sessions already made eligible by a
# genuine user decision or durable fact. (Real durable commitments come through
# deliberate capture_thought / the commitments system.)
_CONFIDENCE_ACTION_USER_COMMITMENT = 0.78   # user: "I need to / I'll / I'm going to" -> pending
_CONFIDENCE_ACTION_USER_SOFT = 0.72         # user: "we should / let's / you should"  -> pending
_CONFIDENCE_ACTION_ASSISTANT = 0.6          # assistant-voiced "I'll / I can"          -> pending

_CONFIDENCE_DECISION_USER = 0.82            # user decision -> canonical
_CONFIDENCE_DECISION_ASSISTANT = 0.6        # assistant confirmation -> pending

_CONFIDENCE_FACT_PREFER = 0.85
_CONFIDENCE_FACT_DEFAULT_PREF = 0.82
_CONFIDENCE_FACT_LIKE = 0.8
_CONFIDENCE_FACT_USUALLY = 0.75
_CONFIDENCE_FACT_USE = 0.75

# How long a pending record survives before automatic decay
_PENDING_DECAY_DAYS = 14

# Minimum sentence length to be considered an action item candidate
_ACTION_ITEM_MIN_LENGTH = 12


@dataclass
class SessionCaptureContext:
    """Structured provenance for a session-boundary capture attempt."""

    session_id: str
    boundary_reason: str
    platform: str
    captured_at: str
    source_layer: str = "session_memory"
    captured_by: str = "hermes_auto_capture"
    parent_session_id: str = ""
    user_id: str = ""
    chat_id: str = ""
    message_count: int = 0
    message_refs: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if not payload.get("parent_session_id"):
            payload.pop("parent_session_id", None)
        if not payload.get("user_id"):
            payload.pop("user_id", None)
        if not payload.get("chat_id"):
            payload.pop("chat_id", None)
        if not payload.get("message_refs"):
            payload.pop("message_refs", None)
        return payload


def _normalize_text(text: Any) -> str:
    if isinstance(text, str):
        raw = text
    elif isinstance(text, list):
        parts: list[str] = []
        for item in text:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        raw = "\n".join(parts)
    elif text is None:
        raw = ""
    else:
        raw = str(text)
    return re.sub(r"\s+", " ", raw).strip()


def _is_trivial(text: str) -> bool:
    return not text or bool(_TRIVIAL_RE.match(text.strip()))


def _shorten(text: str, limit: int = _TEXT_LIMIT_SHORT) -> str:
    text = _normalize_text(text)
    if len(text) <= limit:
        return text
    truncated = text[: limit - 3].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated + "..."


def _message_text(msg: Dict[str, Any]) -> str:
    return _normalize_text(msg.get("content"))


def build_message_refs(
    messages: List[Dict[str, Any]],
    *,
    start_index: int = 0,
) -> List[Dict[str, Any]]:
    """Build lightweight message references for later provenance lookup."""
    refs: List[Dict[str, Any]] = []
    for idx, msg in enumerate(messages or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        if not role:
            continue
        ref: Dict[str, Any] = {"index": start_index + idx, "role": role}
        tool_name = msg.get("tool_name") or msg.get("name")
        if tool_name:
            ref["tool_name"] = str(tool_name)
        tool_call_id = msg.get("tool_call_id")
        if tool_call_id:
            ref["tool_call_id"] = str(tool_call_id)
        refs.append(ref)
    return refs


def _meaningful_messages(
    messages: List[Dict[str, Any]],
    *,
    start_index: int = 0,
) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for idx, msg in enumerate(messages or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        text = _message_text(msg)
        if _is_trivial(text):
            continue
        if _INJECTED_RE.match(text):
            continue
        kept.append({"index": start_index + idx, "role": role, "text": text})
    return kept


def _extract_topics(messages: List[Dict[str, Any]], limit: int = _TOPIC_LIMIT) -> List[str]:
    counts: Dict[str, int] = {}
    for msg in messages:
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", msg.get("text", "").lower()):
            if token in _STOPWORDS or token.isdigit():
                continue
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _ in ranked[:limit]]


def _record_provenance(base: Dict[str, Any], message_indexes: List[int]) -> Dict[str, Any]:
    refs = []
    for ref in base.get("message_refs", []) or []:
        idx = ref.get("index")
        if idx in message_indexes:
            refs.append(dict(ref))
    provenance = {
        "source_layer": "session_memory",
        "session_id": base.get("session_id", ""),
        "boundary_reason": base.get("boundary_reason", ""),
        "platform": base.get("platform", ""),
        "captured_by": "hermes_auto_capture",
        "captured_at": base.get("captured_at", ""),
        "message_refs": refs,
    }
    if base.get("user_id"):
        provenance["user_id"] = base["user_id"]
    return provenance


def _build_session_summary(base: Dict[str, Any], meaningful: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    users = [m for m in meaningful if m["role"] == "user"]
    assistants = [m for m in meaningful if m["role"] == "assistant"]
    if not users:
        return None

    objective = _shorten(users[0]["text"], _TEXT_LIMIT_SHORT)
    outcome_source = assistants[-1]["text"] if assistants else users[-1]["text"]
    outcome = _shorten(outcome_source, _TEXT_LIMIT_OUTCOME)
    summary_text = f"Objective: {objective}"
    if outcome and outcome != objective:
        summary_text += f" Outcome: {outcome}"

    indexes = [m["index"] for m in meaningful]
    # The summary is supporting context for an already-eligible session, not a
    # durable fact in its own right: cap it below the canonical threshold so it
    # routes pending and decays, while the session's canonical decision/fact/
    # commitment records carry the durable knowledge.
    confidence = _CONFIDENCE_SESSION_SUMMARY
    routing = "canonical" if confidence >= _CANONICAL_CONFIDENCE_THRESHOLD else "pending"
    record = {
        "type": "session_summary",
        "session_id": base.get("session_id", ""),
        "parent_session_id": base.get("parent_session_id", ""),
        "boundary_reason": base.get("boundary_reason", ""),
        "platform": base.get("platform", ""),
        "user_id": base.get("user_id", ""),
        "summary_text": summary_text,
        "topics": _extract_topics(meaningful),
        "source_count": len(meaningful),
        "captured_at": base.get("captured_at", ""),
        "confidence": confidence,
        "routing": routing,
        "provenance": _record_provenance(base, indexes),
    }
    if routing == "pending":
        record["decay_at"] = (
            datetime.fromisoformat(base["captured_at"]) + timedelta(days=_PENDING_DECAY_DAYS)
        ).isoformat()
    return record


# Explicit first-person user commitment -> canonical-eligible.
_ACTION_USER_COMMITMENT_RE = re.compile(
    r"\b(i need to|i have to|i must|i will|i'll|i'm going to|i am going to|i plan to|i intend to)\b",
    re.IGNORECASE,
)
# Softer user-side intent (collaborative / suggested) -> pending only.
_ACTION_USER_SOFT_RE = re.compile(
    r"\b(you need to|you should|next step|follow up|action item|we need to|we should|let's|let us)\b",
    re.IGNORECASE,
)
# Assistant-voiced commitments ("I'll check…") are in-session task chatter, not
# durable user commitments -> pending only, low confidence, decays.
_ACTION_ASSISTANT_RE = re.compile(
    r"\b(i will|i'll|i can|i'm going to|i am going to)\b",
    re.IGNORECASE,
)


def _extract_action_items(base: Dict[str, Any], meaningful: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    action_items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for msg in meaningful:
        role = msg["role"]
        text = msg["text"]
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for sentence in sentences:
            cleaned = _shorten(sentence, _TEXT_LIMIT_SHORT)
            if len(cleaned) < _ACTION_ITEM_MIN_LENGTH:
                continue
            # Owner is the actual speaker, not a static label. The old code
            # tagged every "I'll…" match owner="user" regardless of role, so
            # assistant task-chatter became canonical user commitments at 0.88.
            owner = ""
            confidence = 0.0
            if role == "user":
                if _ACTION_USER_COMMITMENT_RE.search(cleaned):
                    owner, confidence = "user", _CONFIDENCE_ACTION_USER_COMMITMENT
                elif _ACTION_USER_SOFT_RE.search(cleaned):
                    owner, confidence = "user", _CONFIDENCE_ACTION_USER_SOFT
            elif role == "assistant":
                if _ACTION_ASSISTANT_RE.search(cleaned):
                    owner, confidence = "hermes", _CONFIDENCE_ACTION_ASSISTANT
            if not owner:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            routing = "canonical" if confidence >= _CANONICAL_CONFIDENCE_THRESHOLD else "pending"
            record = {
                "type": "action_item",
                "session_id": base.get("session_id", ""),
                "title": cleaned,
                "details": cleaned,
                "status": "open",
                "owner": owner,
                "related_entities": [],
                "captured_at": base.get("captured_at", ""),
                "confidence": round(confidence, 2),
                "routing": routing,
                "provenance": _record_provenance(base, [msg["index"]]),
            }
            if routing == "pending":
                record["decay_at"] = (
                    datetime.fromisoformat(base["captured_at"]) + timedelta(days=_PENDING_DECAY_DAYS)
                ).isoformat()
            action_items.append(record)
    return action_items


def _extract_decisions(base: Dict[str, Any], meaningful: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    decisions: List[Dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"\b(decided to|going with|we'll use|let's use|let's keep|we should use|accepted|confirmed)\b",
        re.IGNORECASE,
    )
    for msg in meaningful:
        text = msg["text"]
        if not pattern.search(text):
            continue
        cleaned = _shorten(text, _TEXT_LIMIT_DECISION)
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        confidence = _CONFIDENCE_DECISION_USER if msg["role"] == "user" else _CONFIDENCE_DECISION_ASSISTANT
        routing = "canonical" if confidence >= _CANONICAL_CONFIDENCE_THRESHOLD else "pending"
        record = {
            "type": "decision_record",
            "session_id": base.get("session_id", ""),
            "decision": cleaned,
            "rationale_summary": cleaned,
            "status": "accepted" if msg["role"] == "user" else "confirmed",
            "confidence": round(confidence, 2),
            "captured_at": base.get("captured_at", ""),
            "routing": routing,
            "provenance": _record_provenance(base, [msg["index"]]),
        }
        if routing == "pending":
            record["decay_at"] = (
                datetime.fromisoformat(base["captured_at"]) + timedelta(days=_PENDING_DECAY_DAYS)
            ).isoformat()
        decisions.append(record)
    return decisions


def _extract_durable_facts(base: Dict[str, Any], meaningful: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    seen: set[str] = set()
    explicit_patterns = [
        (re.compile(r"\bi prefer (?P<value>.+)", re.IGNORECASE), "prefers", _CONFIDENCE_FACT_PREFER),
        (re.compile(r"\bi like (?P<value>.+)", re.IGNORECASE), "likes", _CONFIDENCE_FACT_LIKE),
        (re.compile(r"\bi usually (?P<value>.+)", re.IGNORECASE), "usually", _CONFIDENCE_FACT_USUALLY),
        (re.compile(r"\bi use (?P<value>.+)", re.IGNORECASE), "uses", _CONFIDENCE_FACT_USE),
        (re.compile(r"\bmy default (?P<value>.+)", re.IGNORECASE), "default", _CONFIDENCE_FACT_DEFAULT_PREF),
    ]
    for msg in meaningful:
        if msg["role"] != "user":
            continue
        text = msg["text"]
        for pattern, predicate, confidence in explicit_patterns:
            match = pattern.search(text)
            if not match:
                continue
            value = _shorten(match.group("value"), _TEXT_LIMIT_SHORT).rstrip(".")
            if not value:
                continue
            key = f"{predicate}:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            routing = "canonical" if confidence >= _CANONICAL_CONFIDENCE_THRESHOLD else "pending"
            record = {
                "type": "durable_fact",
                "subject": "user",
                "predicate": predicate,
                "value": value,
                "confidence": round(confidence, 2),
                "captured_at": base.get("captured_at", ""),
                "routing": routing,
                "provenance": _record_provenance(base, [msg["index"]]),
            }
            if routing == "pending":
                record["decay_at"] = (
                    datetime.fromisoformat(base["captured_at"]) + timedelta(days=_PENDING_DECAY_DAYS)
                ).isoformat()
            facts.append(record)
            break
    return facts


def _boundary_id(
    *,
    session_id: str,
    boundary_reason: str,
    message_count: int,
    message_refs: List[Dict[str, Any]],
) -> str:
    raw = json.dumps(
        {
            "session_id": session_id,
            "boundary_reason": boundary_reason,
            "message_count": message_count,
            "message_refs": message_refs,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# Pure smoke-test / health-check openers. Used only to label the skip reason
# for observability — eligibility itself is decided by signal, not these.
_SMOKE_TEST_RE = re.compile(
    r"\b(test|testing|reply with exactly|write this exactly|private-smoke|hermes ok|codex ok|go boy)\b",
    re.IGNORECASE,
)
_HEALTH_CHECK_RE = re.compile(
    r"^(back|back up|you there|there|here|work|works|up|running|up and running|"
    r"you good|you good hermes|you ggod|good|ready|alive|online|ping|new)\b[\s\W]*$",
    re.IGNORECASE,
)


def _classify_low_signal(first_user_text: str, durable_records: List[Dict[str, Any]]) -> str:
    """Name *why* a content-bearing session produced no canonical record.

    Purely for logging/audit observability; the eligibility decision is the
    presence of a canonical record, not this label.
    """
    text = (first_user_text or "").strip()
    if _SMOKE_TEST_RE.search(text):
        return "smoke_test"
    if _HEALTH_CHECK_RE.match(text):
        return "health_check"
    if durable_records:
        # Had soft/pending signals (assistant chatter, "let's…") but nothing a
        # user explicitly owned as durable.
        return "no_canonical_signal"
    return "no_durable_signal"


def _build_capture_policy(
    base: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    message_index_offset: int = 0,
) -> Dict[str, Any]:
    meaningful = _meaningful_messages(messages, start_index=message_index_offset)
    users = [m for m in meaningful if m["role"] == "user"]
    assistants = [m for m in meaningful if m["role"] == "assistant"]

    if not meaningful or not users:
        logger.debug(
            "session_capture: skipping boundary=%s reason=no_meaningful_conversation messages=%d",
            base.get("boundary_id", ""),
            len(messages),
        )
        return {
            "eligible": False,
            "skip_reason": "no_meaningful_conversation",
            "records": [],
            "record_counts": {},
        }

    # Signal-based eligibility (2026-07-21): extract candidate records first,
    # then capture the session ONLY if it produced at least one canonical
    # (explicit user-owned) durable record. Health-checks, ops/meta questions,
    # smoke tests, one-off knowledge queries, and file-analysis runs yield no
    # canonical record and are skipped — matching Open Brain's "most sessions
    # produce ZERO captures" policy. The session_summary is deliberately NOT
    # part of this test: it is context that rides along with real signal, not
    # a reason to capture on its own.
    durable_records: List[Dict[str, Any]] = []
    durable_records.extend(_extract_action_items(base, meaningful))
    durable_records.extend(_extract_decisions(base, meaningful))
    durable_records.extend(_extract_durable_facts(base, meaningful))

    has_canonical = any(r.get("routing") == "canonical" for r in durable_records)
    if not has_canonical:
        skip_reason = _classify_low_signal(users[0]["text"], durable_records)
        logger.debug(
            "session_capture: skipping boundary=%s reason=%s meaningful=%d soft_records=%d",
            base.get("boundary_id", ""),
            skip_reason,
            len(meaningful),
            len(durable_records),
        )
        return {
            "eligible": False,
            "skip_reason": skip_reason,
            "records": [],
            "record_counts": {},
        }

    records: List[Dict[str, Any]] = []
    summary = _build_session_summary(base, meaningful)
    if summary:
        records.append(summary)
    records.extend(durable_records)

    counts: Dict[str, int] = {}
    for record in records:
        counts[record["type"]] = counts.get(record["type"], 0) + 1

    logger.debug(
        "session_capture: eligible boundary=%s counts=%s meaningful=%d",
        base.get("boundary_id", ""),
        counts,
        len(meaningful),
    )
    return {
        "eligible": True,
        "skip_reason": "",
        "records": records,
        "record_counts": counts,
        "meaningful_message_count": len(meaningful),
        "user_message_count": len(users),
        "assistant_message_count": len(assistants),
    }


def persist_capture_artifact(
    hermes_home: str,
    capture_context: Dict[str, Any],
) -> Optional[str]:
    """Persist a boundary capture artifact for audit/debugging and safe retries."""
    boundary_id = str(capture_context.get("boundary_id") or "").strip()
    if not boundary_id:
        return None
    captures_dir = Path(hermes_home) / "session_captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    path = captures_dir / f"{boundary_id}.json"
    payload = dict(capture_context)
    payload.setdefault("persisted_at", datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def build_capture_context(
    *,
    session_id: str,
    boundary_reason: str,
    platform: str,
    messages: Optional[List[Dict[str, Any]]] = None,
    parent_session_id: str = "",
    user_id: str = "",
    chat_id: str = "",
    captured_at: Optional[str] = None,
    message_index_offset: int = 0,
) -> Dict[str, Any]:
    """Return a normalized capture-context dict for provider hooks."""
    message_list = list(messages or [])
    captured_at_value = captured_at or datetime.now(timezone.utc).isoformat()
    context = SessionCaptureContext(
        session_id=session_id or "",
        boundary_reason=boundary_reason or "",
        platform=platform or "",
        captured_at=captured_at_value,
        parent_session_id=parent_session_id or "",
        user_id=user_id or "",
        chat_id=chat_id or "",
        message_count=len(message_list),
        message_refs=build_message_refs(message_list, start_index=message_index_offset),
    ).to_dict()

    context["boundary_id"] = _boundary_id(
        session_id=session_id or "",
        boundary_reason=boundary_reason or "",
        message_count=len(message_list),
        message_refs=context.get("message_refs", []) or [],
    )
    policy = _build_capture_policy(
        context,
        message_list,
        message_index_offset=message_index_offset,
    )
    context["eligible"] = policy["eligible"]
    context["capture_skip_reason"] = policy.get("skip_reason", "")
    logger.info(
        "session_capture: boundary=%s reason=%s platform=%s eligible=%s skip=%s records=%s",
        context.get("boundary_id", "")[:12],
        boundary_reason,
        platform,
        policy["eligible"],
        policy.get("skip_reason", ""),
        policy.get("record_counts", {}),
    )
    context["capture_records"] = policy.get("records", [])
    context["capture_record_counts"] = policy.get("record_counts", {})
    if "meaningful_message_count" in policy:
        context["meaningful_message_count"] = policy["meaningful_message_count"]
    if "user_message_count" in policy:
        context["user_message_count"] = policy["user_message_count"]
    if "assistant_message_count" in policy:
        context["assistant_message_count"] = policy["assistant_message_count"]
    return context
