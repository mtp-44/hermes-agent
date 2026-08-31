from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.hermes_health_monitor import (
    CheckResult,
    HealthMonitor,
    RestartResult,
    _check_config,
    _check_pwa,
    _parse_hosted_mcp_url,
    _send_telegram_alert,
)


def _now() -> datetime:
    return datetime(2026, 5, 19, 10, 0, 0, tzinfo=timezone.utc)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_first_ollama_failure_restarts_once(tmp_path):
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "health.jsonl"
    restart_calls: list[str] = []

    monitor = HealthMonitor(
        state_path=state_path,
        log_path=log_path,
        checkers={
            "ollama": lambda: CheckResult("ollama", False, "connection refused", "connect-refused"),
        },
        restarters={
            "ollama": lambda: restart_calls.append("ollama") or RestartResult(True, "restart requested"),
        },
        alert_sender=None,
        restart_backoff_seconds=300,
        now_fn=_now,
        correlation_id_factory=lambda: "cid-1",
    )

    exit_code = monitor.run(["ollama"])

    assert exit_code == 1
    assert restart_calls == ["ollama"]
    state = _read_json(state_path)
    assert state["services"]["ollama"]["failure_count"] == 1
    log_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert any('"action": "restart_requested"' in line for line in log_lines)


def test_persistent_failure_sends_one_alert(tmp_path):
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "health.jsonl"
    alert_messages: list[str] = []

    def build_monitor() -> HealthMonitor:
        return HealthMonitor(
            state_path=state_path,
            log_path=log_path,
            checkers={
                "ollama": lambda: CheckResult("ollama", False, "still unhealthy", "still-unhealthy"),
            },
            restarters={},
            alert_sender=lambda message: alert_messages.append(message) or RestartResult(True, "alert sent"),
            restart_backoff_seconds=300,
            now_fn=_now,
            correlation_id_factory=lambda: "cid-2",
        )

    first_exit = build_monitor().run(["ollama"])
    second_exit = build_monitor().run(["ollama"])
    third_exit = build_monitor().run(["ollama"])

    assert first_exit == 1
    assert second_exit == 1
    assert third_exit == 1
    assert len(alert_messages) == 1
    assert "ollama health check failed" in alert_messages[0]
    state = _read_json(state_path)
    assert state["services"]["ollama"]["alert_sent_for"] == "still-unhealthy"


def test_monitor_exits_zero_when_all_services_healthy(tmp_path):
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "health.jsonl"

    monitor = HealthMonitor(
        state_path=state_path,
        log_path=log_path,
        checkers={
            "gateway": lambda: CheckResult("gateway", True, "running", "ok"),
            "ollama": lambda: CheckResult("ollama", True, "ok (3 model(s))", "ok"),
        },
        restarters={},
        alert_sender=None,
        restart_backoff_seconds=300,
        now_fn=_now,
        correlation_id_factory=lambda: "cid-3",
    )

    exit_code = monitor.run(["gateway", "ollama"])

    assert exit_code == 0
    state = _read_json(state_path)
    assert state["services"]["gateway"]["failure_count"] == 0
    assert state["services"]["ollama"]["failure_count"] == 0


def test_check_config_reports_unhealthy_on_bad_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "telegram:\n"
        "  free_response_chats:\n"
        "  - '-5433465714'\n"
        "   - '-5240111863'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hermes_cli.config.get_config_path", lambda: config_path)

    result = _check_config()

    assert result.ok is False
    assert result.fingerprint == "parse:ParserError"
    assert "config.yaml" in result.detail


def test_check_config_ok_on_valid_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "telegram:\n"
        "  free_response_chats:\n"
        "  - '-5433465714'\n"
        "  - '-5240111863'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hermes_cli.config.get_config_path", lambda: config_path)

    result = _check_config()

    assert result.ok is True
    assert result.fingerprint == "ok"


def test_check_config_skips_when_file_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr("hermes_cli.config.get_config_path", lambda: config_path)

    result = _check_config()

    assert result.ok is True
    assert result.skipped is True


