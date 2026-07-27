"""Durable proactive inbox delivery for connected Hermes clients.

This is a transport, not a feature-specific queue. Producers supply a stable
event id, content, and optional generic ``act:`` callbacks. The destination is
either a durable session id or an existing gateway peer mapping
(``platform`` + ``chat_id`` + optional thread/user), so the inbox never grows a
parallel identity system.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gateway.platforms.actions import decode_action_callback
from hermes_state import SessionDB

CLIENT_INBOX_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
MAX_EVENT_ID_CHARS = 200
MAX_KIND_CHARS = 80
MAX_BODY_CHARS = 200_000
MAX_ACTIONS = 50


class ClientInboxValidationError(ValueError):
    """The producer supplied a malformed inbox record."""


class ClientInboxTargetError(LookupError):
    """The requested peer/session target could not be resolved."""


def _required_text(value: Any, name: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClientInboxValidationError(f"{name} is required")
    if len(text) > max_chars:
        raise ClientInboxValidationError(
            f"{name} exceeds the {max_chars}-character limit"
        )
    return text


def normalize_actions(actions: Any) -> List[Dict[str, str]]:
    if actions is None:
        return []
    if not isinstance(actions, list):
        raise ClientInboxValidationError("actions must be a list")
    if len(actions) > MAX_ACTIONS:
        raise ClientInboxValidationError(
            f"actions may contain at most {MAX_ACTIONS} items"
        )

    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in actions:
        if not isinstance(item, dict):
            raise ClientInboxValidationError("each action must be an object")
        label = _required_text(item.get("label"), "action label", 80)
        callback_id = _required_text(item.get("callback_id"), "action callback_id", 64)
        if decode_action_callback(callback_id) is None:
            raise ClientInboxValidationError(
                f"malformed action callback_id: {callback_id!r}"
            )
        if callback_id not in seen:
            normalized.append({"label": label, "callback_id": callback_id})
            seen.add(callback_id)
    return normalized


def resolve_session_id(
    *,
    db: SessionDB,
    session_id: Optional[str] = None,
    session_platform: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """Resolve a durable target through the canonical session or peer mapping."""
    if session_id:
        resolved = db.resolve_session_id(str(session_id).strip())
        if not resolved:
            raise ClientInboxTargetError(f"unknown session: {session_id}")
        return resolved

    platform = str(session_platform or "").strip().lower()
    peer_chat_id = str(chat_id or "").strip()
    if not platform or not peer_chat_id:
        raise ClientInboxTargetError(
            "session_id or both session_platform and chat_id are required"
        )

    from gateway.mirror import _find_session_id

    resolved = _find_session_id(
        platform,
        peer_chat_id,
        thread_id=str(thread_id) if thread_id is not None else None,
        user_id=str(user_id) if user_id is not None else None,
    )
    if not resolved or not db.get_session(resolved):
        raise ClientInboxTargetError(
            f"no durable session mapped to {platform}:{peer_chat_id}"
        )
    return resolved


def enqueue_client_inbox_item(
    *,
    event_id: str,
    body: str,
    kind: str = "proactive",
    priority: str = "normal",
    reference: Optional[Dict[str, Any]] = None,
    actions: Any = None,
    expires_at: Optional[float] = None,
    created_at: Optional[float] = None,
    session_id: Optional[str] = None,
    session_platform: Optional[str] = None,
    chat_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    db: Optional[SessionDB] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Resolve, validate, and persist one generic proactive inbox item."""
    owns_db = db is None
    store = db or SessionDB()
    try:
        target_session_id = resolve_session_id(
            db=store,
            session_id=session_id,
            session_platform=session_platform,
            chat_id=chat_id,
            thread_id=thread_id,
            user_id=user_id,
        )
        normalized_event_id = _required_text(event_id, "event_id", MAX_EVENT_ID_CHARS)
        normalized_body = _required_text(body, "body", MAX_BODY_CHARS)
        normalized_kind = _required_text(kind, "kind", MAX_KIND_CHARS)
        normalized_priority = str(priority or "normal").strip().lower()
        if normalized_priority not in CLIENT_INBOX_PRIORITIES:
            raise ClientInboxValidationError(
                "priority must be one of: low, normal, high, urgent"
            )
        if reference is not None and not isinstance(reference, dict):
            raise ClientInboxValidationError("reference must be an object")
        timestamp = time.time() if created_at is None else float(created_at)
        expiry = float(expires_at) if expires_at is not None else None
        if expiry is not None and expiry <= timestamp:
            raise ClientInboxValidationError("expires_at must be later than created_at")

        return store.create_client_inbox_item(
            event_id=normalized_event_id,
            session_id=target_session_id,
            created_at=timestamp,
            kind=normalized_kind,
            priority=normalized_priority,
            body=normalized_body,
            reference=reference,
            actions=normalize_actions(actions),
            expires_at=expiry,
        )
    finally:
        if owns_db:
            store.close()


def _json_object(raw: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientInboxValidationError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ClientInboxValidationError(f"{name} must decode to an object")
    return value


def _json_list(raw: Optional[str], name: str) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientInboxValidationError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, list):
        raise ClientInboxValidationError(f"{name} must decode to a list")
    return value


def add_parser(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "client-inbox",
        help="Deliver durable proactive items to connected Hermes clients",
    )
    commands = parser.add_subparsers(dest="client_inbox_action", required=True)
    enqueue = commands.add_parser("enqueue", help="Persist one idempotent inbox item")
    enqueue.add_argument("--event-id", required=True)
    target = enqueue.add_mutually_exclusive_group(required=True)
    target.add_argument("--session-id")
    target.add_argument("--session-platform")
    enqueue.add_argument("--chat-id")
    enqueue.add_argument("--thread-id")
    enqueue.add_argument("--user-id")
    enqueue.add_argument("--kind", default="proactive")
    enqueue.add_argument("--priority", default="normal")
    enqueue.add_argument("--body")
    enqueue.add_argument(
        "--body-file",
        help="Read the body from this UTF-8 file, or '-' for stdin",
    )
    enqueue.add_argument("--reference-json")
    enqueue.add_argument("--actions-json")
    enqueue.add_argument("--created-at", type=float)
    enqueue.add_argument("--expires-at", type=float)
    parser.set_defaults(func=cmd_client_inbox)
    return parser


def cmd_client_inbox(args) -> int:
    if args.client_inbox_action != "enqueue":
        raise ClientInboxValidationError("unsupported client-inbox action")
    if args.session_platform and not args.chat_id:
        raise ClientInboxValidationError(
            "--chat-id is required with --session-platform"
        )
    if args.body is not None and args.body_file:
        raise ClientInboxValidationError("use only one of --body or --body-file")
    if args.body_file == "-":
        body = sys.stdin.read()
    elif args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        body = args.body or ""

    try:
        item, created = enqueue_client_inbox_item(
            event_id=args.event_id,
            body=body,
            kind=args.kind,
            priority=args.priority,
            reference=_json_object(args.reference_json, "reference-json"),
            actions=_json_list(args.actions_json, "actions-json"),
            expires_at=args.expires_at,
            created_at=args.created_at,
            session_id=args.session_id,
            session_platform=args.session_platform,
            chat_id=args.chat_id,
            thread_id=args.thread_id,
            user_id=args.user_id,
        )
    except (ClientInboxValidationError, ClientInboxTargetError, OSError) as exc:
        print(f"client-inbox: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "created": created,
                "event_id": item["event_id"],
                "session_id": item["session_id"],
            },
            sort_keys=True,
        )
    )
    return 0
