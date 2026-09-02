from __future__ import annotations

import json
import time
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


# --- email triage daemon check (ES-0005) -----------------------------------

from datetime import datetime as _dt, timedelta as _td


def _email_log(lines):
    return "\n".join(lines) + "\n"


def _stamp(now, **delta):
    return (now - _td(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def test_counts_only_events_inside_the_window():
    now = _dt(2026, 9, 1, 12, 0, 0)
    text = _email_log([
        f"{_stamp(now, hours=1)} INFO Starting email daemon (poll every 300s)",
        f"{_stamp(now, hours=2)} INFO Starting email daemon (poll every 300s)",
        f"{_stamp(now, hours=99)} INFO Starting email daemon (poll every 300s)",   # outside
        f"{_stamp(now, hours=3)} ERROR Signal send failed: ReadTimeout",
        f"{_stamp(now, hours=1)} INFO Applied recycle label to 34 Archive email(s)",
    ])
    assert hhm.count_email_events(text, now, 6) == (2, 1)


def test_unparseable_and_partial_lines_are_ignored():
    now = _dt(2026, 9, 1, 12, 0, 0)
    text = _email_log([
        "il to 24 Archive email(s)",                          # partial first line from a tail seek
        "not a log line at all",
        "2026-13-45 99:99:99 INFO Starting email daemon",     # unparseable stamp
        f"{_stamp(now, minutes=5)} INFO Starting email daemon (poll every 300s)",
    ])
    assert hhm.count_email_events(text, now, 6) == (1, 0)


def _email_env(monkeypatch, tmp_path, text, age_seconds=60.0):
    log = tmp_path / "triage.log"
    log.write_text(text, encoding="utf-8")
    import os as _os
    stamp = time.time() - age_seconds
    _os.utime(log, (stamp, stamp))
    monkeypatch.setenv("HERMES_HEALTH_EMAIL_LOG", str(log))
    for name in ("HERMES_HEALTH_EMAIL_STALE_SECONDS", "HERMES_HEALTH_EMAIL_MAX_RESTARTS",
                 "HERMES_HEALTH_EMAIL_RESTART_WINDOW_HOURS"):
        monkeypatch.delenv(name, raising=False)
    return log


def test_a_healthy_daemon_passes(monkeypatch, tmp_path):
    now = _dt.now()
    _email_env(monkeypatch, tmp_path, _email_log([
        f"{_stamp(now, minutes=30)} INFO Applied recycle label to 34 Archive email(s)",
    ]))
    result = hhm._check_email()
    assert result.ok
    assert result.metadata["restarts_in_window"] == 0


def test_a_silent_log_is_caught(monkeypatch, tmp_path):
    now = _dt.now()
    _email_env(monkeypatch, tmp_path, _email_log([
        f"{_stamp(now, hours=48)} INFO Applied recycle label to 34 Archive email(s)",
    ]), age_seconds=48 * 3600)
    result = hhm._check_email()
    assert not result.ok
    assert result.fingerprint == "email:stale"


def test_the_real_august_crash_cluster_would_have_fired(monkeypatch, tmp_path):
    # 2026-08-22 saw 20 restarts in a day, absorbed silently by KeepAlive. This
    # is the fault the check exists for, so assert against its actual shape.
    now = _dt.now()
    lines = [f"{_stamp(now, minutes=5 * i)} INFO Starting email daemon (poll every 300s)"
             for i in range(1, 21)]
    _email_env(monkeypatch, tmp_path, _email_log(lines))
    result = hhm._check_email()
    assert not result.ok
    assert result.fingerprint == "email:restart-loop"
    assert result.metadata["restarts_in_window"] == 20
    assert "do not restart it" in result.detail


def test_an_ordinary_restart_does_not_fire(monkeypatch, tmp_path):
    # A single restart after a deploy or a bridge blip must stay quiet, or the
    # check gets muted and stops being worth having.
    now = _dt.now()
    _email_env(monkeypatch, tmp_path, _email_log([
        f"{_stamp(now, minutes=20)} INFO Starting email daemon (poll every 300s)",
        f"{_stamp(now, minutes=19)} INFO IMAP connected",
    ]))
    assert hhm._check_email().ok


def test_signal_failures_are_reported_but_never_alarm(monkeypatch, tmp_path):
    now = _dt.now()
    _email_env(monkeypatch, tmp_path, _email_log([
        f"{_stamp(now, hours=1)} ERROR Signal send failed: ReadTimeout",
        f"{_stamp(now, hours=2)} ERROR Signal send failed: ReadTimeout",
        f"{_stamp(now, hours=3)} ERROR Signal send failed: ReadTimeout",
    ]))
    result = hhm._check_email()
    assert result.ok
    assert result.metadata["signal_failures_in_window"] == 3


def test_a_missing_log_skips_rather_than_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HEALTH_EMAIL_LOG", str(tmp_path / "nope.log"))
    result = hhm._check_email()
    assert result.ok and result.skipped


def test_email_has_no_restarter(tmp_path):
    # KeepAlive already restarts this daemon; restarting it again on a
    # crash-loop alarm would add to the thing being alarmed about.
    monitor = hhm._build_monitor(state_path=tmp_path / "s.json", log_path=tmp_path / "l.jsonl")
    assert "email" in monitor.checkers
    assert "email" not in monitor.restarters


def test_email_is_in_the_default_service_list():
    assert "email" in hhm.DEFAULT_SERVICES


# --- estate silence check (BS-0004 / WW-0004) ------------------------------
# `email` above watches one job; this watches the whole registry, because
# `launchctl list`'s status column is the last EXIT STATUS and a job that exits
# 0 while doing nothing reads as healthy. Most of these assert the check stays
# QUIET: eleven of seventeen registry entries carry `stale_after: null` on
# purpose, and alarming on those would page Mark for not using his own system.


class _FakeEstate:
    """Stands in for bootstrap's Estate — only `all_services()` is used here."""

    def __init__(self, services):
        self._services = services

    def all_services(self):
        return self._services


def _job(label, log, stale_after):
    return {"label": label, "evidence": {"log": str(log) if log else None,
                                         "stale_after": stale_after}}


def _aged(tmp_path, name, hours, now):
    import os as _os

    p = tmp_path / name
    p.write_text("x\n", encoding="utf-8")
    when = now.timestamp() - hours * 3600
    _os.utime(p, (when, when))
    return p


def _freshness(evidence, now):
    """The real rule is bootstrap's spine.log_freshness; this is the same
    contract, kept local so this suite does not depend on ~/ai/bootstrap being
    present when it runs."""
    from datetime import datetime as _dt

    log = evidence.get("log")
    bar = evidence.get("stale_after")
    if log is None:
        return {"log": None, "stale": None}
    path = Path(log)
    if not path.exists():
        return {"log": log, "exists": False, "stale": None}
    age = now.timestamp() - path.stat().st_mtime
    out = {"log": log, "exists": True, "age_hours": round(age / 3600, 2)}
    if bar is None:
        out["stale"] = None
        return out
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    out["stale"] = age > int(bar[:-1]) * units[bar[-1]]
    return out


def test_a_missed_weekly_review_is_found_silent(tmp_path):
    now = datetime(2026, 9, 2, 12, 0, 0)
    estate = _FakeEstate([
        _job("com.mh.estate-review", _aged(tmp_path, "er.log", 14 * 24, now), "8d"),
        _job("com.mh.brainlocal-db-backup", _aged(tmp_path, "bk.log", 2, now), "36h"),
    ])
    silent = hhm.find_silent_jobs(estate, _freshness, now)
    assert [s["label"] for s in silent] == ["com.mh.estate-review"]
    assert silent[0]["age_hours"] == 336.0
    assert silent[0]["bar"] == "8d"


def test_a_null_stale_after_is_never_alarmed_on(tmp_path):
    """gateway.log sat 17.6 days quiet while perfectly healthy and
    dashboard.log is 34 days old while the process is up: for a daemon whose log
    is request-driven, a quiet log means no clients."""
    now = datetime(2026, 9, 2, 12, 0, 0)
    estate = _FakeEstate([
        _job("com.mh.hermes-dashboard", _aged(tmp_path, "db.log", 818, now), None),
    ])
    assert hhm.find_silent_jobs(estate, _freshness, now) == []


def test_the_email_daemon_is_left_to_its_own_check(tmp_path):
    """Two alarms for one job trains you to ignore the channel, and
    `_check_email` says more (restart loops, Signal failures)."""
    now = datetime(2026, 9, 2, 12, 0, 0)
    estate = _FakeEstate([
        _job("net.mtp44.email-triage", _aged(tmp_path, "tr.log", 900, now), "36h"),
    ])
    assert hhm.find_silent_jobs(estate, _freshness, now) == []


def test_an_evidence_log_that_never_appeared_is_reported_differently(tmp_path):
    now = datetime(2026, 9, 2, 12, 0, 0)
    estate = _FakeEstate([_job("com.mh.thing", tmp_path / "never.log", "8d")])
    silent = hhm.find_silent_jobs(estate, _freshness, now)
    assert silent[0]["why"] == "its evidence log has never been written"
    assert silent[0]["age_hours"] is None


def test_a_fresh_estate_is_silent(tmp_path):
    now = datetime(2026, 9, 2, 12, 0, 0)
    estate = _FakeEstate([
        _job("com.mh.a", _aged(tmp_path, "a.log", 1, now), "36h"),
        _job("com.mh.b", _aged(tmp_path, "b.log", 2, now), "8d"),
    ])
    assert hhm.find_silent_jobs(estate, _freshness, now) == []


def _latest(tmp_path, **fields):
    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"date": "2026-09-08", "generated": "2026-09-08T07:00:00",
                             "findings": 3, "pr_url": "https://example.invalid/pr/2",
                             "pr_error": None, **fields}), encoding="utf-8")
    return p


