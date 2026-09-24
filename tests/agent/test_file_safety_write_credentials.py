"""Secret stores under HERMES_HOME must be write-denied.

``get_read_block_error`` refuses every credential store, but the write
denylist had drifted: ``auth/google_oauth.json`` (an OAuth token store) and
the plaintext Bitwarden cache ``cache/bws_cache.json`` were writable through
``write_file`` / ``patch``.

Ported from upstream e7cd1848c9 (vault/, browser-profile/ and
bws_cache.enc.json do not exist in this tree).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agent.file_safety as fs

# Secret material: read-denied AND write-denied.
SECRET_FILES = (
    ".env",
    ".anthropic_oauth.json",
    "auth/google_oauth.json",
    "cache/bws_cache.json",
)


@pytest.fixture()
def hermes_layout(tmp_path, monkeypatch):
    """Profile HERMES_HOME plus a distinct global root, both patched."""
    root = tmp_path / "hermes_root"
    profile = root / "profiles" / "coder"
    profile.mkdir(parents=True)
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: profile)
    monkeypatch.setattr(fs, "_hermes_root_path", lambda: root)
    return root, profile


def _touch(base: Path, rel: str) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy", encoding="utf-8")
    return p


@pytest.mark.parametrize("name", SECRET_FILES)
def test_secret_files_are_write_denied(hermes_layout, name):
    root, profile = hermes_layout
    for base in (profile, root):
        path = _touch(base, name)
        assert fs.is_write_denied(str(path)), f"write allowed: {path}"


def test_nested_secret_basename_stays_writable(hermes_layout):
    _, profile = hermes_layout
    path = _touch(profile, "skills/my-skill/.env.example")
    assert fs.is_write_denied(str(path)) is False


def test_secret_outside_hermes_home_is_not_denied_by_this_rule(hermes_layout, tmp_path):
    project = tmp_path / "myproject"
    path = _touch(project, "cache/bws_cache.json")
    assert fs.is_write_denied(str(path)) is False


def test_write_file_does_not_replace_google_oauth(hermes_layout):
    _, profile = hermes_layout
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations

    target = _touch(profile, "auth/google_oauth.json")
    target.write_text('{"ok": true}\n', encoding="utf-8")
    ops = ShellFileOperations(LocalEnvironment(cwd=str(profile)), cwd=str(profile))
    res = ops.write_file(str(target), '{"pwned": true}\n')
    assert res.error is not None
    assert "protected system/credential file" in res.error
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'


def test_write_file_does_not_replace_bitwarden_cache(hermes_layout):
    _, profile = hermes_layout
    from tools.environments.local import LocalEnvironment
    from tools.file_operations import ShellFileOperations

    target = _touch(profile, "cache/bws_cache.json")
    target.write_text("{}\n", encoding="utf-8")
    ops = ShellFileOperations(LocalEnvironment(cwd=str(profile)), cwd=str(profile))
    res = ops.write_file(str(target), '{"pwned": true}\n')
    assert res.error is not None
    assert "protected system/credential file" in res.error
    assert target.read_text(encoding="utf-8") == "{}\n"
