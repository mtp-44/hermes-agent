#!/usr/bin/env python3
"""Pre/post update guard for the local Hermes production deployment.

This script is intentionally boring and agent-friendly: Codex, Claude, or a
human can run it before and after a Hermes update and get a clear pass/fail
summary without exposing secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_PROD_BRANCH = "main"
EXPECTED_OPENBRAIN_TOOL_FLOOR = 30
HEALTH_FRESHNESS_SECONDS = 15 * 60
_REEXEC_ENV = "HERMES_UPDATE_GUARD_NO_REEXEC"


def _maybe_reexec_into_repo_venv() -> None:
    """Make direct execution use Hermes' project venv when it is available."""
    if os.getenv(_REEXEC_ENV):
        return
    venv_python = REPO_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    try:
        active_prefix = Path(sys.prefix).resolve()
        target_prefix = (REPO_ROOT / ".venv").resolve()
    except OSError:
        return
    if active_prefix == target_prefix:
        return
    env = dict(os.environ)
    env[_REEXEC_ENV] = "1"
    os.execve(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]], env)


_maybe_reexec_into_repo_venv()

LOCAL_DELTA_PATHS = (
    "agent/session_capture.py",
    "plugins/memory/openbrain/__init__.py",
    "plugins/openbrain-query-brain-format/__init__.py",
    "gateway/open_brain.py",
    "gateway/open_brain_feedback.py",
    "gateway/platforms/telegram.py",
    "gateway/run.py",
    "gateway/session.py",
    "gateway/slash_commands.py",
    "hermes_cli/commands.py",
    "scripts/hermes_health_monitor.py",
    "launchd/com.mh.hermes-dashboard.plist",
    "launchd/com.mh.hermes-health-monitor.plist",
    "launchd/com.mh.ollama.plist",
)

# Each entry is (relative_path, required). ``required`` is either a single
# substring or a tuple of substrings that must *all* be present in the file.
# These are "must contain substring" assertions, not symbol-resolution checks:
# they exist to catch upstream replays that drop a local Hermes/Open Brain
# delta. Asserting a wiring call (not just the symbol it references) is the
# point — see the query-feedback entry below.
LOCAL_DELTA_PATTERNS: tuple[tuple[str, str | tuple[str, ...]], ...] = (
    ("agent/session_capture.py", "SessionCaptureContext"),
    ("plugins/memory/openbrain/__init__.py", "OpenBrainMemoryProvider"),
    # The 👍/👎 query-feedback buttons only fire when the producer hook is
    # actually wired. A 2026-06 upstream replay (1fc7e29a4) kept these symbols
    # *defined* but dropped the register_hook("post_tool_call", ...) call, so
    # the feature went dead while a symbol-presence check still passed. Assert
    # both the hook registration and the candidate-capture call, not just that
    # the formatter symbol exists.
    (
        "plugins/openbrain-query-brain-format/__init__.py",
        (
            "mcp_open_brain_query_brain",
            'register_hook("post_tool_call"',
            "capture_query_brain_feedback_candidate(",
        ),
    ),
    ("gateway/open_brain.py", "record_query_feedback"),
    ("gateway/open_brain_feedback.py", "capture_query_brain_feedback_candidate"),
    ("gateway/platforms/telegram.py", "record_query_feedback"),
    # Consumer side of the same feature: the gateway must still pop the
    # feedback candidate that the producer hook captured.
    ("gateway/run.py", "pop_feedback_candidate"),
    ("scripts/hermes_health_monitor.py", "_check_openbrain"),
)

LAUNCH_AGENT_LABELS = (
    "ai.hermes.gateway",
    "com.mh.hermes-dashboard",
    "com.mh.hermes-health-monitor",
)

