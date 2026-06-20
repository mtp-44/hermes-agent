"""Regression guard for the Open Brain query-feedback wiring assertions.

Context (2026-06): the 👍/👎 query-feedback buttons went dead during the
4,510-commit upstream replay because cherry-pick 1fc7e29a4 kept the consumer
side and the ``capture_query_brain_feedback_candidate`` *definition* but
dropped the producer hook in the formatter plugin —
``ctx.register_hook("post_tool_call", _post_tool_call)``. The update guard's
``LOCAL_DELTA_PATTERNS`` only checked that symbols *existed*, so it passed
while the feature was broken.

These tests pin the strengthened contract: the guard must assert the wiring
call itself (not just symbol presence) on both the producer and consumer
sides, and must fail loudly if a future replay drops it.
"""

import shutil
from pathlib import Path

import scripts.hermes_update_guard as guard_mod
from scripts.hermes_update_guard import (
    LOCAL_DELTA_PATHS,
    LOCAL_DELTA_PATTERNS,
    REPO_ROOT,
    HermesUpdateGuard,
)

PLUGIN_REL = "plugins/openbrain-query-brain-format/__init__.py"
HOOK_NEEDLE = 'register_hook("post_tool_call"'
CAPTURE_NEEDLE = "capture_query_brain_feedback_candidate("
HOOK_LINE = 'ctx.register_hook("post_tool_call", _post_tool_call)'


def _make_guard() -> HermesUpdateGuard:
    return HermesUpdateGuard(
        hermes_home=Path("/nonexistent"),
        prod_branch="main",
        allow_dirty=True,
        live_smoke=False,
    )


def _delta_patterns_check(guard: HermesUpdateGuard):
    return next(c for c in guard.checks if c.name == "local-delta-patterns")


def test_patterns_assert_feedback_wiring_not_just_symbols():
    """The producer hook and consumer pop must be required substrings."""
    flat = {(rel, needle) for rel, req in LOCAL_DELTA_PATTERNS for needle in ((req,) if isinstance(req, str) else req)}
    assert (PLUGIN_REL, HOOK_NEEDLE) in flat
    assert (PLUGIN_REL, CAPTURE_NEEDLE) in flat
    assert ("gateway/run.py", "pop_feedback_candidate") in flat


def test_real_repo_passes_feedback_wiring_check():
    """The currently-restored repo satisfies every required marker."""
    guard = _make_guard()
    guard._check_local_delta_surface()
    check = _delta_patterns_check(guard)
    assert check.status == "pass", check.detail


def _mirror_repo_deltas(tmp_path: Path) -> None:
    rels = set(LOCAL_DELTA_PATHS) | {rel for rel, _ in LOCAL_DELTA_PATTERNS}
    for rel in rels:
        src = REPO_ROOT / rel
        if not src.exists():
            continue
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def test_dropped_producer_hook_fails_loudly(tmp_path, monkeypatch):
    """Simulate the replay that omitted the register_hook wiring."""
    _mirror_repo_deltas(tmp_path)
    plugin = tmp_path / PLUGIN_REL
    text = plugin.read_text(encoding="utf-8")
    assert HOOK_LINE in text, "fixture assumption broke: hook line not found in real plugin"
    # Keep the symbols/definition, drop only the wiring — exactly what 1fc7e29a4 did.
    plugin.write_text(text.replace(HOOK_LINE, ""), encoding="utf-8")

    monkeypatch.setattr(guard_mod, "REPO_ROOT", tmp_path)
    guard = _make_guard()
    guard._check_local_delta_surface()
    check = _delta_patterns_check(guard)
    assert check.status == "fail"
    assert HOOK_NEEDLE in check.detail


def test_dropped_consumer_pop_fails_loudly(tmp_path, monkeypatch):
    """Simulate a replay that drops the gateway-side pop_feedback_candidate use."""
    _mirror_repo_deltas(tmp_path)
    run_py = tmp_path / "gateway/run.py"
    text = run_py.read_text(encoding="utf-8")
    run_py.write_text(text.replace("pop_feedback_candidate", "pop_disabled_candidate"), encoding="utf-8")

    monkeypatch.setattr(guard_mod, "REPO_ROOT", tmp_path)
    guard = _make_guard()
    guard._check_local_delta_surface()
    check = _delta_patterns_check(guard)
    assert check.status == "fail"
    assert "gateway/run.py:pop_feedback_candidate" in check.detail
