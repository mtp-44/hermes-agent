"""Open Brain memory provider.

First-pass implementation: consume Hermes session-close capture records and
write them into the hosted ``open-brain-mcp`` durable memory layer via its
``capture_thought`` MCP tool.

This provider intentionally keeps scope narrow:
- no model tools
- no auto-recall by default
- durable writes only on session boundaries

That gives Hermes a clean real-world path:
Telegram/CLI/session boundary -> capture policy -> Open Brain durable store
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

_DEFAULT_MCP_URL = "https://icxyfzzbsrsiyaqnynum.supabase.co/functions/v1/open-brain-mcp"
_DEFAULT_TIMEOUT = 15.0
_SYNC_LEDGER_FILENAME = "openbrain_sync.json"
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE = 1.0


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    except Exception:
        logger.debug("OpenBrain env file read failed: %s", path, exc_info=True)
    return values


def _parse_mcp_response(body: str) -> dict:
    trimmed = (body or "").strip()
    if not trimmed:
        return {}
    if trimmed.startswith("{"):
        return json.loads(trimmed)

    data_lines = []
    for line in trimmed.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload and payload != "[DONE]":
            data_lines.append(payload)

    for payload in reversed(data_lines):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            continue
    raise ValueError("Unable to parse MCP response body")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class OpenBrainMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._session_id = ""
        self._platform = ""
        self._hermes_home = ""
        self._agent_context = "primary"
        self._mcp_url = _DEFAULT_MCP_URL
        self._mcp_key = ""
        try:
            self._timeout = max(
                2.0,
                min(
                    60.0,
                    float(os.environ.get("OPENBRAIN_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT))),
                ),
            )
        except Exception:
            self._timeout = _DEFAULT_TIMEOUT
        self._sync_lock = threading.Lock()
        self._sync_ledger_path: Optional[Path] = None
        self._synced_record_ids: set[str] = set()
        self._refresh_config()

    @property
    def name(self) -> str:
        return "openbrain"

    def is_available(self) -> bool:
        self._refresh_config()
        return bool(self._mcp_url and self._mcp_key)

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id or ""
        self._platform = str(kwargs.get("platform") or "")
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        self._refresh_config()
        if self._hermes_home:
            self._sync_ledger_path = Path(self._hermes_home) / _SYNC_LEDGER_FILENAME
            self._load_sync_ledger()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        return None

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return ""

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = new_session_id or self._session_id

    def on_session_end(
        self,
        messages: List[Dict[str, Any]],
        *,
        capture_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self._agent_context != "primary":
            return
        context = dict(capture_context or {})
        if not context.get("eligible"):
            return

        records = context.get("capture_records") or []
        if not isinstance(records, list) or not records:
            return

        successes = 0
        failures = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = self._record_sync_id(record, context)
            if self._is_synced(record_id):
                continue
            try:
                self._capture_record(record, context)
                self._mark_synced(record_id)
                successes += 1
            except Exception as exc:
                failures += 1
                logger.warning(
                    "OpenBrain capture failed: boundary=%s type=%s error=%s",
                    context.get("boundary_id", ""),
                    record.get("type", ""),
                    exc,
                )

        if successes or failures:
            logger.info(
                "OpenBrain session capture complete: boundary=%s success=%d failure=%d",
                context.get("boundary_id", ""),
                successes,
                failures,
            )

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "mcp_url",
                "description": "Open Brain MCP endpoint URL",
                "required": False,
                "default": _DEFAULT_MCP_URL,
            },
            {
                "key": "mcp_key",
                "description": "Open Brain MCP access key",
                "secret": True,
                "required": True,
                "env_var": "OPENBRAIN_MCP_KEY",
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        url = str(values.get("mcp_url") or "").strip()
        if not url:
            return
        path = Path(hermes_home) / "openbrain.json"
        existing: Dict[str, Any] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = raw
            except Exception:
                existing = {}
        existing["mcp_url"] = url
        path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _load_sync_ledger(self) -> None:
        path = self._sync_ledger_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                synced = raw.get("synced_record_ids", [])
                if isinstance(synced, list):
                    self._synced_record_ids = {str(item) for item in synced if item}
        except Exception:
            logger.debug("OpenBrain sync ledger load failed", exc_info=True)

    def _save_sync_ledger(self) -> None:
        path = self._sync_ledger_path
        if path is None:
            return
        payload = {"synced_record_ids": sorted(self._synced_record_ids)}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _is_synced(self, record_id: str) -> bool:
        with self._sync_lock:
            return record_id in self._synced_record_ids

    def _mark_synced(self, record_id: str) -> None:
        with self._sync_lock:
            self._synced_record_ids.add(record_id)
            self._save_sync_ledger()

    def _record_sync_id(self, record: Dict[str, Any], context: Dict[str, Any]) -> str:
        stable = {
            "boundary_id": context.get("boundary_id", ""),
            "type": record.get("type", ""),
            "payload": record,
        }
        return sha256(_json_dumps(stable).encode("utf-8")).hexdigest()[:32]

    def _capture_record(self, record: Dict[str, Any], context: Dict[str, Any]) -> None:
        # capture_thought only accepts content/metadata/embedding/contact_id
        # (see its MCP schema) — record_type/source_app must live under
        # `metadata`, matching the contract gateway/open_brain.py's
        # _is_hermes_brief_candidate() reads for /brief, /digest, and /stale.
        # Previously this sent domain/category/subcategory as top-level args,
        # which the server silently drops, so these captures never carried
        # the record_type/source_app the read-side filters require.
        from gateway.open_brain import _HERMES_CAPTURE_SOURCE_APP, _HERMES_CAPTURE_SCHEMA_VERSION

        record_type = str(record.get("type") or "hermes_capture")
        platform = str(context.get("platform") or "")
        session_id = str(context.get("session_id") or "")
        metadata = {
            "record_type": record_type,
            "source": platform or None,
            "source_app": _HERMES_CAPTURE_SOURCE_APP,
            "session_id": session_id or None,
            "domain": "brain",
            "category": record_type,
            "subcategory": str(record.get("routing") or "canonical"),
            "provenance": (
                f"Hermes {context.get('boundary_reason', '')} capture "
                f"({record_type}) from {platform} session {session_id}."
            ),
            "schema_version": _HERMES_CAPTURE_SCHEMA_VERSION,
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}
        args = {
            "content": self._render_record_content(record, context),
            "metadata": metadata,
        }
        result = self._call_mcp_tool("capture_thought", args)
        if result.get("error"):
            raise RuntimeError(str(result["error"]))

    def _call_mcp_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        self._refresh_config()
        url = self._mcp_url
        key = self._mcp_key
        if not url or not key:
            raise RuntimeError("Open Brain MCP URL/key not configured")

        sep = "&" if "?" in url else "?"
        target = f"{url}{sep}key={urllib.parse.quote(key)}"
        payload = {
            "jsonrpc": "2.0",
            "id": int(threading.get_ident()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": args,
            },
        }
        req = urllib.request.Request(
            target,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        body = ""
        last_exc: Optional[Exception] = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
                last_exc = None
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code < 500:
                    raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
                last_exc = RuntimeError(f"HTTP {exc.code}: {body[:300]}")
            except urllib.error.URLError as exc:
                last_exc = RuntimeError(f"network error: {exc}")
            if attempt < _RETRY_ATTEMPTS - 1:
                delay = _RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug("OpenBrain MCP call failed, retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, _RETRY_ATTEMPTS)
                time.sleep(delay)
        if last_exc is not None:
            raise last_exc

        parsed = _parse_mcp_response(body)
        if parsed.get("error"):
            message = parsed["error"].get("message") if isinstance(parsed["error"], dict) else parsed["error"]
            return {"error": message or "unknown MCP error"}
        return {"result": parsed.get("result")}

    def _render_record_content(self, record: Dict[str, Any], context: Dict[str, Any]) -> str:
        lines = [
            f"Hermes auto-capture: {record.get('type', 'record')}",
            f"Session: {context.get('session_id', '')}",
            f"Boundary: {context.get('boundary_reason', '')}",
            f"Routing: {record.get('routing', 'canonical')}",
        ]
        record_type = str(record.get("type") or "")
        if record_type == "session_summary":
            lines.append(f"Summary: {record.get('summary_text', '')}")
            topics = record.get("topics") or []
            if topics:
                lines.append(f"Topics: {', '.join(str(t) for t in topics)}")
        elif record_type == "action_item":
            lines.append(f"Title: {record.get('title', '')}")
            if record.get("details"):
                lines.append(f"Details: {record.get('details', '')}")
            lines.append(f"Owner: {record.get('owner', '')}")
            lines.append(f"Status: {record.get('status', '')}")
        elif record_type == "decision_record":
            lines.append(f"Decision: {record.get('decision', '')}")
            if record.get("rationale_summary"):
                lines.append(f"Rationale: {record.get('rationale_summary', '')}")
        elif record_type == "durable_fact":
            lines.append(
                f"Fact: {record.get('subject', '')} {record.get('predicate', '')} {record.get('value', '')}".strip()
            )

        compact = {
            "boundary_id": context.get("boundary_id", ""),
            "record": record,
        }
        lines.append("")
        lines.append("Structured metadata:")
        lines.append(_json_dumps(compact))
        return "\n".join(lines).strip()

    def _refresh_config(self) -> None:
        env_url = os.environ.get("OPENBRAIN_MCP_URL", "").strip()
        env_key = (
            os.environ.get("OPENBRAIN_MCP_KEY")
            or os.environ.get("MCP_ACCESS_KEY")
            or ""
        ).strip()

        file_url = ""
        file_key = ""
        hermes_home = self._hermes_home or os.environ.get("HERMES_HOME", "").strip()
        if hermes_home:
            config_path = Path(hermes_home) / "openbrain.json"
            if config_path.exists():
                try:
                    raw = json.loads(config_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        file_url = str(raw.get("mcp_url") or "").strip()
                except Exception:
                    logger.debug("OpenBrain config load failed", exc_info=True)
            env_values = _read_env_file(Path(hermes_home) / ".env")
            file_key = (
                env_values.get("OPENBRAIN_MCP_KEY")
                or env_values.get("MCP_ACCESS_KEY")
                or ""
            ).strip()

        self._mcp_url = env_url or file_url or _DEFAULT_MCP_URL
        self._mcp_key = env_key or file_key


def register(ctx) -> None:
    ctx.register_memory_provider(OpenBrainMemoryProvider())
