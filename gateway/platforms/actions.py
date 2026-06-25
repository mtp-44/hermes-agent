"""Generic platform message actions (interactive buttons).

A platform-agnostic way to attach interactive buttons to an outbound message
and route a press back to a handler, without any feature-specific knowledge in
the platform layer. Platforms render :class:`MessageAction`s natively (Telegram
inline keyboards, Discord components, …) and translate a press into a generic
``action:invoked`` dispatch.

This is the generic seam that feature code (query feedback, proactive
useful/dismiss, future confirmations) consumes instead of hand-writing inline
keyboards and callback parsing into each platform adapter.

Wire format (callback id): ``act:<action_id>:<token>``

* ``action_id`` — what was pressed (e.g. ``"good"``); the handler switches on it.
* ``token`` — an opaque correlation handle the producer minted (e.g. a registry
  key pointing at the staged context for this message). Never put payloads here.

Both segments must be free of ``:`` and the whole callback id must stay within
the platform's callback-data budget (Telegram caps at 64 bytes), so keep
``action_id`` and ``token`` short.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

ACTION_CALLBACK_PREFIX = "act"
# Telegram's callback_data hard limit; the smallest common denominator.
MAX_CALLBACK_BYTES = 64


@dataclass(frozen=True)
class MessageAction:
    """One interactive button.

    ``label`` is what the user sees; ``action_id`` is what the handler receives;
    ``token`` is an opaque per-message correlation handle.
    """

    label: str
    action_id: str
    token: str = ""

    def callback_id(self) -> str:
        return encode_action_callback(self.action_id, self.token)


def encode_action_callback(action_id: str, token: str = "") -> str:
    """Build the wire callback id for an action.

    Raises ``ValueError`` if a segment contains the ``:`` separator or the
    encoded id exceeds the callback-data budget — failing loud here beats a
    silently-truncated callback that never round-trips.
    """
    aid = str(action_id or "").strip()
    tok = str(token or "").strip()
    if not aid:
        raise ValueError("action_id is required")
    if ":" in aid or ":" in tok:
        raise ValueError("action_id and token must not contain ':'")
    encoded = f"{ACTION_CALLBACK_PREFIX}:{aid}:{tok}"
    if len(encoded.encode("utf-8")) > MAX_CALLBACK_BYTES:
        raise ValueError(
            f"encoded action callback exceeds {MAX_CALLBACK_BYTES} bytes: {encoded!r}"
        )
    return encoded


def is_action_callback(data: Optional[str]) -> bool:
    return bool(data) and data.startswith(ACTION_CALLBACK_PREFIX + ":")


def decode_action_callback(data: str) -> Optional[tuple[str, str]]:
    """Parse ``act:<action_id>:<token>`` → ``(action_id, token)``.

    Returns ``None`` when ``data`` is not a well-formed action callback, so a
    dispatcher can cleanly fall through to other callback families.
    """
    if not is_action_callback(data):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or not parts[1]:
        return None
    return parts[1], parts[2]


def _coerce_action(item: Any) -> Optional[MessageAction]:
    if isinstance(item, MessageAction):
        return item
    if isinstance(item, dict):
        label = str(item.get("label") or "").strip()
        action_id = str(item.get("action_id") or "").strip()
        if not label or not action_id:
            return None
        return MessageAction(
            label=label,
            action_id=action_id,
            token=str(item.get("token") or ""),
        )
    return None


def action_rows_from_metadata(
    metadata: Optional[Dict[str, Any]],
) -> List[List[MessageAction]]:
    """Normalize a message's ``metadata`` into rows of :class:`MessageAction`.

    Accepts either ``metadata["actions"]`` (a flat list rendered as one row) or
    ``metadata["action_rows"]`` (a list of rows). Malformed entries are dropped;
    an empty result means "no actions" and callers should attach no markup.
    """
    if not metadata:
        return []

    rows_src: List[List[Any]]
    if isinstance(metadata.get("action_rows"), list):
        rows_src = [r if isinstance(r, list) else [r] for r in metadata["action_rows"]]
    elif isinstance(metadata.get("actions"), list):
        rows_src = [metadata["actions"]]
    else:
        return []

    rows: List[List[MessageAction]] = []
    for row_src in rows_src:
        row = [a for a in (_coerce_action(i) for i in row_src) if a is not None]
        if row:
            rows.append(row)
    return rows