def test_check_pwa_verifies_atomic_stamp_and_both_network_paths(tmp_path, monkeypatch):
    release_root = tmp_path / "releases-root"
    release_id = "20260723T120000Z-aaaaaaaaaaaa"
    release = release_root / "releases" / release_id
    release.mkdir(parents=True)
    (release / "pwa-release.json").write_text(
        json.dumps({"release_id": release_id, "commit": "a" * 40}),
        encoding="utf-8",
    )
    (release_root / "current").symlink_to(f"releases/{release_id}")
    requested: list[str] = []
    monkeypatch.setattr("scripts.hermes_health_monitor.sys.platform", "linux")
    monkeypatch.setattr(
        "scripts.hermes_health_monitor._http_json",
        lambda url, **_kwargs: requested.append(url) or (200, {"version": "0.17.0"}),
    )
    websocket_requested: list[str] = []
    monkeypatch.setattr(
        "scripts.hermes_health_monitor._check_websocket",
        lambda url, **_kwargs: websocket_requested.append(url) or "HTTP 101",
    )

    result = _check_pwa(
        release_root=release_root,
        status_urls=("http://loopback/api/status", "https://tailnet/api/status"),
        websocket_urls=("ws://loopback/api/ws-health", "wss://tailnet/api/ws-health"),
    )

    assert result.ok is True
    assert result.metadata == {
        "release_id": release_id,
        "commit": "a" * 40,
        "status_urls": requested,
        "websocket_urls": websocket_requested,
    }
    assert requested == ["http://loopback/api/status", "https://tailnet/api/status"]
    assert websocket_requested == ["ws://loopback/api/ws-health", "wss://tailnet/api/ws-health"]


def test_check_pwa_rejects_stamp_that_does_not_match_current_target(tmp_path):
    release_root = tmp_path / "releases-root"
    release = release_root / "releases" / "release-a"
    release.mkdir(parents=True)
    (release / "pwa-release.json").write_text(
        json.dumps({"release_id": "release-b", "commit": "b" * 40}),
        encoding="utf-8",
    )
    (release_root / "current").symlink_to("releases/release-a")

    result = _check_pwa(release_root=release_root, status_urls=(), websocket_urls=())

    assert result.ok is False
    assert result.fingerprint == "release:stamp-mismatch"


