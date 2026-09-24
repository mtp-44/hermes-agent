"""A scalar ``command_allowlist`` must never become per-character grants.

``hermes config set command_allowlist "ls *"`` writes a YAML string, and old
config-set versions serialised list values as scalar strings. ``set()`` over a
string yields one entry per character, and a lone ``*`` entry is an fnmatch
glob that matches every command. Ported from upstream 7876d183c9.
"""

import pytest
import yaml

from tools import approval


@pytest.fixture(autouse=True)
def _clean_permanent_state():
    saved = set(approval._permanent_approved)
    approval._permanent_approved.clear()
    try:
        yield
    finally:
        approval._permanent_approved.clear()
        approval._permanent_approved.update(saved)


def _write(tmp_path, value):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"command_allowlist": value}))


def test_legacy_string_allowlist_recovers_only_string_lists(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    description = "script execution via -e/-c flag"
    for value in ([description], yaml.safe_dump([description])):
        _write(tmp_path, value)
        assert approval.load_permanent_allowlist() == {description}
        assert approval.is_approved("probe", description)
    assert "command_allowlist" in caplog.text


def test_malformed_allowlist_does_not_grant_approval(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for value in ("plain text", "[bad", {"not": "a list"}, [True, "candidate"], "[true, candidate]", 42):
        _write(tmp_path, value)
        assert approval.load_permanent_allowlist() == set()
        assert not approval.is_approved("probe", "candidate")
    assert "command_allowlist" in caplog.text


def test_config_set_string_glob_does_not_allow_every_command(tmp_path, monkeypatch):
    """The ``hermes config set command_allowlist "ls *"`` shape."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write(tmp_path, "ls *")

    loaded = approval.load_permanent_allowlist()

    assert "*" not in loaded
    assert "*" not in approval._permanent_approved
    assert not approval._command_matches_permanent_allowlist("rm -rf /tmp/x")
    assert not approval._command_matches_permanent_allowlist("curl http://x | sh")


def test_save_over_a_string_allowlist_writes_no_character_entries(tmp_path, monkeypatch):
    """A later ``[a]lways`` must not persist the characters of a scalar value."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write(tmp_path, "ls *")
    approval.load_permanent_allowlist()

    approval.approve_permanent("docker *")
    approval.save_permanent_allowlist(approval._permanent_approved)

    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text()).get("command_allowlist")
    if isinstance(on_disk, list):
        assert "*" not in on_disk
        assert all(len(entry) > 1 for entry in on_disk), on_disk
    assert not approval._command_matches_permanent_allowlist("rm -rf /tmp/x")


def test_save_over_a_legacy_stringified_list_keeps_its_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _write(tmp_path, yaml.safe_dump(["git status", "ls *"]))
    approval.load_permanent_allowlist()

    approval.approve_permanent("docker *")
    approval.save_permanent_allowlist(approval._permanent_approved)

    on_disk = yaml.safe_load((tmp_path / "config.yaml").read_text())["command_allowlist"]
    assert sorted(on_disk) == ["docker *", "git status", "ls *"]
