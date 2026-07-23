from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from scripts.pwa_release import ReleaseError, ReleaseManager


NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _project(tmp_path):
    project = tmp_path / "source"
    dist = project / "apps" / "desktop" / "dist-pwa"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
    (dist / "sw.js").write_text(
        "const VERSION = 'hermes-pwa-__HERMES_PWA_BUILD_STAMP__'\n",
        encoding="utf-8",
    )
    return project


def _manager(tmp_path, monkeypatch, commit="a" * 40):
    manager = ReleaseManager(
        project_root=_project(tmp_path),
        release_root=tmp_path / "runtime",
        health_urls=(),
        now_fn=lambda: NOW,
    )
    monkeypatch.setattr(manager, "_commit", lambda: commit)
    monkeypatch.setattr(manager, "_verify_source", lambda: None)
    return manager


def test_deploy_stages_stamped_release_before_atomic_promotion(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)

    result = manager.deploy()

    current = manager.release_root / manager.current_link.readlink()
    metadata = json.loads((current / "pwa-release.json").read_text(encoding="utf-8"))
    assert result["release_id"] == metadata["release_id"]
    assert metadata["commit"] == "a" * 40
    assert metadata["release_id"] in (current / "sw.js").read_text(encoding="utf-8")
    assert f'content="{metadata["release_id"]}"' in (current / "index.html").read_text(encoding="utf-8")
    assert not list(manager.release_root.glob(".staging-*"))


def test_failed_health_restores_prior_release(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.deploy()
    original = manager.current_link.readlink()
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    monkeypatch.setattr(manager, "_require_healthy", lambda: (_ for _ in ()).throw(ReleaseError("unhealthy")))

    with pytest.raises(ReleaseError, match="unhealthy"):
        manager.deploy()

    assert manager.current_link.readlink() == original


def test_rollback_swaps_current_and_previous(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.deploy()
    first = manager.current_link.readlink()
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    manager.deploy()
    second = manager.current_link.readlink()

    result = manager.rollback()

    assert result["action"] == "rollback"
    assert manager.current_link.readlink() == first
    assert manager.previous_link.readlink() == second


def test_interrupted_stage_cannot_change_served_release(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.deploy()
    original = manager.current_link.readlink()
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    monkeypatch.setattr(
        manager,
        "_stage_release",
        lambda **_kwargs: (_ for _ in ()).throw(ReleaseError("copy interrupted")),
    )

    with pytest.raises(ReleaseError, match="copy interrupted"):
        manager.deploy()

    assert manager.current_link.readlink() == original


def test_status_is_not_ok_without_a_promoted_release(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)

    status = manager.status()

    assert status["ok"] is False
    assert status["current"] is None