STALE_BRANCH_PREFIXES = (
    "sync/",
    "claude/",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    severity: str = "blocker"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"pass", "warn", "skip"}

    @property
    def blocks(self) -> bool:
        return self.status == "fail" and self.severity == "blocker"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _git(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], timeout=timeout)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:
        return _load_config_light(path, reason=f"PyYAML unavailable: {exc}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"failed to load {path}: {exc}") from exc
    return raw if isinstance(raw, dict) else {}


def _load_config_light(path: Path, *, reason: str) -> dict[str, Any]:
    """Small fallback parser for the config fields this guard checks.

    This is not a general YAML parser. It exists so ``hermes_update_guard.py``
    can still run from a plain system Python when PyYAML is unavailable.
    """
    text = _read_text(path)
    if not text:
        raise RuntimeError(f"failed to load {path}: empty or unreadable config; {reason}")

    config: dict[str, Any] = {
        "memory": {},
        "mcp_servers": {"open_brain": {"headers": {}}},
        "plugins": {"enabled": []},
    }
    section = ""
    in_open_brain = False
    in_headers = False
    in_plugins_enabled = False

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        if indent == 0 and line.endswith(":"):
            section = line[:-1]
            in_open_brain = False
            in_headers = False
            in_plugins_enabled = False
            continue

        if section == "memory" and indent >= 2 and line.startswith("provider:"):
            config["memory"]["provider"] = line.split(":", 1)[1].strip().strip("'\"")
            continue

        if section == "mcp_servers":
            if indent == 2 and line == "open_brain:":
                in_open_brain = True
                in_headers = False
                continue
            if indent == 2 and line.endswith(":"):
                in_open_brain = False
                in_headers = False
                continue
            if in_open_brain and indent == 4 and line.startswith("url:"):
                config["mcp_servers"]["open_brain"]["url"] = line.split(":", 1)[1].strip().strip("'\"")
                continue
            if in_open_brain and indent == 4 and line == "headers:":
                in_headers = True
                continue
            if in_open_brain and in_headers and indent >= 6 and ":" in line:
                key, value = line.split(":", 1)
                config["mcp_servers"]["open_brain"]["headers"][key.strip()] = value.strip().strip("'\"")
                continue

        if section == "plugins":
            if indent == 2 and line == "enabled:":
                in_plugins_enabled = True
                continue
            if in_plugins_enabled and indent >= 2 and line.startswith("- "):
                config["plugins"]["enabled"].append(line[2:].strip().strip("'\""))

    return config


def _expand_env(value: str, env: dict[str, str]) -> str:
    expanded = value
    for key, env_value in env.items():
        expanded = expanded.replace("${" + key + "}", env_value)
        expanded = expanded.replace("$" + key, env_value)
    return os.path.expandvars(expanded)


def _redacted_present(value: str) -> str:
    return "<set>" if value else "<missing>"


def _jsonrpc_text_payload(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        outer = json.loads(stripped)
    else:
        data_lines = []
        for line in stripped.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    data_lines.append(payload)
        outer = json.loads(data_lines[-1]) if data_lines else {}
    if not isinstance(outer, dict):
        return {}
    return outer


class HermesUpdateGuard:
    def __init__(
        self,
        *,
        hermes_home: Path,
        prod_branch: str,
        allow_dirty: bool,
        live_smoke: bool,
    ) -> None:
        self.hermes_home = hermes_home
        self.prod_branch = prod_branch
        self.allow_dirty = allow_dirty
        self.live_smoke = live_smoke
        self.checks: list[Check] = []

    def add(self, name: str, status: str, detail: str, *, severity: str = "blocker", **metadata: Any) -> None:
        self.checks.append(Check(name=name, status=status, detail=detail, severity=severity, metadata=metadata))

    def run(self, phase: str) -> dict[str, Any]:
        self.checks = []
        if phase in {"pre", "full"}:
            self._pre_checks()
        if phase in {"post", "full"}:
            self._post_checks()

        blocking_failures = [check for check in self.checks if check.blocks]
        warnings = [check for check in self.checks if check.status == "warn"]
        report = {
            "status": "fail" if blocking_failures else "pass",
            "phase": phase,
            "repo_root": str(REPO_ROOT),
            "hermes_home": str(self.hermes_home),
            "generated_at": _utc_now().isoformat(),
            "blocking_failures": len(blocking_failures),
            "warnings": len(warnings),
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "severity": check.severity,
                    "detail": check.detail,
                    **({"metadata": check.metadata} if check.metadata else {}),
                }
                for check in self.checks
            ],
        }
        return report

    def _pre_checks(self) -> None:
        self._check_repo_identity()
        self._check_git_state()
        self._check_local_delta_surface()
        self._check_config()
        self._check_ssl_guard_inputs()
        self._check_launch_agents()

    def _post_checks(self) -> None:
        self._check_repo_identity()
        self._check_runtime_gateway()
        self._check_health_monitor()
        self._check_config()
        self._check_launch_agents()
        if self.live_smoke:
            self._check_openbrain_live()
        else:
            self.add(
                "openbrain-live-smoke",
                "skip",
                "live MCP probe skipped; pass --live-smoke after network access is available",
                severity="info",
            )

    def _check_repo_identity(self) -> None:
        if not (REPO_ROOT / ".git").exists():
            self.add("repo", "fail", f"{REPO_ROOT} is not a git repo")
            return
        remote = _git(["remote", "get-url", "origin"])
        if remote.returncode != 0:
            self.add("repo-origin", "fail", remote.stderr.strip() or "origin remote missing")
            return
        url = remote.stdout.strip()
        if "hermes-agent" not in url:
            self.add("repo-origin", "warn", f"origin does not look like hermes-agent: {url}", severity="warn")
        else:
            self.add("repo-origin", "pass", f"origin={url}")

    def _check_git_state(self) -> None:
        branch = _git(["branch", "--show-current"])
        current_branch = branch.stdout.strip() if branch.returncode == 0 else ""
        if current_branch != self.prod_branch:
            self.add("git-branch", "fail", f"expected production branch {self.prod_branch!r}, got {current_branch!r}")
        else:
            self.add("git-branch", "pass", f"on {current_branch}")

        status = _git(["status", "--porcelain=v1"])
        dirty_lines = [line for line in status.stdout.splitlines() if line.strip()]
        if dirty_lines and not self.allow_dirty:
            self.add(
                "git-dirty",
                "fail",
                f"working tree has {len(dirty_lines)} changed/untracked item(s); rerun with --allow-dirty only for audit",
                changed_count=len(dirty_lines),
            )
        elif dirty_lines:
            self.add("git-dirty", "warn", f"working tree dirty but allowed ({len(dirty_lines)} item(s))", severity="warn")
        else:
            self.add("git-dirty", "pass", "working tree clean")

        head = _git(["rev-parse", "HEAD"])
        upstream = _git(["rev-parse", f"origin/{self.prod_branch}"])
        if head.returncode == 0 and upstream.returncode == 0:
            head_sha = head.stdout.strip()
            upstream_sha = upstream.stdout.strip()
            if head_sha == upstream_sha:
                self.add("git-origin-match", "pass", f"HEAD matches origin/{self.prod_branch} ({head_sha[:10]})")
            else:
                self.add(
                    "git-origin-match",
                    "fail",
                    f"HEAD {head_sha[:10]} != origin/{self.prod_branch} {upstream_sha[:10]}",
                    head=head_sha,
                    upstream=upstream_sha,
                )
        else:
            self.add("git-origin-match", "warn", "could not compare HEAD to origin branch", severity="warn")

        branches = _git(["branch", "--format=%(refname:short)"])
        if branches.returncode == 0:
            stale = [
                item.strip()
                for item in branches.stdout.splitlines()
                if item.strip().startswith(STALE_BRANCH_PREFIXES)
            ]
            if stale:
                self.add("stale-branches", "warn", f"stale update/worktree branches present: {', '.join(stale)}", severity="warn")
            else:
                self.add("stale-branches", "pass", "no stale sync/claude branches found")

        worktrees = _git(["worktree", "list", "--porcelain"])
        if worktrees.returncode == 0:
            extra_worktrees = [
                line.split(" ", 1)[1]
                for line in worktrees.stdout.splitlines()
                if line.startswith("worktree ") and Path(line.split(" ", 1)[1]).resolve() != REPO_ROOT
            ]
            if extra_worktrees:
                self.add("extra-worktrees", "warn", f"extra worktrees present: {', '.join(extra_worktrees)}", severity="warn")
            else:
                self.add("extra-worktrees", "pass", "no extra worktrees")

    def _check_local_delta_surface(self) -> None:
        missing = [path for path in LOCAL_DELTA_PATHS if not (REPO_ROOT / path).exists()]
        if missing:
            self.add("local-delta-files", "fail", f"missing local Hermes/Open Brain files: {', '.join(missing)}")
        else:
            self.add("local-delta-files", "pass", f"{len(LOCAL_DELTA_PATHS)} local delta files present")

        missing_patterns: list[str] = []
        marker_count = 0
        for rel_path, required in LOCAL_DELTA_PATTERNS:
            text = _read_text(REPO_ROOT / rel_path)
            needles = (required,) if isinstance(required, str) else tuple(required)
            for needle in needles:
                marker_count += 1
                if needle not in text:
                    missing_patterns.append(f"{rel_path}:{needle}")
        if missing_patterns:
            self.add("local-delta-patterns", "fail", f"expected integration markers missing: {', '.join(missing_patterns)}")
        else:
            self.add("local-delta-patterns", "pass", f"{marker_count} integration markers present")

    def _check_config(self) -> None:
        config_path = self.hermes_home / "config.yaml"
        env_path = self.hermes_home / ".env"
        if not config_path.exists():
            self.add("config-file", "fail", f"missing config: {config_path}")
            return

        env = dict(os.environ)
        env.update(_load_dotenv(env_path))
        try:
            config = _load_config(config_path)
        except RuntimeError as exc:
            self.add("config-load", "fail", str(exc))
            return
        self.add("config-load", "pass", f"loaded {config_path}")

        memory = config.get("memory") if isinstance(config.get("memory"), dict) else {}
        provider = str(memory.get("provider") or "")
        if provider == "openbrain":
            self.add("config-memory-provider", "pass", "memory.provider=openbrain")
        else:
            self.add("config-memory-provider", "warn", f"memory.provider={provider or '<missing>'}", severity="warn")

        servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
        open_brain = servers.get("open_brain") if isinstance(servers.get("open_brain"), dict) else {}
        url = str(open_brain.get("url") or "").strip()
        headers = open_brain.get("headers") if isinstance(open_brain.get("headers"), dict) else {}
        raw_key = str(headers.get("x-brain-key") or headers.get("x-access-key") or "").strip()
        expanded_key = _expand_env(raw_key, env)
        if not url:
            self.add("config-openbrain-url", "fail", "mcp_servers.open_brain.url is missing")
        else:
            self.add("config-openbrain-url", "pass", f"open_brain url configured: {url}")
        if not raw_key:
            self.add("config-openbrain-key", "fail", "open_brain MCP auth header is missing")
        elif "${" in expanded_key or "$" in expanded_key:
            self.add("config-openbrain-key", "fail", "open_brain MCP auth header still contains an unexpanded env placeholder")
        elif not expanded_key:
            self.add("config-openbrain-key", "fail", "open_brain MCP auth header resolved to an empty value")
        else:
            self.add("config-openbrain-key", "pass", f"open_brain auth header resolved: {_redacted_present(expanded_key)}")

        enabled_plugins = (((config.get("plugins") or {}).get("enabled") or []) if isinstance(config.get("plugins"), dict) else [])
        if "openbrain-query-brain-format" in enabled_plugins:
            self.add("config-openbrain-format-plugin", "pass", "query_brain formatter plugin enabled")
        else:
            self.add("config-openbrain-format-plugin", "warn", "query_brain formatter plugin is not enabled", severity="warn")

    def _check_ssl_guard_inputs(self) -> None:
        for env_name in ("HERMES_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            raw = os.getenv(env_name, "").strip()
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.exists():
                self.add("ssl-ca-env", "fail", f"{env_name} points to a missing CA bundle")
                return
            if path.stat().st_size < 1024:
                self.add("ssl-ca-env", "fail", f"{env_name} points to an implausibly small CA bundle")
                return
            try:
                ssl.create_default_context(cafile=str(path))
            except Exception as exc:
                self.add("ssl-ca-env", "fail", f"{env_name} cannot be loaded as a CA bundle: {exc}")
                return

        try:
            import certifi  # type: ignore
        except Exception as exc:
            self.add("ssl-certifi", "fail", f"certifi import failed: {exc}")
            return
        certifi_path = Path(certifi.where())
        if not certifi_path.exists() or certifi_path.stat().st_size < 1024:
            self.add("ssl-certifi", "fail", "certifi CA bundle is missing or implausibly small")
            return
        try:
            ssl.create_default_context(cafile=str(certifi_path))
        except Exception as exc:
            self.add("ssl-certifi", "fail", f"certifi CA bundle cannot be loaded: {exc}")
            return
        self.add("ssl-certifi", "pass", "certifi CA bundle is loadable")

    def _check_launch_agents(self) -> None:
        launch_dir = Path.home() / "Library" / "LaunchAgents"
        missing = []
        bad_targets = []
        for label in LAUNCH_AGENT_LABELS:
            plist_path = launch_dir / f"{label}.plist"
            if not plist_path.exists():
                missing.append(str(plist_path))
                continue
            try:
                payload = plistlib.loads(plist_path.read_bytes())
            except Exception as exc:
                bad_targets.append(f"{plist_path}: unreadable plist ({exc})")
                continue
            args = payload.get("ProgramArguments") or []
            if isinstance(args, list) and args:
                for arg in args[:2]:
                    text = str(arg)
                    if text.startswith("/Users/") and not Path(text).exists():
                        bad_targets.append(f"{plist_path}: missing target {text}")
        if missing:
            self.add("launchagents-installed", "warn", f"missing LaunchAgent plist(s): {', '.join(missing)}", severity="warn")
        else:
            self.add("launchagents-installed", "pass", "Hermes LaunchAgent plists are installed")
        if bad_targets:
            self.add("launchagents-targets", "fail", "; ".join(bad_targets))
        else:
            self.add("launchagents-targets", "pass", "LaunchAgent executable targets exist")

    def _check_runtime_gateway(self) -> None:
        pid_file = self.hermes_home / "gateway.pid"
        if not pid_file.exists():
            self.add("runtime-gateway-pid", "fail", f"missing {pid_file}")
            return
        try:
            payload = json.loads(pid_file.read_text(encoding="utf-8"))
            pid = int(payload.get("pid") or 0)
        except Exception as exc:
            self.add("runtime-gateway-pid", "fail", f"could not parse gateway pid file: {exc}")
            return
        if pid <= 0:
            self.add("runtime-gateway-pid", "fail", "gateway pid is empty")
            return
        try:
            running = psutil.pid_exists(pid)
        except Exception as exc:  # psutil should not raise here, but stay boring
            self.add("runtime-gateway-pid", "warn", f"could not check gateway pid {pid}: {exc}", severity="warn")
            return
        if not running:
            self.add("runtime-gateway-pid", "fail", f"gateway pid {pid} is not running")
            return
        self.add("runtime-gateway-pid", "pass", f"gateway running as pid {pid}")

    def _check_health_monitor(self) -> None:
        jsonl_path = self.hermes_home / "logs" / "health-monitor.jsonl"
        if not jsonl_path.exists():
            self.add("health-monitor-jsonl", "fail", f"missing {jsonl_path}")
            return
        lines = [line for line in _read_text(jsonl_path).splitlines() if line.strip()]
        if not lines:
            self.add("health-monitor-jsonl", "fail", f"{jsonl_path} is empty")
            return

        records: list[dict[str, Any]] = []
        for line in lines[-100:]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        if not records:
            self.add("health-monitor-jsonl", "fail", "no parseable health monitor records")
            return

        latest_by_service: dict[str, dict[str, Any]] = {}
        for item in records:
            service = str(item.get("service") or "")
            if service:
                latest_by_service[service] = item

        stale: list[str] = []
        unhealthy: list[str] = []
        now = _utc_now()
        for service in ("ollama", "gateway", "telegram", "openbrain", "disk", "memory"):
            item = latest_by_service.get(service)
            if not item:
                unhealthy.append(f"{service}: missing")
                continue
            if item.get("status") != "healthy":
                unhealthy.append(f"{service}: {item.get('status')} ({item.get('detail')})")
            timestamp = str(item.get("timestamp") or "")
            try:
                observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                stale.append(f"{service}: bad timestamp")
                continue
            if now - observed.astimezone(timezone.utc) > timedelta(seconds=HEALTH_FRESHNESS_SECONDS):
                stale.append(service)

        openbrain = latest_by_service.get("openbrain") or {}
        tool_count = int(openbrain.get("tool_count") or 0)
        if tool_count and tool_count < EXPECTED_OPENBRAIN_TOOL_FLOOR:
            unhealthy.append(f"openbrain: expected at least {EXPECTED_OPENBRAIN_TOOL_FLOOR} tools, saw {tool_count}")

        if unhealthy:
            self.add("health-monitor-services", "fail", "; ".join(unhealthy))
        elif stale:
            self.add("health-monitor-services", "warn", f"health records stale or malformed: {', '.join(stale)}", severity="warn")
        else:
            self.add("health-monitor-services", "pass", "health monitor reports all required services healthy", openbrain_tool_count=tool_count)

    def _check_openbrain_live(self) -> None:
        config_path = self.hermes_home / "config.yaml"
        env = dict(os.environ)
        env.update(_load_dotenv(self.hermes_home / ".env"))
        try:
            config = _load_config(config_path)
            server = ((config.get("mcp_servers") or {}).get("open_brain") or {})
            url = str(server.get("url") or "").strip()
            headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
            key = _expand_env(str(headers.get("x-brain-key") or ""), env)
        except Exception as exc:
            self.add("openbrain-live-smoke", "fail", f"could not read Open Brain config: {exc}")
            return
        if not url or not key or "${" in key:
            self.add("openbrain-live-smoke", "fail", "Open Brain URL/key unavailable for live smoke")
            return
        body = json.dumps({"jsonrpc": "2.0", "id": "hermes-update-guard", "method": "tools/list"}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"content-type": "application/json", "x-brain-key": key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            self.add("openbrain-live-smoke", "fail", f"Open Brain live smoke returned HTTP {exc.code}")
            return
        except Exception as exc:
            self.add("openbrain-live-smoke", "fail", f"Open Brain live smoke failed: {type(exc).__name__}: {exc}")
            return
        try:
            payload = _jsonrpc_text_payload(raw)
        except Exception as exc:
            self.add("openbrain-live-smoke", "fail", f"could not parse Open Brain response: {exc}")
            return
        result = payload.get("result") if isinstance(payload, dict) else {}
        tools = result.get("tools") if isinstance(result, dict) else []
        if not isinstance(tools, list):
            self.add("openbrain-live-smoke", "fail", "Open Brain tools/list response missing tools")
            return
        if len(tools) < EXPECTED_OPENBRAIN_TOOL_FLOOR:
            self.add("openbrain-live-smoke", "fail", f"Open Brain exposed only {len(tools)} tools")
            return
        self.add("openbrain-live-smoke", "pass", f"Open Brain live tools/list ok ({len(tools)} tools)")


def _print_human(report: dict[str, Any]) -> None:
    status = report["status"].upper()
    print(f"Hermes update guard: {status} ({report['phase']})")
    print(f"repo: {report['repo_root']}")
    print(f"blocking failures: {report['blocking_failures']}  warnings: {report['warnings']}")
    print("")
    for check in report["checks"]:
        marker = {
            "pass": "PASS",
            "fail": "FAIL",
            "warn": "WARN",
            "skip": "SKIP",
        }.get(check["status"], check["status"].upper())
        print(f"[{marker}] {check['name']}: {check['detail']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes production update pre/post guard.")
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--pre", action="store_true", help="Run pre-update checks.")
    phase.add_argument("--post", action="store_true", help="Run post-update checks.")
    phase.add_argument("--full", action="store_true", help="Run both pre- and post-update checks.")
    parser.add_argument("--hermes-home", default=str(DEFAULT_HERMES_HOME), help="Hermes home directory.")
    parser.add_argument("--prod-branch", default=DEFAULT_PROD_BRANCH, help="Expected production branch.")
    parser.add_argument("--allow-dirty", action="store_true", help="Warn instead of failing on dirty working tree.")
    parser.add_argument("--live-smoke", action="store_true", help="Perform live Open Brain tools/list smoke check.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable output.")
    args = parser.parse_args(argv)

    selected_phase = "pre" if args.pre else "post" if args.post else "full"
    guard = HermesUpdateGuard(
        hermes_home=Path(args.hermes_home).expanduser(),
        prod_branch=args.prod_branch,
        allow_dirty=args.allow_dirty,
        live_smoke=args.live_smoke,
    )
    report = guard.run(selected_phase)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
