#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import time
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR))

from gateway.status import get_running_pid, read_runtime_status
from hermes_cli.config import read_raw_config
from hermes_cli.env_loader import load_hermes_dotenv


DEFAULT_HTTP_TIMEOUT_SECONDS = 2.0
DEFAULT_MIN_DISK_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_MIN_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_RESTART_BACKOFF_SECONDS = 300
DEFAULT_PWA_RELEASE_ROOT = PROJECT_DIR.parent / "hermes-agent-pwa-releases"
DEFAULT_PWA_STATUS_URLS = (
    "http://127.0.0.1:9219/api/status",
    "https://mini-mh.tailbd0650.ts.net/api/status",
)
DEFAULT_PWA_WS_URLS = (
    "ws://127.0.0.1:9219/api/ws-health",
    "wss://mini-mh.tailbd0650.ts.net/api/ws-health",
)
DEFAULT_SERVICES = ("ollama", "gateway", "pwa", "config", "telegram", "openbrain", "email", "estate", "disk", "memory")

# --- email triage daemon (ES-0005) -----------------------------------------
# `net.mtp44.email-triage` ran a cluster of 59 crash-restarts between
# 2026-08-10 and 08-27 — up to twenty in one day — absorbed silently by its
# launchd `KeepAlive` with nothing announcing it, and separately lost four
# digests to Signal timeouts. Neither was visible because this monitor had no
# email check at all. That is what these two assertions exist to catch.
#
# Read `logs/triage.log`, NOT `logs/daemon.log`: since `ES-0002` the latter is
# launchd's stderr sink and carries tracebacks only, so its mtime is the last
# *crash* and a stale `daemon.log` is health. Reading the wrong one is exactly
# the mistake `ES-0005` was written to correct.
DEFAULT_EMAIL_LOG = "/Users/mh/ai/email_solutions/logs/triage.log"
# Measured, not guessed: over 2026-08-10 → 09-01 (200 log lines) the largest
# real gap between consecutive lines was 24.0 h and the p95 was 11.3 h. 36 h is
# 1.5x the observed maximum — loose enough not to cry wolf on a quiet mailbox,
# tight enough to still mean something. The daemon polls every 300 s but only
# logs when it does something, so this is the "silently stopped working"
# tripwire, not a heartbeat. Re-measure before tightening it.
DEFAULT_EMAIL_STALE_SECONDS = 36 * 3600
# Normal is zero restarts a day. The fault being caught was 16-20.
DEFAULT_EMAIL_MAX_RESTARTS = 5
DEFAULT_EMAIL_RESTART_WINDOW_HOURS = 6
# Log lines are `2026-09-01 03:34:44 LEVEL message` in local time.
_EMAIL_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ")
_EMAIL_START_MARKER = "Starting email daemon"
_EMAIL_SIGNAL_FAILURE = "Signal send failed"
# Bounded tail read: the window of interest is hours, the file is capped at
# 5 MB by its own RotatingFileHandler, and this runs every 300 s.
_EMAIL_TAIL_BYTES = 256 * 1024


# --- estate silence (BS-0004 / WW-0004) ------------------------------------
# The `email` check below watches ONE job. This one watches the whole estate,
# and it exists because `launchctl list`'s status column is the last EXIT
# STATUS: a scheduled job that exits 0 while doing nothing reads as perfectly
# healthy. That is how `OB-0010`'s commitment extraction was dead from
# 2026-07-05 and the `email_solutions` daemon quiet from 2026-08-27 — both found
# only because somebody decided to audit, which `WW-0001` named as the estate's
# real cost: "nothing announces when a piece stops working".
#
# `BS-0004` wrote down, per job, which file proves it ran and what "silent"
# means for it, with the measurement behind every threshold. `bootstrap`'s
# `make standup` and its weekly reviewer read that registry — but only when
# invoked. This is the only thing that reads it every 300 s, which is why
# `WW-0004` amended `WW-0003`'s one-output-surface constraint to let it exist.
#
# IT IMPORTS `bootstrap/scripts/spine.py` ON PURPOSE, rather than copying the
# rule: two implementations of "is this job alive" is how an estate ends up with
# two answers. The coupling is declared in `WW-0004` and in `estate.yaml`'s note
# on this job — editing `spine.log_freshness` now changes what this live monitor
# alarms on. If bootstrap or PyYAML is unavailable the check SKIPS rather than
# failing, so a bare machine mid-rebuild does not page anyone.
DEFAULT_ESTATE_SPINE_DIR = "/Users/mh/ai/bootstrap/scripts"
DEFAULT_ESTATE_REVIEW_LATEST = "/Users/mh/state/bootstrap/review/latest.json"
# The weekly reviewer's own bar is 8d (estate.yaml). 9d here so a single late
# run does not race the job's own freshness check into a duplicate alarm.
DEFAULT_ESTATE_REVIEW_MAX_AGE_DAYS = 9
# Already covered, in more depth, by `_check_email` — restart-loop counting and
# Signal-failure reporting on top of freshness. Alarming twice on one job trains
# you to ignore the channel.
ESTATE_LABELS_COVERED_ELSEWHERE = ("net.mtp44.email-triage",)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_csv(value: str | None, *, default: tuple[str, ...]) -> list[str]:
    if not value:
        return list(default)
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> tuple[int, Any]:
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            parsed = _parse_http_payload(raw)
            return response.getcode(), parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        parsed = _parse_http_payload(raw)
        return exc.code, parsed


