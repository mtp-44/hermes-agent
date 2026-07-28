"""Durable, one-shot correlations for Signal reaction feedback.

Signal identifies a reacted-to message by its outbound millisecond timestamp.
Keep only the correlation needed to route a supported reaction back through the
generic action seam; message bodies and Signal envelopes are never stored here.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


class SignalReactionFeedbackStore:
    """A small SQLite store scoped to one Hermes profile.

    Claiming is message-wide and atomic: 👍 followed by 👎 (or a replay) can
    dispatch at most one handler for the same reacted-to Signal message.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            # The payload can contain a query-feedback candidate needed after a
            # restart. Keep it in the mode-0600 main database, not WAL sidecars
            # created with the process umask.
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_reaction_feedback (
                    account TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_timestamp TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    payload_json TEXT,
                    expires_at REAL NOT NULL,
                    claimed_at REAL,
                    PRIMARY KEY (account, chat_id, message_timestamp, action_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_reaction_feedback_expiry
                ON signal_reaction_feedback (expires_at)
                """
            )
            # Numbered actions (WP4) are a separate table on purpose. A digest
            # offers three choices per commitment, so the message-wide atomic
            # claim above — right for one binary verdict per answer — would let
            # acting on item 1 silently consume items 2..n.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_numbered_actions (
                    account TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_timestamp TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    action_id TEXT NOT NULL,
                    token TEXT NOT NULL,
                    label TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (account, chat_id, message_timestamp, ordinal, action_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_signal_numbered_actions_lookup
                ON signal_numbered_actions (account, chat_id, expires_at)
                """
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def store_actions(
        self,
        *,
        account: str,
        chat_id: str,
        message_timestamp: str | int,
        actions: Iterable[dict[str, Any]],
        ttl_seconds: float,
    ) -> None:
        now = time.time()
        expires_at = now + ttl_seconds
        rows: list[tuple[str, str, str, str, str, str | None, float]] = []
        for action in actions:
            action_id = str(action.get("action_id") or "").strip()
            token = str(action.get("token") or "").strip()
            if not action_id or not token:
                continue
            payload = action.get("payload")
            payload_json = json.dumps(payload, separators=(",", ":")) if payload is not None else None
            rows.append((
                account,
                chat_id,
                str(message_timestamp),
                action_id,
                token,
                payload_json,
                expires_at,
            ))
        if not rows:
            return
        with self._connect() as conn:
            conn.execute("DELETE FROM signal_reaction_feedback WHERE expires_at <= ?", (now,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO signal_reaction_feedback
                    (account, chat_id, message_timestamp, action_id, token, payload_json, expires_at, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                rows,
            )

    def store_numbered_actions(
        self,
        *,
        account: str,
        chat_id: str,
        message_timestamp: str | int,
        numbered: Iterable[tuple[int, str, str, str]],
        ttl_seconds: float,
    ) -> int:
        """Record ``(ordinal, action_id, token, label)`` for one outbound message.

        Returns the number of rows stored. Ordinals are only meaningful with the
        message that showed them, so the newest message in a chat wins on lookup
        — a fresh digest renumbers, and yesterday's "3" must not resolve against
        today's list.
        """
        now = time.time()
        expires_at = now + ttl_seconds
        rows = [
            (account, chat_id, str(message_timestamp), int(ordinal), action_id, token, label, expires_at)
            for ordinal, action_id, token, label in numbered
            if action_id and token
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.execute("DELETE FROM signal_numbered_actions WHERE expires_at <= ?", (now,))
            conn.executemany(
                """
                INSERT OR REPLACE INTO signal_numbered_actions
                    (account, chat_id, message_timestamp, ordinal, action_id, token, label, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def resolve_numbered_action(
        self,
        *,
        account: str,
        chat_id: str,
        ordinal: int,
        action_id: str,
    ) -> dict[str, Any] | None:
        """Look up one numbered action from the most recent message that offered it.

        Not one-shot: these replace buttons that stayed pressable, so correcting
        a snooze to a done must keep working until the correlation expires.
        """
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT token, label, message_timestamp FROM signal_numbered_actions
                WHERE account = ? AND chat_id = ? AND ordinal = ? AND action_id = ?
                  AND expires_at > ?
                ORDER BY CAST(message_timestamp AS INTEGER) DESC
                LIMIT 1
                """,
                (account, chat_id, int(ordinal), action_id, now),
            ).fetchone()
        if row is None:
            return None
        return {
            "token": row["token"],
            "label": row["label"],
            "message_timestamp": row["message_timestamp"],
        }

    def claim(
        self,
        *,
        account: str,
        chat_id: str,
        message_timestamp: str | int,
        action_id: str,
    ) -> dict[str, Any] | None:
        """Atomically consume one supported action, or return ``None``."""
        now = time.time()
        key = (account, chat_id, str(message_timestamp))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT token, payload_json FROM signal_reaction_feedback
                WHERE account = ? AND chat_id = ? AND message_timestamp = ?
                  AND action_id = ? AND claimed_at IS NULL AND expires_at > ?
                """,
                (*key, action_id, now),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            # Consume every action on the target so replacement/replay cannot
            # produce a second Open Brain write (notably proactive feedback).
            conn.execute(
                """
                UPDATE signal_reaction_feedback SET claimed_at = ?
                WHERE account = ? AND chat_id = ? AND message_timestamp = ?
                  AND claimed_at IS NULL
                """,
                (now, *key),
            )
            conn.commit()
        payload = None
        if row["payload_json"]:
            try:
                parsed = json.loads(row["payload_json"])
                payload = parsed if isinstance(parsed, dict) else None
            except (TypeError, json.JSONDecodeError):
                payload = None
        return {"token": row["token"], "payload": payload}
