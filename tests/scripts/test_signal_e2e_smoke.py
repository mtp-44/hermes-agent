from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "signal_e2e_smoke.py"
SPEC = importlib.util.spec_from_file_location("signal_e2e_smoke", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def _handoff_payload() -> dict[str, str]:
    return {
        "signal_http_url": "http://127.0.0.1:18080",
        "signal_account": "+15550000002",
        "signal_allowed_user": "11111111-2222-3333-4444-555555555555",
        "signal_home_channel": "11111111-2222-3333-4444-555555555555",
    }


def _write_handoff(path: Path, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(_handoff_payload()), encoding="utf-8")
    path.chmod(mode)
    return path


def test_private_handoff_loads_without_exposing_identifiers(tmp_path):
    path = _write_handoff(tmp_path / "signal.json")

    loaded = smoke.load_protected_handoff(path)

    assert loaded.http_url == "http://127.0.0.1:18080"
    assert loaded.allowed_user == loaded.home_channel
    assert loaded.account != loaded.allowed_user


def test_handoff_rejects_group_or_other_read_access(tmp_path):
    path = _write_handoff(tmp_path / "signal.json", mode=0o640)

    with pytest.raises(ValueError, match="0600"):
        smoke.load_protected_handoff(path)


def test_handoff_rejects_non_loopback_endpoint(tmp_path):
    payload = _handoff_payload()
    payload["signal_http_url"] = "https://signal.example.test:18080"
    path = tmp_path / "signal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="loopback"):
        smoke.load_protected_handoff(path)


def test_handoff_requires_same_allowed_user_and_home_channel(tmp_path):
    payload = _handoff_payload()
    payload["signal_home_channel"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    path = tmp_path / "signal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="same test user"):
        smoke.load_protected_handoff(path)


def test_isolated_home_refuses_production_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke.Path, "home", lambda: tmp_path)
    production = tmp_path / ".hermes"
    profile = production / "profiles" / "signal-test"
    profile.mkdir(parents=True)
    profile.chmod(0o700)

    with pytest.raises(ValueError, match="production Hermes home"):
        smoke.validate_isolated_home(profile)


def test_isolated_home_accepts_private_external_directory(monkeypatch, tmp_path):
    fake_user_home = tmp_path / "user-home"
    fake_user_home.mkdir()
    monkeypatch.setattr(smoke.Path, "home", lambda: fake_user_home)
    isolated = tmp_path / "wp2-hermes-home"
    isolated.mkdir()
    isolated.chmod(0o700)

    assert smoke.validate_isolated_home(isolated) == isolated.resolve()


def test_apply_environment_sets_strict_signal_shape(monkeypatch, tmp_path):
    for key in (
        "SIGNAL_ALLOW_ALL_USERS",
        "GATEWAY_ALLOW_ALL_USERS",
        "SIGNAL_GROUP_ALLOWED_USERS",
    ):
        monkeypatch.delenv(key, raising=False)
    config = smoke.SignalSmokeConfig(**{
        "http_url": "http://127.0.0.1:18080",
        "account": "+15550000002",
        "allowed_user": "11111111-2222-3333-4444-555555555555",
        "home_channel": "11111111-2222-3333-4444-555555555555",
    })

    smoke.apply_signal_environment(config, tmp_path)

    assert os.environ["SIGNAL_ALLOWED_USERS"] == config.allowed_user
    assert os.environ["SIGNAL_HOME_CHANNEL"] == config.home_channel
    assert os.environ["SIGNAL_REACTIONS"] == "true"
    assert os.environ["SIGNAL_ALLOW_ALL_USERS"] == "false"
    assert os.environ["GATEWAY_ALLOW_ALL_USERS"] == "false"
    assert os.environ["SIGNAL_GROUP_ALLOWED_USERS"] == ""


def test_apply_environment_refuses_allow_all(monkeypatch, tmp_path):
    monkeypatch.setenv("SIGNAL_ALLOW_ALL_USERS", "true")
    config = smoke.SignalSmokeConfig(
        http_url="http://127.0.0.1:18080",
        account="+15550000002",
        allowed_user="11111111-2222-3333-4444-555555555555",
        home_channel="11111111-2222-3333-4444-555555555555",
    )

    with pytest.raises(ValueError, match="must not be enabled"):
        smoke.apply_signal_environment(config, tmp_path)
