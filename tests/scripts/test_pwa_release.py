from __future__ import annotations

import json
import os
import plistlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.pwa_release import ReleaseError, ReleaseManager, _launchd_runtime


NOW = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)


def _project(tmp_path):
    project = tmp_path / "source"
    dist = project / "apps" / "desktop" / "dist-pwa"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head></head><body></body></html>", encoding="utf-8"
    )
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
    assert f'content="{metadata["release_id"]}"' in (current / "index.html").read_text(
        encoding="utf-8"
    )
    assert not list(manager.release_root.glob(".staging-*"))


def test_failed_health_restores_prior_release(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.deploy()
    original = manager.current_link.readlink()
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    restarts = []
    monkeypatch.setattr(
        manager,
        "_restart_runtime",
        lambda: restarts.append(manager.current_link.readlink()),
    )
    monkeypatch.setattr(
        manager,
        "_require_healthy",
        lambda: (_ for _ in ()).throw(ReleaseError("unhealthy")),
    )

    with pytest.raises(ReleaseError, match="unhealthy"):
        manager.deploy()

    assert manager.current_link.readlink() == original
    assert restarts == [
        manager.releases_dir.relative_to(manager.release_root)
        / f"20260723T120000Z-{'b' * 12}",
        original,
    ]


def test_deploy_refuses_release_commit_that_runtime_source_will_not_load(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.runtime_project_root = tmp_path / "canonical-runtime-source"
    monkeypatch.setattr(manager, "_run", lambda *_args, **_kwargs: "b" * 40)

    with pytest.raises(
        ReleaseError, match="does not match the configured runtime source"
    ):
        manager.deploy()

    assert not manager.current_link.exists()


def test_deploy_restarts_runtime_before_health_gate(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    events = []
    monkeypatch.setattr(manager, "_restart_runtime", lambda: events.append("restart"))
    monkeypatch.setattr(
        manager, "_require_healthy", lambda: events.append("health") or []
    )

    manager.deploy()

    assert events == ["restart", "health"]


def test_launchd_runtime_uses_configured_working_directory_and_label(tmp_path):
    plist_path = tmp_path / "com.example.hermes-pwa.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(
            {
                "Label": "com.example.hermes-pwa",
                "WorkingDirectory": "/srv/hermes-agent",
            },
            handle,
        )

    runtime_root, restart_command = _launchd_runtime(plist_path)

    assert runtime_root == Path("/srv/hermes-agent")
    assert restart_command[-1] == f"gui/{os.getuid()}/com.example.hermes-pwa"


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


def test_deploy_carries_forward_assets_needed_by_already_open_clients(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch)
    manager.compatibility_assets_dir.mkdir(parents=True)
    (manager.compatibility_assets_dir / "old-lazy-hash.js").write_text(
        "export const oldClient = true\n",
        encoding="utf-8",
    )

    manager.deploy()

    current = manager.release_root / manager.current_link.readlink()
    assert (current / "assets" / "old-lazy-hash.js").read_text(encoding="utf-8") == (
        "export const oldClient = true\n"
    )


def test_deploy_carries_forward_assets_from_prior_atomic_releases(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    source_assets = manager.project_root / "apps" / "desktop" / "dist-pwa" / "assets"
    source_assets.mkdir()
    old_asset = source_assets / "old-lazy-hash.js"
    old_asset.write_text("export const oldClient = true\n", encoding="utf-8")
    manager.deploy()

    old_asset.unlink()
    (source_assets / "new-entry-hash.js").write_text(
        "export const newClient = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    manager.deploy()

    current = manager.release_root / manager.current_link.readlink()
    assert (current / "assets" / "old-lazy-hash.js").read_text(encoding="utf-8") == (
        "export const oldClient = true\n"
    )
    assert (current / "assets" / "new-entry-hash.js").is_file()
