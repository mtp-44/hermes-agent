#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
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
DEFAULT_SERVICES = ("ollama", "gateway", "telegram", "openbrain", "disk", "memory")


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
    explicit = os.getenv("OPEN_BRAIN_MCP_URL", "").strip()
    if explicit:
        return explicit

    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    if supabase_url:
        return f"{supabase_url.rstrip('/')}/functions/v1/open-brain-mcp"

    raw_config = read_raw_config()
    server = (((raw_config.get("mcp_servers") or {}).get("open_brain") or {}))
    url = str(server.get("url") or "").strip()
    return url or None


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
    try:
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.mh.ollama"],
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
    if not token or not target:
        return RestartResult(False, "missing TELEGRAM_BOT_TOKEN or alert chat target")

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


def _check_openbrain() -> CheckResult:
    url = _parse_hosted_mcp_url()
    key = os.getenv("MCP_ACCESS_KEY", "").strip()
    if not url or not key:
        return CheckResult("openbrain", True, "openbrain not configured", "skip", skipped=True)

    timeout = _parse_float_env("HERMES_HEALTH_HTTP_TIMEOUT_SECONDS", DEFAULT_HTTP_TIMEOUT_SECONDS)
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
            headers={"x-brain-key": key},
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
    return CheckResult("openbrain", True, f"ok ({len(tools)} tool(s))", "ok", {"tool_count": len(tools)})


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
            "telegram": _check_telegram,
            "openbrain": _check_openbrain,
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