def test_a_review_that_gathered_but_could_not_deliver_is_a_fault(tmp_path):
    now = datetime(2026, 9, 8, 12, 0, 0)
    got = hhm.read_review_latest(
        _latest(tmp_path, pr_url=None, pr_error="gh pr create failed: no auth"), now, 9)
    assert got["pr_error"] == "gh pr create failed: no auth"
    assert got["overdue"] is False


def test_a_reviewer_that_stopped_running_is_overdue(tmp_path):
    now = datetime(2026, 9, 30, 12, 0, 0)
    got = hhm.read_review_latest(_latest(tmp_path), now, 9)
    assert got["overdue"] is True
    assert got["age_days"] == 22.2


def test_a_recent_review_is_not_overdue(tmp_path):
    now = datetime(2026, 9, 8, 12, 0, 0)
    got = hhm.read_review_latest(_latest(tmp_path), now, 9)
    assert got["overdue"] is False
    assert got["findings"] == 3


def test_an_open_pull_request_is_reported_and_never_alarmed_on(tmp_path, monkeypatch):
    """A reminder down a health channel is how a health channel stops being
    read — and it would leave this service unhealthy, and so the monitor exiting
    1, until Mark got round to it. `make standup` is where a nudge belongs."""
    monkeypatch.setenv("HERMES_HEALTH_ESTATE_REVIEW_LATEST", str(_latest(tmp_path)))
    monkeypatch.setattr(hhm, "_load_estate_spine",
                        lambda _: (type("E", (), {"load": staticmethod(lambda: _FakeEstate([]))}),
                                   _freshness))
    result = hhm._check_estate()
    assert result.ok is True
    assert "delivered, 3 finding(s)" in result.detail


