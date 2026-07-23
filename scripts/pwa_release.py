#!/usr/bin/env python3
"""Atomic release manager for the tailnet PWA appliance."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_RELEASE_ROOT = PROJECT_DIR.parent / "hermes-agent-pwa-releases"
DEFAULT_HEALTH_URLS = (
    "http://127.0.0.1:9219/api/status",
    "https://mini-mh.tailbd0650.ts.net/api/status",
)
STAMP_FILE = "pwa-release.json"
STAMP_PLACEHOLDER = "__HERMES_PWA_BUILD_STAMP__"


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class HealthResult:
    url: str
    ok: bool
    detail: str


class ReleaseManager:
    def __init__(
        self,
        *,
        project_root: Path,
        release_root: Path,
        health_urls: Sequence[str],
        health_attempts: int = 6,
        health_interval_seconds: float = 2.0,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.release_root = release_root.resolve()
        self.releases_dir = self.release_root / "releases"
        self.health_urls = tuple(health_urls)
        self.health_attempts = health_attempts
        self.health_interval_seconds = health_interval_seconds
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    @property
    def current_link(self) -> Path:
        return self.release_root / "current"

    @property
    def previous_link(self) -> Path:
        return self.release_root / "previous"

    @property
    def compatibility_assets_dir(self) -> Path:
        return self.release_root / "compat-assets"

    def _run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        result = subprocess.run(
            list(command),
            cwd=str(cwd or self.project_root),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            raise ReleaseError(f"{' '.join(command)} failed: {detail}")
        return result.stdout.strip()

    def _verify_source(self) -> None:
        status = self._run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=self.project_root,
        )
        # A linked dependency install is allowed in a dedicated worktree. It
        # is runtime scaffolding, not deployable source; every other untracked
        # or modified path still blocks a release.
        dirty = [
            line
            for line in status.splitlines()
            if line not in {"?? node_modules", "?? node_modules/"}
        ]
        if dirty:
            raise ReleaseError("source worktree is not clean; commit or remove local changes before deploying")
        verify_env = os.environ.copy()
        appliance_node = Path("/opt/homebrew/opt/node@22/bin")
        if (appliance_node / "node").is_file():
            verify_env["PATH"] = f"{appliance_node}{os.pathsep}{verify_env.get('PATH', '')}"
        self._run(
            ["npm", "run", "verify:pwa"],
            cwd=self.project_root / "apps" / "desktop",
            env=verify_env,
        )

    def _commit(self) -> str:
        return self._run(["git", "rev-parse", "HEAD"], cwd=self.project_root)

    def _release_id(self, commit: str) -> str:
        timestamp = self.now_fn().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{timestamp}-{commit[:12]}"

    def _read_link(self, link: Path) -> str | None:
        if not link.is_symlink():
            return None
        return os.readlink(link)

    def _replace_link(self, link: Path, target: str | None) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        if target is None:
            if link.is_symlink():
                link.unlink()
            return
        temporary = link.parent / f".{link.name}.{uuid.uuid4().hex}.tmp"
        os.symlink(target, temporary)
        os.replace(temporary, link)

    def _metadata(self, release_id: str, commit: str) -> dict[str, str]:
        return {
            "release_id": release_id,
            "commit": commit,
            "built_at": self.now_fn().astimezone(timezone.utc).isoformat(),
        }

    def _merge_compatibility_assets(self, staging: Path) -> None:
        """Carry immutable hashed assets forward for already-open clients."""
        destination_root = staging / "assets"
        sources = [self.compatibility_assets_dir]
        if self.releases_dir.is_dir():
            sources.extend(
                release / "assets"
                for release in sorted(self.releases_dir.iterdir())
                if release.is_dir()
            )
        for source_root in sources:
            if not source_root.is_dir():
                continue
            for source in source_root.rglob("*"):
                if not source.is_file():
                    continue
                destination = destination_root / source.relative_to(source_root)
                if destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    def _stage_release(self, *, release_id: str, commit: str) -> Path:
        source = self.project_root / "apps" / "desktop" / "dist-pwa"
        if not (source / "index.html").is_file() or not (source / "sw.js").is_file():
            raise ReleaseError(f"verified PWA build is incomplete: {source}")

        self.releases_dir.mkdir(parents=True, exist_ok=True)
        destination = self.releases_dir / release_id
        if destination.exists():
            raise ReleaseError(f"release already exists: {release_id}")
        staging = self.release_root / f".staging-{release_id}-{uuid.uuid4().hex[:8]}"

        try:
            shutil.copytree(source, staging)
            self._merge_compatibility_assets(staging)
            metadata = self._metadata(release_id, commit)
            (staging / STAMP_FILE).write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            sw_path = staging / "sw.js"
            sw_source = sw_path.read_text(encoding="utf-8")
            if STAMP_PLACEHOLDER not in sw_source:
                raise ReleaseError(f"{sw_path} does not contain the build-stamp placeholder")
            sw_path.write_text(sw_source.replace(STAMP_PLACEHOLDER, release_id), encoding="utf-8")

            index_path = staging / "index.html"
            html = index_path.read_text(encoding="utf-8")
            marker = f'<meta name="hermes-pwa-build" content="{release_id}">'
            if "</head>" not in html:
                raise ReleaseError(f"{index_path} has no </head> marker")
            index_path.write_text(html.replace("</head>", f"{marker}</head>", 1), encoding="utf-8")

            staging.rename(destination)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return destination

    def _probe(self, url: str) -> HealthResult:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                status = response.getcode()
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            return HealthResult(url, False, f"{type(exc).__name__}: {exc}")
        if status != 200:
            return HealthResult(url, False, f"HTTP {status}")
        if not isinstance(payload, dict) or "version" not in payload:
            return HealthResult(url, False, "status response missing version")
        return HealthResult(url, True, f"HTTP {status}, Hermes {payload['version']}")

    def healthcheck(self) -> list[HealthResult]:
        if not self.health_urls:
            return []
        latest: list[HealthResult] = []
        for attempt in range(self.health_attempts):
            latest = [self._probe(url) for url in self.health_urls]
            if all(result.ok for result in latest):
                return latest
            if attempt + 1 < self.health_attempts:
                time.sleep(self.health_interval_seconds)
        return latest

    def _require_healthy(self) -> list[HealthResult]:
        results = self.healthcheck()
        failed = [result for result in results if not result.ok]
        if failed:
            details = "; ".join(f"{result.url}: {result.detail}" for result in failed)
            raise ReleaseError(f"release health check failed: {details}")
        return results

    def deploy(self, *, verify: bool = True) -> dict[str, object]:
        if verify:
            self._verify_source()
        commit = self._commit()
        release_id = self._release_id(commit)
        destination = self._stage_release(release_id=release_id, commit=commit)
        target = str(destination.relative_to(self.release_root))
        old_current = self._read_link(self.current_link)
        old_previous = self._read_link(self.previous_link)

        if old_current:
            self._replace_link(self.previous_link, old_current)
        self._replace_link(self.current_link, target)

        try:
            health = self._require_healthy()
        except Exception:
            self._replace_link(self.current_link, old_current)
            self._replace_link(self.previous_link, old_previous)
            raise

        return {
            "action": "deploy",
            "release_id": release_id,
            "commit": commit,
            "current": target,
            "previous": old_current,
            "health": [result.__dict__ for result in health],
        }

    def rollback(self) -> dict[str, object]:
        old_current = self._read_link(self.current_link)
        old_previous = self._read_link(self.previous_link)
        if not old_current or not old_previous:
            raise ReleaseError("rollback requires both current and previous releases")
        self._replace_link(self.current_link, old_previous)
        self._replace_link(self.previous_link, old_current)
        try:
            health = self._require_healthy()
        except Exception:
            self._replace_link(self.current_link, old_current)
            self._replace_link(self.previous_link, old_previous)
            raise
        return {
            "action": "rollback",
            "current": old_previous,
            "previous": old_current,
            "health": [result.__dict__ for result in health],
        }

    def status(self) -> dict[str, object]:
        current = self._read_link(self.current_link)
        previous = self._read_link(self.previous_link)
        metadata: dict[str, str] | None = None
        if current:
            stamp_path = self.release_root / current / STAMP_FILE
            if stamp_path.is_file():
                metadata = json.loads(stamp_path.read_text(encoding="utf-8"))
        health = self.healthcheck()
        ok = bool(current and metadata and all(result.ok for result in health))
        return {
            "action": "status",
            "ok": ok,
            "release_root": str(self.release_root),
            "current": current,
            "previous": previous,
            "metadata": metadata,
            "health": [result.__dict__ for result in health],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy or roll back the Hermes tailnet PWA atomically.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--deploy", action="store_true")
    action.add_argument("--rollback", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--project-root", type=Path, default=PROJECT_DIR)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--health-url", action="append", dest="health_urls")
    parser.add_argument("--health-attempts", type=int, default=6)
    parser.add_argument("--health-interval-seconds", type=float, default=2.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manager = ReleaseManager(
        project_root=args.project_root,
        release_root=args.release_root,
        health_urls=args.health_urls or DEFAULT_HEALTH_URLS,
        health_attempts=args.health_attempts,
        health_interval_seconds=args.health_interval_seconds,
    )
    try:
        if args.deploy:
            result = manager.deploy()
        elif args.rollback:
            result = manager.rollback()
        else:
            result = manager.status()
    except ReleaseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
