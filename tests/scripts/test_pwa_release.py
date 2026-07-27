from __future__ import annotations

import json
import os
import plistlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import pwa_release
from scripts.pwa_release import (
    BUILD_ASSET_MANIFEST_FILE,
    ReleaseError,
    ReleaseManager,
    _launchd_runtime,
)


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


def _manager(tmp_path, monkeypatch, commit="a" * 40, health_urls=()):
    manager = ReleaseManager(
        project_root=_project(tmp_path),
        release_root=tmp_path / "runtime",
        health_urls=health_urls,
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
    asset_manifest = json.loads(
        (current / BUILD_ASSET_MANIFEST_FILE).read_text(encoding="utf-8")
    )
    assert asset_manifest["compatibility_release_horizon"] == 2


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


def test_deploy_requires_exact_release_stamp_from_both_health_origins(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.deploy()
    original = manager.current_link.readlink()
    manager.health_urls = (
        "http://local.test:9219/api/status",
        "https://tail.test/api/status",
    )
    manager.health_attempts = 1
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    monkeypatch.setattr(manager, "_require_healthy", lambda: [])
    requested_urls = []

    class Response:
        def __init__(self, release_id):
            self.release_id = release_id

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def read(self):
            return json.dumps({"release_id": self.release_id}).encode()

    expected = f"20260723T120000Z-{'b' * 12}"

    def urlopen(request, timeout):
        assert timeout == 3.0
        requested_urls.append(request.full_url)
        release_id = expected if request.full_url.startswith("http://") else "stale"
        return Response(release_id)

    monkeypatch.setattr(pwa_release.urllib.request, "urlopen", urlopen)

    with pytest.raises(ReleaseError, match="release stamp check failed"):
        manager.deploy()

    assert requested_urls == [
        "http://local.test:9219/pwa-release.json",
        "https://tail.test/pwa-release.json",
    ]
    assert manager.current_link.readlink() == original


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


def test_compatibility_assets_are_bounded_to_two_prior_releases(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    source_assets = manager.project_root / "apps" / "desktop" / "dist-pwa" / "assets"
    source_assets.mkdir()

    def replace_source_asset(name):
        for asset in source_assets.iterdir():
            asset.unlink()
        (source_assets / name).write_text(name, encoding="utf-8")

    replace_source_asset("release-a.js")
    manager.deploy()
    replace_source_asset("release-b.js")
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    manager.deploy()
    replace_source_asset("release-c.js")
    monkeypatch.setattr(manager, "_commit", lambda: "c" * 40)
    manager.deploy()
    replace_source_asset("release-d.js")
    monkeypatch.setattr(manager, "_commit", lambda: "d" * 40)
    manager.deploy()

    current_assets = manager.release_root / manager.current_link.readlink() / "assets"
    assert not (current_assets / "release-a.js").exists()
    assert (current_assets / "release-b.js").is_file()
    assert (current_assets / "release-c.js").is_file()
    assert (current_assets / "release-d.js").is_file()


def test_successful_deploy_prunes_staging_and_unreachable_release_directories(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch, commit="a" * 40)
    manager.deploy()
    first = manager.current_link.readlink()
    monkeypatch.setattr(manager, "_commit", lambda: "b" * 40)
    manager.deploy()
    second = manager.current_link.readlink()

    orphan = manager.releases_dir / "orphan-release"
    orphan.mkdir()
    (orphan / "junk").write_text("junk", encoding="utf-8")
    stale_staging = manager.release_root / ".staging-interrupted"
    stale_staging.mkdir()
    (stale_staging / "junk").write_text("junk", encoding="utf-8")

    monkeypatch.setattr(manager, "_commit", lambda: "c" * 40)
    result = manager.deploy()

    assert manager.previous_link.readlink() == second
    assert manager.current_link.readlink() != second
    assert not (manager.release_root / first).exists()
    assert not orphan.exists()
    assert not stale_staging.exists()
    assert sorted(path.name for path in manager.releases_dir.iterdir()) == sorted([
        Path(manager.current_link.readlink()).name,
        Path(manager.previous_link.readlink()).name,
    ])
    assert ".staging-interrupted" in result["pruned"]
    assert "releases/orphan-release" in result["pruned"]