def _parse_http_payload(raw: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        return raw
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return raw
    if "data:" in stripped:
        data_lines = []
        for line in stripped.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if data_lines:
            joined = "\n".join(data_lines)
            try:
                return json.loads(joined)
            except json.JSONDecodeError:
                return raw
    return raw


def _parse_hosted_mcp_url() -> str | None:
    # Explicit override wins. Both spellings are honored: OPEN_BRAIN_MCP_URL is
    # this monitor's historical name; OPENBRAIN_MCP_URL is the spelling the
    # plugins/memory/openbrain surface documents for ~/.hermes/.env.
    for name in ("OPEN_BRAIN_MCP_URL", "OPENBRAIN_MCP_URL"):
        explicit = os.getenv(name, "").strip()
        if explicit:
            return explicit

    # Prefer the URL the gateway actually talks to (mcp_servers.open_brain.url).
    # Since the F5 cutover (2026-07-11) that is the local server; the
    # SUPABASE_URL-derived hosted front door is sealed (401 for every key), so
    # deriving the probe URL from SUPABASE_URL ahead of the gateway config made
    # this check report an outage the gateway never had. Keep the Supabase
    # derivation only as a last-resort legacy fallback.
    raw_config = read_raw_config()
    server = (((raw_config.get("mcp_servers") or {}).get("open_brain") or {}))
    url = str(server.get("url") or "").strip()
    if url:
        return url

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    if supabase_url:
        return f"{supabase_url.rstrip('/')}/functions/v1/open-brain-mcp"
    return None


def _parse_telegram_target(value: str) -> tuple[str, int | None]:
    if ":" not in value:
        return value, None
    chat_id, maybe_thread = value.split(":", 1)
    if maybe_thread.isdigit():
        return chat_id, int(maybe_thread)
    return value, None


@dataclass
class CheckResult:
    service: str
    ok: bool
    detail: str
    fingerprint: str
    metadata: dict[str, Any] | None = None
    skipped: bool = False


@dataclass
class RestartResult:
    ok: bool
    detail: str


class HealthMonitor:
    def __init__(
        self,
        *,
        state_path: Path,
        log_path: Path,
        checkers: dict[str, Callable[[], CheckResult]],
        restarters: dict[str, Callable[[], RestartResult]],
        alert_sender: Callable[[str], RestartResult] | None,
        restart_backoff_seconds: int,
        now_fn: Callable[[], datetime] = _utc_now,
        correlation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.state_path = state_path
        self.log_path = log_path
        self.checkers = checkers
        self.restarters = restarters
        self.alert_sender = alert_sender
        self.restart_backoff_seconds = restart_backoff_seconds
        self.now_fn = now_fn
        self.correlation_id_factory = correlation_id_factory or (lambda: uuid.uuid4().hex[:12])
        self.correlation_id = self.correlation_id_factory()
        self.state = _read_json_file(self.state_path)
        self.state.setdefault("services", {})
        self.state.setdefault("updated_at", _utc_now_iso())

    def _service_state(self, service: str) -> dict[str, Any]:
        services = self.state.setdefault("services", {})
        payload = services.setdefault(service, {})
        payload.setdefault("failure_count", 0)
        payload.setdefault("alert_sent_for", "")
        payload.setdefault("last_failure_fingerprint", "")
        payload.setdefault("next_restart_after", "")
        return payload

    def _log(self, *, service: str, status: str, action: str, detail: str, **extra: Any) -> None:
        payload = {
            "timestamp": self.now_fn().isoformat(),
            "correlation_id": self.correlation_id,
            "service": service,
            "status": status,
            "action": action,
            "detail": detail,
        }
        if extra:
            payload.update(extra)
        _append_jsonl(self.log_path, payload)

    def _restart_allowed(self, service_state: dict[str, Any], now: datetime) -> bool:
        raw = str(service_state.get("next_restart_after") or "").strip()
        if not raw:
            return True
        try:
            allowed_after = datetime.fromisoformat(raw)
        except ValueError:
            return True
        return now >= allowed_after

    def _record_restart_backoff(self, service_state: dict[str, Any], now: datetime) -> None:
        service_state["next_restart_after"] = (now + timedelta(seconds=self.restart_backoff_seconds)).isoformat()

    def _mark_healthy(self, result: CheckResult) -> None:
        service_state = self._service_state(result.service)
        service_state["failure_count"] = 0
        service_state["alert_sent_for"] = ""
        service_state["last_failure_fingerprint"] = ""
        service_state["next_restart_after"] = ""
        service_state["last_ok_at"] = self.now_fn().isoformat()
        self._log(service=result.service, status="healthy", action="none", detail=result.detail, **(result.metadata or {}))

    def _handle_failure(self, result: CheckResult) -> None:
        now = self.now_fn()
        service_state = self._service_state(result.service)
        previous_fingerprint = str(service_state.get("last_failure_fingerprint") or "")
        failure_count = int(service_state.get("failure_count") or 0)
        if previous_fingerprint != result.fingerprint:
            failure_count = 0
            service_state["alert_sent_for"] = ""
        failure_count += 1
        service_state["failure_count"] = failure_count
        service_state["last_failure_fingerprint"] = result.fingerprint
        service_state["last_failure_at"] = now.isoformat()

        restart = self.restarters.get(result.service)
        restart_attempted = False
        if restart and self._restart_allowed(service_state, now):
            restart_attempted = True
            restart_result = restart()
            self._record_restart_backoff(service_state, now)
            service_state["last_restart_at"] = now.isoformat()
            if restart_result.ok:
                self._log(
                    service=result.service,
                    status="unhealthy",
                    action="restart_requested",
                    detail=restart_result.detail,
                    failure_count=failure_count,
                    **(result.metadata or {}),
                )
            else:
                self._log(
                    service=result.service,
                    status="unhealthy",
                    action="restart_failed",
                    detail=restart_result.detail,
                    failure_count=failure_count,
                    **(result.metadata or {}),
                )
                self._maybe_alert(result, service_state, f"Restart failed for {result.service}: {restart_result.detail}")
                return
        else:
            self._log(
                service=result.service,
                status="unhealthy",
                action="backoff" if restart else "none",
                detail=result.detail,
                failure_count=failure_count,
                **(result.metadata or {}),
            )

        if failure_count >= 2:
            reason = result.detail if not restart_attempted else f"Persistent unhealthy state after restart: {result.detail}"
            self._maybe_alert(result, service_state, f"{result.service} health check failed: {reason}")

    def _maybe_alert(self, result: CheckResult, service_state: dict[str, Any], message: str) -> None:
        if not self.alert_sender:
            self._log(service=result.service, status="unhealthy", action="alert_skipped", detail="alert sender not configured")
            return
        if service_state.get("alert_sent_for") == result.fingerprint:
            self._log(service=result.service, status="unhealthy", action="alert_suppressed", detail="alert already sent")
            return
        alert_result = self.alert_sender(message)
        if alert_result.ok:
            service_state["alert_sent_for"] = result.fingerprint
            service_state["last_alert_at"] = self.now_fn().isoformat()
            self._log(service=result.service, status="unhealthy", action="alert_sent", detail=alert_result.detail)
        else:
            self._log(service=result.service, status="unhealthy", action="alert_failed", detail=alert_result.detail)

    def run(self, services: list[str]) -> int:
        unhealthy = False
        for service in services:
            checker = self.checkers.get(service)
            if checker is None:
                self._log(service=service, status="skipped", action="unknown_service", detail="service not configured")
                continue
            result = checker()
            if result.skipped:
                self._log(service=service, status="skipped", action="none", detail=result.detail, **(result.metadata or {}))
                continue
            if result.ok:
                self._mark_healthy(result)
                continue
            unhealthy = True
            self._handle_failure(result)

        self.state["updated_at"] = self.now_fn().isoformat()
        _write_json_file(self.state_path, self.state)
        return 1 if unhealthy else 0


def _restart_ollama() -> RestartResult:
    getuid = getattr(os, "getuid", None)
    if sys.platform != "darwin" or getuid is None:
        return RestartResult(False, "ollama launchctl restart is only supported on macOS")
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{getuid()}/com.mh.ollama"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        return RestartResult(False, str(exc))
    if result.returncode == 0:
        return RestartResult(True, "launchctl kickstart requested for com.mh.ollama")
    detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return RestartResult(False, detail)


def _restart_gateway() -> RestartResult:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "hermes_cli.main", "gateway", "restart"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as exc:
        return RestartResult(False, str(exc))
    if result.returncode == 0:
        return RestartResult(True, "hermes gateway restart completed")
    detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return RestartResult(False, detail)


def _send_telegram_alert(message: str) -> RestartResult:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    target = (
        os.getenv("HERMES_HEALTH_ALERT_CHAT_ID", "").strip()
        or os.getenv("TELEGRAM_HOME_CHANNEL", "").strip()
    )
    if not token and not target:
        return RestartResult(False, "missing TELEGRAM_BOT_TOKEN and alert chat target")
    if not token:
        return RestartResult(False, "missing TELEGRAM_BOT_TOKEN")
    if not target:
        return RestartResult(
            False,
            "missing alert chat target (set HERMES_HEALTH_ALERT_CHAT_ID or TELEGRAM_HOME_CHANNEL)",
        )

    chat_id, thread_id = _parse_telegram_target(target)
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": f"Hermes health monitor alert\n\n{message}",
    }
    if thread_id is not None:
        body["message_thread_id"] = thread_id

    try:
        status_code, payload = _http_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            method="POST",
            body=body,
            timeout=_parse_float_env("HERMES_HEALTH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS),
        )
    except Exception as exc:
        return RestartResult(False, str(exc))

    if status_code == 200 and isinstance(payload, dict) and payload.get("ok") is True:
        return RestartResult(True, "telegram alert delivered")
    return RestartResult(False, f"telegram alert failed with status {status_code}")


def _check_ollama() -> CheckResult:
    timeout = _parse_float_env("HERMES_HEALTH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        status_code, payload = _http_json("http://127.0.0.1:11434/api/tags", timeout=timeout)
    except Exception as exc:
        return CheckResult("ollama", False, str(exc), f"error:{type(exc).__name__}")
    if status_code != 200:
        return CheckResult("ollama", False, f"unexpected status {status_code}", f"http:{status_code}")
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return CheckResult("ollama", False, "missing models payload", "payload:missing-models")
    return CheckResult("ollama", True, f"ok ({len(models)} model(s))", "ok", {"model_count": len(models)})


def _check_gateway() -> CheckResult:
    pid = get_running_pid(cleanup_stale=False)
    if pid is None:
        return CheckResult("gateway", False, "gateway pid not running", "pid:missing")

    status_payload = read_runtime_status() or {}
    gateway_state = str(status_payload.get("gateway_state") or "unknown")
    exit_reason = str(status_payload.get("exit_reason") or "").strip()
    if gateway_state == "startup_failed":
        detail = exit_reason or "startup_failed"
        return CheckResult("gateway", False, detail, f"startup_failed:{detail}", {"gateway_state": gateway_state, "pid": pid})
    if gateway_state == "stopped":
        detail = exit_reason or "gateway stopped"
        return CheckResult("gateway", False, detail, f"stopped:{detail}", {"gateway_state": gateway_state, "pid": pid})

    return CheckResult("gateway", True, f"{gateway_state} (pid {pid})", "ok", {"gateway_state": gateway_state, "pid": pid})


def _check_websocket(url: str, *, timeout: float) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError(f"invalid WebSocket URL: {url}")
    secure = parsed.scheme == "wss"
    port = parsed.port or (443 if secure else 80)
    host_header = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    request_path = parsed.path or "/"
    if parsed.query:
        request_path = f"{request_path}?{parsed.query}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1(f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode("ascii")).digest()
    ).decode("ascii")
    raw_socket = socket.create_connection((parsed.hostname, port), timeout=timeout)
    connection: socket.socket | ssl.SSLSocket = raw_socket
    try:
        if secure:
            connection = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=parsed.hostname)
        origin_scheme = "https" if secure else "http"
        request = (
            f"GET {request_path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            f"Origin: {origin_scheme}://{host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16_384:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response += chunk
        header_text = response.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        lines = header_text.splitlines()
        if not lines or " 101 " not in f" {lines[0]} ":
            raise RuntimeError(lines[0] if lines else "empty WebSocket response")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != expected_accept:
            raise RuntimeError("invalid Sec-WebSocket-Accept")
    finally:
        connection.close()
    return "HTTP 101"


def _check_pwa(
    *,
    release_root: Path = DEFAULT_PWA_RELEASE_ROOT,
    status_urls: tuple[str, ...] = DEFAULT_PWA_STATUS_URLS,
    websocket_urls: tuple[str, ...] = DEFAULT_PWA_WS_URLS,
) -> CheckResult:
    current = release_root / "current"
    if not current.is_symlink():
        return CheckResult("pwa", False, f"missing atomic current link at {current}", "release:current-missing")

    try:
        target = os.readlink(current)
        stamp_path = release_root / target / "pwa-release.json"
        metadata = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            "pwa",
            False,
            f"could not read served build stamp: {type(exc).__name__}: {exc}",
            f"release:stamp:{type(exc).__name__}",
        )

    release_id = str(metadata.get("release_id") or "")
    commit = str(metadata.get("commit") or "")
    if not release_id or not commit or Path(target).name != release_id:
        return CheckResult("pwa", False, "current release and build stamp do not agree", "release:stamp-mismatch")

    if sys.platform == "darwin":
        getuid = getattr(os, "getuid", None)
        if getuid is None:
            return CheckResult("pwa", False, "cannot determine launchd user domain", "launchd:no-uid")
        launchd = subprocess.run(
            ["launchctl", "print", f"gui/{getuid()}/com.mh.hermes-pwa"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if launchd.returncode != 0:
            return CheckResult("pwa", False, "com.mh.hermes-pwa is not loaded", "launchd:not-loaded")

    timeout = _parse_float_env("HERMES_HEALTH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
    for url in status_urls:
        try:
            status_code, payload = _http_json(url, timeout=timeout)
        except Exception as exc:
            return CheckResult(
                "pwa",
                False,
                f"{url} failed: {type(exc).__name__}: {exc}",
                f"http:{url}:{type(exc).__name__}",
                {"release_id": release_id, "commit": commit},
            )
        if status_code != 200 or not isinstance(payload, dict) or not payload.get("version"):
            return CheckResult(
                "pwa",
                False,
                f"{url} returned an invalid status response",
                f"http:{url}:{status_code}",
                {"release_id": release_id, "commit": commit},
            )

    for url in websocket_urls:
        try:
            _check_websocket(url, timeout=timeout)
        except Exception as exc:
            return CheckResult(
                "pwa",
                False,
                f"{url} failed: {type(exc).__name__}: {exc}",
                f"websocket:{url}:{type(exc).__name__}",
                {"release_id": release_id, "commit": commit},
            )

    return CheckResult(
        "pwa",
        True,
        f"ok ({release_id}, loopback + tailnet HTTP/WS)",
        "ok",
        {
            "release_id": release_id,
            "commit": commit,
            "status_urls": list(status_urls),
            "websocket_urls": list(websocket_urls),
        },
    )


def _check_telegram() -> CheckResult:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return CheckResult("telegram", True, "telegram not configured", "skip", skipped=True)
    timeout = _parse_float_env("HERMES_HEALTH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        status_code, payload = _http_json(f"https://api.telegram.org/bot{token}/getMe", timeout=timeout)
    except Exception as exc:
        return CheckResult("telegram", False, str(exc), f"error:{type(exc).__name__}")
    if status_code != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
        return CheckResult("telegram", False, f"unexpected status {status_code}", f"http:{status_code}")
    result = payload.get("result") or {}
    username = str(result.get("username") or "unknown")
    return CheckResult("telegram", True, f"ok ({username})", "ok")


# How often the *full* auth + tool-contract probe runs. Every 300 s run does
# the cheap `/health` check instead. Task B of `OB-0012`: the old shape sent an
# unauthenticated `tools/list` (expecting 401) plus an authenticated one on
# every cycle — 576 tool-discovery requests a day, and 12,185 of them in the
# 2026-08-31 audit window, to learn that a daemon was up.
OPENBRAIN_CONTRACT_INTERVAL_SECONDS = 24 * 60 * 60
OPENBRAIN_CONTRACT_STATE_PATH = Path.home() / ".hermes" / "openbrain-contract-state.json"


def _openbrain_health_url(url: str) -> str:
    return f"{url.rstrip('/')}/health"


def _openbrain_contract_due(state: dict[str, Any], version: str, now: float) -> tuple[bool, str]:
    """Daily, after a deploy, or after the contract last failed.

    "After deploys" is read off the server version `/health` returns, so a
    restart carrying new code is probed immediately rather than up to a day
    later — the plan asks for daily *and* post-deploy, and a version change is
    the only post-deploy signal available without a hook.
    """
    if not state.get("last_ok_at"):
        return True, "first-run"
    if state.get("last_status") != "ok":
        return True, "retry-after-failure"
    if version and state.get("version") != version:
        return True, "version-changed"
    if now - float(state.get("last_ok_at") or 0) >= OPENBRAIN_CONTRACT_INTERVAL_SECONDS:
        return True, "daily"
    return False, ""


def _check_openbrain_contract(url: str, key: str, timeout: float) -> CheckResult:
    """The expensive probe, now run daily instead of every 300 s.

    Deliberately keeps **both** halves of the old check. The unauthenticated
    request is not redundant with the authenticated one: it is the only thing
    asserting that the auth gate is actually closed, and dropping it to save a
    request would quietly retire a security check under the banner of noise
    reduction.
    """
    request_body = {"jsonrpc": "2.0", "id": "health-monitor", "method": "tools/list"}
    try:
        unauth_status, unauth_payload = _http_json(url, method="POST", body=request_body, timeout=timeout)
    except Exception as exc:
        return CheckResult("openbrain", False, str(exc), f"unauth:{type(exc).__name__}")
    if unauth_status != 401:
        return CheckResult("openbrain", False, f"expected 401 for unauthenticated probe, got {unauth_status}", f"unauth:http:{unauth_status}")
    if not isinstance(unauth_payload, dict) or unauth_payload.get("error") != "Unauthorized":
        return CheckResult("openbrain", False, "unexpected unauthenticated response body", "unauth:body")

    try:
        auth_status, auth_payload = _http_json(
            url,
            method="POST",
            headers={"x-brain-key": key, "x-brain-client": "hermes-health-monitor"},
            body=request_body,
            timeout=timeout,
        )
    except Exception as exc:
        return CheckResult("openbrain", False, str(exc), f"auth:{type(exc).__name__}")
    if auth_status != 200:
        return CheckResult("openbrain", False, f"authenticated probe returned {auth_status}", f"auth:http:{auth_status}")
    result = auth_payload.get("result") if isinstance(auth_payload, dict) else None
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        return CheckResult("openbrain", False, "authenticated probe missing tools payload", "auth:tools")
    return CheckResult("openbrain", True, f"contract ok ({len(tools)} tool(s))", "ok", {"tool_count": len(tools)})


def _check_openbrain() -> CheckResult:
    """Cheap every cycle, full contract daily and after a deploy.

    The cheap probe is one authenticated `GET /health`, which since 2026-08-31
    answers all three liveness questions at once: the process is serving, the
    key is accepted, and Postgres — the system of record — is reachable (the
    route returns 503, not a flagged 200, when it is not).

    Detection is not weakened by this, which is the thing to check before
    believing the request-count win. A dead or wedged server still fails the
    very next 300 s cycle and alerts through the same `_maybe_alert` path; a
    broken tool contract or an open auth gate is caught within a day, or
    immediately if the server version moved. WW-0001's finding was that all
    three of August's faults were found by audit rather than by an alert —
    so the rule here is that every failure mode the old check could see is
    still seen by one of these two, just at a cadence matched to how fast it
    can appear.
    """
    url = _parse_hosted_mcp_url()
    key = os.getenv("MCP_ACCESS_KEY", "").strip()
    if not url or not key:
        return CheckResult("openbrain", True, "openbrain not configured", "skip", skipped=True)

    timeout = _parse_float_env("HERMES_HEALTH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        status, payload = _http_json(
            _openbrain_health_url(url),
            headers={"x-brain-key": key, "x-brain-client": "hermes-health-monitor"},
            timeout=timeout,
        )
    except Exception as exc:
        return CheckResult("openbrain", False, str(exc), f"health:{type(exc).__name__}")
    if status != 200:
        detail = ""
        if isinstance(payload, dict):
            detail = f" ({payload.get('status')}/{payload.get('database')})"
        return CheckResult("openbrain", False, f"/health returned {status}{detail}", f"health:http:{status}")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return CheckResult("openbrain", False, "/health did not report ok", "health:body")
    if payload.get("database") not in (None, "ok"):
        # `None` keeps this compatible with a server predating the DB probe.
        return CheckResult("openbrain", False, f"database {payload.get('database')}", "health:db")

    version = str(payload.get("version") or "")
    state = _read_json_file(OPENBRAIN_CONTRACT_STATE_PATH)
    now = time.time()
    due, reason = _openbrain_contract_due(state, version, now)
    if not due:
        return CheckResult("openbrain", True, f"ok (health, v{version or '?'})", "ok",
                           {"probe": "health", "version": version})

    contract = _check_openbrain_contract(url, key, timeout)
    state["last_status"] = "ok" if contract.ok else "failed"
    state["version"] = version
    state["last_run_reason"] = reason
    if contract.ok:
        state["last_ok_at"] = now
    _write_json_file(OPENBRAIN_CONTRACT_STATE_PATH, state)
    if contract.ok:
        metadata = dict(contract.metadata or {})
        metadata.update({"probe": "contract", "reason": reason, "version": version})
        return CheckResult("openbrain", True, f"{contract.detail} [{reason}]", "ok", metadata)
    return contract


def _check_config() -> CheckResult:
    """Verify ~/.hermes/config.yaml still parses.

    A syntax error here (e.g. a bad list indent) makes ``load_gateway_config()``
    silently fall back to defaults — every user override (auxiliary providers,
    fallback chain, model settings) is dropped with no visible symptom besides
    a WARNING that scrolls off in the logs. That happened for ~12 hours
    undetected on 2026-07-01 before surfacing as opaque "model provider failed"
    replies in Telegram. This check makes that failure mode loud instead of
    silent.
    """
    from hermes_cli.config import get_config_path

    config_path = get_config_path()
    if not config_path.exists():
        return CheckResult("config", True, "config.yaml not present (defaults only)", "skip", skipped=True)
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        return CheckResult(
            "config",
            False,
            f"{config_path} failed to parse ({type(exc).__name__}): {exc}. "
            f"Every user override (auxiliary providers, fallback chain, model settings) "
            f"is being silently ignored until this is fixed.",
            f"parse:{type(exc).__name__}",
        )
    if not isinstance(data, dict):
        return CheckResult(
            "config",
            False,
            f"{config_path} did not parse to a mapping (got {type(data).__name__})",
            "parse:not-a-mapping",
        )
    return CheckResult("config", True, "ok", "ok")


def _check_disk() -> CheckResult:
    minimum_bytes = _parse_int_env("HERMES_HEALTH_MIN_DISK_BYTES", DEFAULT_MIN_DISK_BYTES)
    usage = shutil.disk_usage(Path.home())
    free_bytes = int(usage.free)
    if free_bytes < minimum_bytes:
        return CheckResult("disk", False, f"free disk {free_bytes} below threshold {minimum_bytes}", "disk:low", {"free_bytes": free_bytes})
    return CheckResult("disk", True, f"ok ({free_bytes} bytes free)", "ok", {"free_bytes": free_bytes})


def _available_memory_bytes() -> int | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return _available_memory_bytes_macos()
    try:
        return int(page_size) * int(available_pages)
    except (TypeError, ValueError):
        return _available_memory_bytes_macos()


def _available_memory_bytes_macos() -> int | None:
    try:
        result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    page_size = None
    free_pages = 0
    wanted_keys = {"Pages free", "Pages inactive", "Pages speculative"}
    for line in result.stdout.splitlines():
        if "page size of" in line:
            try:
                page_size = int(line.split("page size of", 1)[1].split("bytes", 1)[0].strip())
            except (IndexError, ValueError):
                page_size = None
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key not in wanted_keys:
            continue
        raw_value = value.strip().rstrip(".")
        try:
            free_pages += int(raw_value)
        except ValueError:
            continue
    if page_size is None or free_pages <= 0:
        return None
    return page_size * free_pages


def _read_log_tail(path: Path, max_bytes: int) -> str:
    with path.open("rb") as fh:
        try:
            fh.seek(max(0, path.stat().st_size - max_bytes))
        except OSError:
            pass
        # A mid-line seek can split a UTF-8 sequence; the first partial line is
        # discarded by the timestamp match anyway.
        return fh.read().decode("utf-8", errors="replace")


def count_email_events(text: str, now: datetime, window_hours: float) -> tuple[int, int]:
    """`(daemon starts, Signal send failures)` inside the trailing window.

    Pure so the thresholds can be tested without a live log. `now` and the log's
    timestamps are both **local naive** — the daemon writes local time with no
    offset, and comparing that to a UTC clock would silently shift the window.
    """
    cutoff = now - timedelta(hours=window_hours)
    starts = 0
    signal_failures = 0
    for line in text.splitlines():
        match = _EMAIL_TS_RE.match(line)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if stamp < cutoff:
            continue
        if _EMAIL_START_MARKER in line:
            starts += 1
        elif _EMAIL_SIGNAL_FAILURE in line:
            signal_failures += 1
    return starts, signal_failures


def _check_email() -> CheckResult:
    """Two assertions: the daemon is still doing work, and it is not crash-looping.

    Deliberately has **no restarter** in `_build_monitor`. launchd's `KeepAlive`
    already restarts this daemon; restarting it again on a crash-loop alarm would
    add to the very thing being alarmed about. The correct response to this
    firing is a human reading `logs/triage.log`.
    """
    log_path = Path(os.getenv("HERMES_HEALTH_EMAIL_LOG", DEFAULT_EMAIL_LOG))
    if not log_path.exists():
        return CheckResult("email", True, f"{log_path} not present (email triage not installed)", "skip", skipped=True)

    stale_seconds = _parse_float_env("HERMES_HEALTH_EMAIL_STALE_SECONDS", DEFAULT_EMAIL_STALE_SECONDS)
    max_restarts = _parse_int_env("HERMES_HEALTH_EMAIL_MAX_RESTARTS", DEFAULT_EMAIL_MAX_RESTARTS)
    window_hours = _parse_float_env("HERMES_HEALTH_EMAIL_RESTART_WINDOW_HOURS", DEFAULT_EMAIL_RESTART_WINDOW_HOURS)

    try:
        age_seconds = time.time() - log_path.stat().st_mtime
        text = _read_log_tail(log_path, _EMAIL_TAIL_BYTES)
    except OSError as exc:
        return CheckResult("email", False, f"cannot read {log_path}: {exc}", f"email:{type(exc).__name__}")

    age_hours = round(age_seconds / 3600, 1)
    if age_seconds > stale_seconds:
        return CheckResult(
            "email",
            False,
            f"triage.log has not been written for {age_hours} h "
            f"(threshold {round(stale_seconds / 3600, 1)} h) — the daemon may be up but doing nothing",
            "email:stale",
            {"log_age_hours": age_hours},
        )

    starts, signal_failures = count_email_events(text, datetime.now(), window_hours)
    metadata = {
        "log_age_hours": age_hours,
        "restarts_in_window": starts,
        "signal_failures_in_window": signal_failures,
    }
    if starts >= max_restarts:
        return CheckResult(
            "email",
            False,
            f"{starts} daemon restarts in {window_hours} h (threshold {max_restarts}) — "
            f"crash-looping under launchd KeepAlive; read logs/triage.log, do not restart it",
            "email:restart-loop",
            metadata,
        )
    # Signal timeouts are reported, never alarmed on: they are transient, the
    # digest is not safety-critical, and a check nobody trusts gets muted.
    return CheckResult("email", True, f"ok (last log {age_hours} h ago, {starts} restart(s) in {window_hours} h)", "ok", metadata)


def _load_estate_spine(spine_dir: str) -> tuple[Any, Any] | None:
    """(Estate, log_freshness) from bootstrap, or None when unavailable.

    Deliberately tolerant: a bare machine part-way through `make bootstrap` has
    no `~/ai/bootstrap` yet, and the monitor must keep watching the nine things
    it can see rather than dying on the tenth.
    """
    if spine_dir not in sys.path:
        sys.path.append(spine_dir)
    try:
        from spine import Estate, log_freshness  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — missing repo, missing PyYAML, anything
        return None
    return Estate, log_freshness


def find_silent_jobs(estate: Any, log_freshness: Any, now: datetime) -> list[dict[str, Any]]:
    """Registry jobs whose evidence log has gone past its measured `stale_after`.

    Pure given an Estate, so the thresholds are testable without a live registry.
    Only entries with a non-null `stale_after` can be judged — eleven of
    seventeen are null on purpose, because for a daemon whose log is
    request-driven a quiet log means no clients rather than a fault
    (`gateway.log` sat 17.6 days quiet while perfectly healthy). Rendering those
    as an alarm would page Mark for not using his own system.
    """
    out: list[dict[str, Any]] = []
    for svc in estate.all_services():
        label = str(svc.get("label"))
        if label in ESTATE_LABELS_COVERED_ELSEWHERE:
            continue
        evidence = svc.get("evidence") or {}
        if evidence.get("stale_after") is None:
            continue
        freshness = log_freshness(evidence, now)
        if freshness.get("exists") is False:
            out.append({"label": label, "why": "its evidence log has never been written",
                        "bar": evidence["stale_after"], "age_hours": None})
        elif freshness.get("stale"):
            out.append({"label": label, "why": "silent", "bar": evidence["stale_after"],
                        "age_hours": freshness.get("age_hours")})
    return out


def read_review_latest(path: Path, now: datetime, max_age_days: float) -> dict[str, Any]:
    """The weekly reviewer's own pointer: did the last run deliver, and how old?

    Two faults live here and neither is "a report is waiting". An OPEN pull
    request is reported and never alarmed on: a reminder delivered down a health
    channel is how a health channel stops being read, and it would leave this
    service unhealthy — and therefore the monitor exiting 1 — until Mark got
    round to it. `make standup` is where a nudge belongs.
    """
    if not path.is_file():
        return {"present": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": True, "unreadable": str(exc)}
    out: dict[str, Any] = {"present": True, "date": data.get("date"),
                           "findings": data.get("findings"), "pr_url": data.get("pr_url"),
                           "pr_error": data.get("pr_error")}
    stamp = str(data.get("generated") or "")
    try:
        generated = datetime.fromisoformat(stamp)
    except ValueError:
        out["age_days"] = None
        return out
    if generated.tzinfo is not None:
        generated = generated.astimezone().replace(tzinfo=None)
    out["age_days"] = round((now - generated).total_seconds() / 86400, 1)
    out["overdue"] = out["age_days"] > max_age_days
    return out


def _check_estate() -> CheckResult:
    """Has anything in the estate gone quiet, and is the weekly reviewer alive?

    Deliberately has **no restarter** in `_build_monitor`, like `_check_email`.
    There is no generic safe restart for "some job stopped doing work", and
    kicking a job whose evidence is stale is as likely to hide the cause as fix
    it. The correct response to this firing is a human running
    `cd /Users/mh/ai/bootstrap && make review-gather`.
    """
    spine_dir = os.getenv("HERMES_HEALTH_ESTATE_SPINE_DIR", DEFAULT_ESTATE_SPINE_DIR)
    loaded = _load_estate_spine(spine_dir)
    if loaded is None:
        return CheckResult("estate", True, f"{spine_dir} not importable (estate spine not installed)",
                           "skip", skipped=True)
    Estate, log_freshness = loaded
    try:
        estate = Estate.load()
    except Exception as exc:  # noqa: BLE001 — an unreadable registry is the finding
        return CheckResult("estate", False, f"cannot read the estate registry: {exc}",
                           f"estate:registry:{type(exc).__name__}")

    now = datetime.now()
    silent = find_silent_jobs(estate, log_freshness, now)
    review = read_review_latest(
        Path(os.getenv("HERMES_HEALTH_ESTATE_REVIEW_LATEST", DEFAULT_ESTATE_REVIEW_LATEST)),
        now,
        _parse_float_env("HERMES_HEALTH_ESTATE_REVIEW_MAX_AGE_DAYS", DEFAULT_ESTATE_REVIEW_MAX_AGE_DAYS),
    )
    metadata: dict[str, Any] = {
        "silent_jobs": [s["label"] for s in silent],
        "review_date": review.get("date"),
        "review_age_days": review.get("age_days"),
        "review_findings": review.get("findings"),
    }

    if silent:
        worst = ", ".join(
            f"{s['label']} ({s['why']}"
            + (f", {s['age_hours']}h against a {s['bar']} bar" if s["age_hours"] is not None else "")
            + ")"
            for s in silent
        )
        return CheckResult(
            "estate", False,
            f"{len(silent)} job(s) exiting 0 but leaving no evidence of work: {worst}. "
            f"Exit status says nothing here — run `make review-gather` in ~/ai/bootstrap",
            "estate:silent:" + ",".join(sorted(s["label"] for s in silent)),
            metadata,
        )

    if review.get("unreadable"):
        return CheckResult("estate", False,
                           f"the estate review pointer is unreadable: {review['unreadable']}",
                           "estate:review:unreadable", metadata)
    if review.get("pr_error"):
        return CheckResult(
            "estate", False,
            f"the estate review of {review.get('date')} gathered {review.get('findings')} "
            f"finding(s) but could not deliver them: {review['pr_error']}",
            f"estate:review:undelivered:{review.get('date')}", metadata,
        )
    if review.get("overdue"):
        return CheckResult(
            "estate", False,
            f"the estate review has not run for {review.get('age_days')} days "
            f"(last {review.get('date')}) — com.mh.estate-review is scheduled weekly",
            f"estate:review:stale:{review.get('date')}", metadata,
        )

    if not review.get("present"):
        detail = "every evidenced job fresh; the weekly review has not run yet"
    else:
        waiting = f", review of {review.get('date')} delivered, {review.get('findings')} finding(s)"             if review.get("pr_url") else ""
        # "delivered", not "open": whether Mark has merged it is a `gh` call
        # this check deliberately does not make every 300 s — standup owns that.
        detail = f"every evidenced job fresh{waiting}"
    return CheckResult("estate", True, f"ok ({detail})", "ok", metadata)


def _check_memory() -> CheckResult:
    minimum_bytes = _parse_int_env("HERMES_HEALTH_MIN_MEMORY_BYTES", DEFAULT_MIN_MEMORY_BYTES)
    available_bytes = _available_memory_bytes()
    if available_bytes is None:
        return CheckResult("memory", True, "memory probe unavailable on this platform", "skip", skipped=True)
    if available_bytes < minimum_bytes:
        return CheckResult("memory", False, f"available memory {available_bytes} below threshold {minimum_bytes}", "memory:low", {"available_bytes": available_bytes})
    return CheckResult("memory", True, f"ok ({available_bytes} bytes available)", "ok", {"available_bytes": available_bytes})


def _build_monitor(*, state_path: Path, log_path: Path) -> HealthMonitor:
    return HealthMonitor(
        state_path=state_path,
        log_path=log_path,
        checkers={
            "ollama": _check_ollama,
            "gateway": _check_gateway,
            "pwa": _check_pwa,
            "config": _check_config,
            "telegram": _check_telegram,
            "openbrain": _check_openbrain,
            "email": _check_email,
            "estate": _check_estate,
            "disk": _check_disk,
            "memory": _check_memory,
        },
        restarters={
            "ollama": _restart_ollama,
            "gateway": _restart_gateway,
        },
        alert_sender=_send_telegram_alert if not _parse_bool_env("HERMES_HEALTH_DISABLE_ALERTS") else None,
        restart_backoff_seconds=_parse_int_env("HERMES_HEALTH_RESTART_BACKOFF_SECONDS", DEFAULT_RESTART_BACKOFF_SECONDS),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes local health monitor.")
    parser.add_argument(
        "--services",
        default=",".join(DEFAULT_SERVICES),
        help="Comma-separated services to check.",
    )
    parser.add_argument(
        "--state-path",
        default=str(Path.home() / ".hermes" / "health-monitor-state.json"),
        help="Path for persisted monitor state.",
    )
    parser.add_argument(
        "--log-path",
        default=str(Path.home() / ".hermes" / "logs" / "health-monitor.jsonl"),
        help="Path for structured JSONL monitor logs.",
    )
    args = parser.parse_args(argv)

    load_hermes_dotenv(project_env=PROJECT_DIR / ".env")
    monitor = _build_monitor(state_path=Path(args.state_path), log_path=Path(args.log_path))
    return monitor.run(_parse_csv(args.services, default=DEFAULT_SERVICES))


if __name__ == "__main__":
    raise SystemExit(main())
