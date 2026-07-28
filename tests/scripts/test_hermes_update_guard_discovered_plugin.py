"""Regression guard for the *discovered* plugin copy, not the repo file.

Context (WP3, 2026-07-27): the isolated gateway ran a pre-Signal copy of the
query-feedback plugin while this repository's copy was correct. Two mechanisms
combined — plugin discovery derives its bundled root from the installed
``hermes_cli`` package (a shared/symlinked venv can bind that to another
checkout), and ``$HERMES_HOME/plugins`` overrides bundled plugins per plugin.
The deterministic suites passed throughout, because they load the plugin file
by path rather than through discovery.

These tests pin the contract that closes that gap: the guard must resolve the
copy discovery would win with, and must fail when that copy is stale — including
when it is stale *only* in the outbound decorator's platform gate, which every
substring marker in ``LOCAL_DELTA_PATTERNS`` survives.
"""

import shutil
from pathlib import Path

import scripts.hermes_update_guard as guard_mod
from scripts.hermes_update_guard import (
    DISCOVERED_PLUGIN_GATES,
    REPO_ROOT,
    HermesUpdateGuard,
    _gate_platforms,
)

PLUGIN_REL = "plugins/openbrain-query-brain-format/__init__.py"
PLUGIN_DIR = "openbrain-query-brain-format"
GATE_LINE = 'if str(context.get("platform") or "").lower() not in {"telegram", "signal", "desktop"}:'
STALE_GATE_LINE = 'if str(context.get("platform") or "").lower() not in {"telegram", "desktop"}:'


def _make_guard(hermes_home: Path) -> HermesUpdateGuard:
    return HermesUpdateGuard(
        hermes_home=hermes_home,
        prod_branch="main",
        allow_dirty=True,
        live_smoke=False,
    )


def _check(guard: HermesUpdateGuard, name: str):
    return next(c for c in guard.checks if c.name == name)


