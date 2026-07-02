from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.hermes_health_monitor import CheckResult, HealthMonitor, RestartResult, _check_config


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