def test_a_missing_latest_json_is_not_a_fault(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HEALTH_ESTATE_REVIEW_LATEST", str(tmp_path / "nope.json"))
    monkeypatch.setattr(hhm, "_load_estate_spine",
                        lambda _: (type("E", (), {"load": staticmethod(lambda: _FakeEstate([]))}),
                                   _freshness))
    result = hhm._check_estate()
    assert result.ok is True
    assert "has not run yet" in result.detail


def test_an_unreadable_latest_json_is_a_fault(tmp_path, monkeypatch):
    bad = tmp_path / "latest.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("HERMES_HEALTH_ESTATE_REVIEW_LATEST", str(bad))
    monkeypatch.setattr(hhm, "_load_estate_spine",
                        lambda _: (type("E", (), {"load": staticmethod(lambda: _FakeEstate([]))}),
                                   _freshness))
    result = hhm._check_estate()
    assert result.ok is False
    assert result.fingerprint == "estate:review:unreadable"


def test_a_silent_job_beats_a_review_fault_in_the_message(tmp_path, monkeypatch):
    """Silence is the thing WW-0001 was written about; it is reported first."""
    now = datetime(2026, 9, 2, 12, 0, 0)
    estate = _FakeEstate([_job("com.mh.thing", _aged(tmp_path, "t.log", 400, now), "36h")])
    monkeypatch.setenv("HERMES_HEALTH_ESTATE_REVIEW_LATEST",
                       str(_latest(tmp_path, pr_error="also broken")))
    monkeypatch.setattr(hhm, "_load_estate_spine",
                        lambda _: (type("E", (), {"load": staticmethod(lambda: estate)}), _freshness))
    result = hhm._check_estate()
    assert result.ok is False
    assert result.fingerprint == "estate:silent:com.mh.thing"
    assert "make review-gather" in result.detail


def test_no_bootstrap_on_the_machine_skips_rather_than_pages(monkeypatch):
    """A bare machine part-way through `make bootstrap` has no ~/ai/bootstrap
    yet, and the monitor must keep watching the nine things it can see."""
    monkeypatch.setattr(hhm, "_load_estate_spine", lambda _: None)
    result = hhm._check_estate()
    assert result.skipped is True
    assert result.ok is True


def test_an_unreadable_registry_is_a_fault(monkeypatch):
    def boom(_):
        class E:
            @staticmethod
            def load():
                raise ValueError("estate.yaml is not valid YAML")
        return E, _freshness

    monkeypatch.setattr(hhm, "_load_estate_spine", boom)
    result = hhm._check_estate()
    assert result.ok is False
    assert result.fingerprint.startswith("estate:registry:")


def test_the_estate_check_has_no_restarter():
    """There is no generic safe restart for "some job stopped doing work", and
    kicking a job whose evidence is stale is as likely to hide the cause."""
    monitor = hhm._build_monitor(state_path=Path("/dev/null"), log_path=Path("/dev/null"))
    assert "estate" in monitor.checkers
    assert "estate" not in monitor.restarters