def _pin_bundled_root(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(
        guard_mod,
        "_resolve_bundled_plugins_dir",
        lambda: (root, "<pinned by test>", ""),
    )


def _copy_plugin_tree(dest_root: Path) -> Path:
    """Copy every plugin the guard inspects into ``dest_root``."""
    for rel, _ in guard_mod.LOCAL_DELTA_PATTERNS:
        if not rel.startswith("plugins/"):
            continue
        src = REPO_ROOT / rel
        if not src.exists():
            continue
        dst = dest_root / Path(rel).relative_to("plugins")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dest_root


def test_gate_parser_reads_the_real_decorator():
    """The parser finds the platform set inside the real outbound decorator."""
    rel, func_name, platform = DISCOVERED_PLUGIN_GATES[0]
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    platforms = _gate_platforms(source, func_name)
    assert platforms is not None, f"{func_name} not found in {rel}"
    assert platform in platforms


def test_gate_parser_returns_none_for_unknown_function():
    assert _gate_platforms("x = 1\n", "_decorate_outbound") is None


def test_gate_parser_returns_none_for_unparseable_source():
    assert _gate_platforms("def broken(:\n", "_decorate_outbound") is None


def test_repo_copy_passes_when_it_is_the_discovered_copy(tmp_path, monkeypatch):
    """With no override and the bundled root pinned here, the repo copy passes."""
    _pin_bundled_root(monkeypatch, REPO_ROOT / "plugins")
    guard = _make_guard(tmp_path / "hermes-home")
    guard._check_discovered_plugin_delta()

    assert _check(guard, "discovered-plugin-root").status == "pass"
    delta = _check(guard, "discovered-plugin-delta")
    assert delta.status == "pass", delta.detail
    assert delta.metadata["markers_checked"] > 0
    assert delta.metadata["overrides"] == {}


def test_foreign_bundled_root_fails_and_names_both_roots(tmp_path, monkeypatch):
    """A venv bound to another checkout must block, not pass quietly."""
    other = _copy_plugin_tree(tmp_path / "other-checkout" / "plugins")
    _pin_bundled_root(monkeypatch, other)
    guard = _make_guard(tmp_path / "hermes-home")
    guard._check_discovered_plugin_delta()

    root = _check(guard, "discovered-plugin-root")
    assert root.status == "fail"
    assert root.blocks
    assert str(other.resolve()) in root.detail
    assert str((REPO_ROOT / "plugins").resolve()) in root.detail
    # The content itself is a faithful copy, so only the root check fails.
    assert _check(guard, "discovered-plugin-delta").status == "pass"


def test_stale_signal_gate_in_discovered_copy_fails(tmp_path, monkeypatch):
    """The exact WP3 failure: every marker present, only the gate is stale."""
    other = _copy_plugin_tree(tmp_path / "other-checkout" / "plugins")
    plugin = other / Path(PLUGIN_REL).relative_to("plugins")
    text = plugin.read_text(encoding="utf-8")
    assert GATE_LINE in text, "fixture assumption broke: gate line not found in real plugin"
    plugin.write_text(text.replace(GATE_LINE, STALE_GATE_LINE), encoding="utf-8")

    _pin_bundled_root(monkeypatch, other)
    guard = _make_guard(tmp_path / "hermes-home")
    guard._check_discovered_plugin_delta()

    delta = _check(guard, "discovered-plugin-delta")
    assert delta.status == "fail"
    assert delta.blocks
    assert "signal" in delta.detail
    assert str(plugin) in delta.detail


def test_user_override_copy_is_the_one_checked(tmp_path, monkeypatch):
    """$HERMES_HOME/plugins wins over the bundled copy, and is what gets checked."""
    hermes_home = tmp_path / "hermes-home"
    override = _copy_plugin_tree(hermes_home / "plugins")
    plugin = override / Path(PLUGIN_REL).relative_to("plugins")
    plugin.write_text(
        plugin.read_text(encoding="utf-8").replace(GATE_LINE, STALE_GATE_LINE),
        encoding="utf-8",
    )

    # The bundled root is this repo and is correct; only the override is stale.
    _pin_bundled_root(monkeypatch, REPO_ROOT / "plugins")
    guard = _make_guard(hermes_home)
    guard._check_discovered_plugin_delta()

    assert _check(guard, "discovered-plugin-root").status == "pass"
    delta = _check(guard, "discovered-plugin-delta")
    assert delta.status == "fail"
    assert str(plugin) in delta.detail
    assert PLUGIN_REL in delta.metadata["overrides"]


def test_healthy_user_override_passes_and_is_reported(tmp_path, monkeypatch):
    """A correct override is not a failure, but it is reported as an override."""
    hermes_home = tmp_path / "hermes-home"
    _copy_plugin_tree(hermes_home / "plugins")

    _pin_bundled_root(monkeypatch, REPO_ROOT / "plugins")
    guard = _make_guard(hermes_home)
    guard._check_discovered_plugin_delta()

    delta = _check(guard, "discovered-plugin-delta")
    assert delta.status == "pass", delta.detail
    assert PLUGIN_REL in delta.metadata["overrides"]
    assert PLUGIN_REL in delta.detail


def test_missing_discovered_file_fails(tmp_path, monkeypatch):
    """An empty bundled root must fail rather than report zero markers as clean."""
    empty = tmp_path / "empty-plugins"
    empty.mkdir()
    _pin_bundled_root(monkeypatch, empty)
    guard = _make_guard(tmp_path / "hermes-home")
    guard._check_discovered_plugin_delta()

    delta = _check(guard, "discovered-plugin-delta")
    assert delta.status == "fail"
    assert "unreadable" in delta.detail


def test_unimportable_hermes_cli_fails(tmp_path, monkeypatch):
    """If discovery cannot be resolved at all, block instead of skipping."""
    monkeypatch.setattr(
        guard_mod,
        "_resolve_bundled_plugins_dir",
        lambda: (None, "", "could not import hermes_cli.plugins: boom"),
    )
    guard = _make_guard(tmp_path / "hermes-home")
    guard._check_discovered_plugin_delta()

    root = _check(guard, "discovered-plugin-root")
    assert root.status == "fail"
    assert root.blocks
    assert "boom" in root.detail
    assert not any(c.name == "discovered-plugin-delta" for c in guard.checks)


def test_discovered_check_runs_in_both_phases(monkeypatch):
    """The gap bites at cutover, so both pre and post must run the check."""
    calls = []
    monkeypatch.setattr(
        HermesUpdateGuard,
        "_check_discovered_plugin_delta",
        lambda self: calls.append(1),
    )
    for method in ("_pre_checks", "_post_checks"):
        calls.clear()
        guard = _make_guard(Path("/nonexistent"))
        for other in (
            "_check_repo_identity",
            "_check_git_state",
            "_check_local_delta_surface",
            "_check_config",
            "_check_ssl_guard_inputs",
            "_check_launch_agents",
            "_check_runtime_gateway",
            "_check_health_monitor",
        ):
            monkeypatch.setattr(HermesUpdateGuard, other, lambda self: None)
        getattr(guard, method)()
        assert calls == [1], f"{method} did not run the discovered-plugin check"