def _clear_openbrain_env(monkeypatch):
    for name in ("OPEN_BRAIN_MCP_URL", "OPENBRAIN_MCP_URL", "SUPABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_probe_url_explicit_env_wins(monkeypatch):
    _clear_openbrain_env(monkeypatch)
    monkeypatch.setenv("OPEN_BRAIN_MCP_URL", "http://localhost:9999")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

    assert _parse_hosted_mcp_url() == "http://localhost:9999"


def test_probe_url_accepts_plugin_spelling(monkeypatch):
    _clear_openbrain_env(monkeypatch)
    monkeypatch.setenv("OPENBRAIN_MCP_URL", "http://localhost:8765")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")

    assert _parse_hosted_mcp_url() == "http://localhost:8765"


def test_probe_url_prefers_gateway_config_over_supabase(monkeypatch):
    # Regression: post-F5 (2026-07-11) the hosted Supabase front door is sealed
    # and 401s every key; the probe must follow the gateway's configured URL,
    # not derive a hosted URL from SUPABASE_URL.
    _clear_openbrain_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(
        "scripts.hermes_health_monitor.read_raw_config",
        lambda: {"mcp_servers": {"open_brain": {"url": "http://localhost:8765"}}},
    )

    assert _parse_hosted_mcp_url() == "http://localhost:8765"


def test_probe_url_falls_back_to_supabase_when_unconfigured(monkeypatch):
    _clear_openbrain_env(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
    monkeypatch.setattr("scripts.hermes_health_monitor.read_raw_config", lambda: {})

    assert _parse_hosted_mcp_url() == "https://example.supabase.co/functions/v1/open-brain-mcp"


def test_alert_detail_names_the_missing_piece(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    for name in ("HERMES_HEALTH_ALERT_CHAT_ID", "TELEGRAM_HOME_CHANNEL"):
        monkeypatch.delenv(name, raising=False)

    result = _send_telegram_alert("test")

    assert result.ok is False
    assert "HERMES_HEALTH_ALERT_CHAT_ID" in result.detail

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("HERMES_HEALTH_ALERT_CHAT_ID", "12345")

    result = _send_telegram_alert("test")

    assert result.ok is False
    assert result.detail == "missing TELEGRAM_BOT_TOKEN"


# --- Task B (`OB-0012`): cheap /health every cycle, full contract daily -----

import scripts.hermes_health_monitor as hhm


def _openbrain_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPEN_BRAIN_MCP_URL", "http://localhost:8765")
    monkeypatch.setenv("MCP_ACCESS_KEY", "k")
    monkeypatch.setattr(hhm, "OPENBRAIN_CONTRACT_STATE_PATH", tmp_path / "contract.json")


def _stub_http(monkeypatch, calls, *, health=(200, {"status": "ok", "database": "ok", "version": "1.0.0"}),
               unauth=(401, {"error": "Unauthorized"}), auth=(200, {"result": {"tools": [{}, {}, {}]}})):
    def fake(url, *, method="GET", headers=None, body=None, timeout=None):
        calls.append((method, url, (body or {}).get("method")))
        if url.endswith("/health"):
            return health
        if headers and "x-brain-key" in headers:
            return auth
        return unauth

    monkeypatch.setattr(hhm, "_http_json", fake)


def test_the_routine_cycle_sends_one_health_request_not_two_tools_list(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)
    calls: list = []
    _stub_http(monkeypatch, calls)

    first = hhm._check_openbrain()          # first run: contract is due
    assert first.ok
    assert any(c[2] == "tools/list" for c in calls)

    calls.clear()
    second = hhm._check_openbrain()         # steady state
    assert second.ok
    assert calls == [("GET", "http://localhost:8765/health", None)]
    assert second.metadata["probe"] == "health"


def test_a_database_outage_fails_the_cheap_probe_immediately(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch, [], health=(503, {"status": "degraded", "database": "unavailable"}))
    result = hhm._check_openbrain()
    assert not result.ok
    assert result.fingerprint == "health:http:503"


def test_a_dead_server_fails_the_cheap_probe_immediately(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise ConnectionRefusedError("no listener")

    monkeypatch.setattr(hhm, "_http_json", boom)
    result = hhm._check_openbrain()
    assert not result.ok
    assert result.fingerprint == "health:ConnectionRefusedError"


def test_an_open_auth_gate_is_still_caught_by_the_contract_probe(monkeypatch, tmp_path):
    # The unauthenticated half of the old check is kept, not dropped: it is the
    # only assertion that the gate is closed. Retiring it to save a request
    # would be a security check removed under the banner of noise reduction.
    _openbrain_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch, [], unauth=(200, {"result": {"tools": []}}))
    result = hhm._check_openbrain()
    assert not result.ok
    assert result.fingerprint == "unauth:http:200"


def test_a_broken_tool_contract_is_caught(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch, [], auth=(200, {"result": {}}))
    result = hhm._check_openbrain()
    assert not result.ok
    assert result.fingerprint == "auth:tools"


def test_a_failed_contract_is_retried_next_cycle_rather_than_next_day(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch, [], auth=(200, {"result": {}}))
    assert not hhm._check_openbrain().ok
    calls: list = []
    _stub_http(monkeypatch, calls, auth=(200, {"result": {}}))
    assert not hhm._check_openbrain().ok
    assert any(c[2] == "tools/list" for c in calls), "a failed contract must not wait a day"


def test_a_deploy_reruns_the_contract_immediately(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch, [])
    hhm._check_openbrain()                                   # settles at v1.0.0
    calls: list = []
    _stub_http(monkeypatch, calls,
               health=(200, {"status": "ok", "database": "ok", "version": "1.1.0"}))
    result = hhm._check_openbrain()
    assert result.ok
    assert result.metadata["reason"] == "version-changed"
    assert any(c[2] == "tools/list" for c in calls)


def test_the_contract_comes_due_again_after_a_day():
    day = hhm.OPENBRAIN_CONTRACT_INTERVAL_SECONDS
    fresh = {"last_ok_at": 1000.0, "last_status": "ok", "version": "1.0.0"}
    assert hhm._openbrain_contract_due(fresh, "1.0.0", 1000.0 + day - 1) == (False, "")
    assert hhm._openbrain_contract_due(fresh, "1.0.0", 1000.0 + day)[0] is True


def test_a_server_predating_the_db_probe_still_passes(monkeypatch, tmp_path):
    _openbrain_env(monkeypatch, tmp_path)
    _stub_http(monkeypatch, [], health=(200, {"status": "ok", "version": "1.0.0"}))
    assert hhm._check_openbrain().ok
