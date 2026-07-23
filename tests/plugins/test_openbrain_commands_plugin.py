"""Tests for the openbrain-commands plugin (Phase 5c.3).

Verifies the plugin registers the four read-only Open Brain commands and that
each handler formats output faithfully, with gateway.open_brain mocked.
"""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# The plugin package dir name contains a hyphen, so load it by path.
_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "openbrain-commands"
_spec = importlib.util.spec_from_file_location(
    "openbrain_commands_plugin", _PLUGIN_DIR / "__init__.py"
)
obc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(obc)


class _Ctx:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = {"handler": handler, "description": description,
                               "args_hint": args_hint}


def test_register_adds_four_commands():
    ctx = _Ctx()
    obc.register(ctx)
    assert set(ctx.commands) == {"brief", "digest", "stale", "finance-check"}
    for entry in ctx.commands.values():
        assert callable(entry["handler"])


@pytest.mark.asyncio
async def test_brief_lists_and_passes_query():
    with patch("gateway.open_brain.fetch_briefing", new_callable=AsyncMock) as m:
        m.return_value = [
            {"record_type": "session_summary", "created_at": "2026-06-20T10:00:00Z",
             "excerpt": "Shipped seam 1", "citation": "c1"},
        ]
        out = await obc._handle_brief("rollout")
        assert "🧠 **Brief**" in out
        assert "session summary" in out
        assert "Shipped seam 1" in out
        m.assert_awaited_once_with(query="rollout", limit=3)


@pytest.mark.asyncio
async def test_brief_empty_states():
    with patch("gateway.open_brain.fetch_briefing", new_callable=AsyncMock) as m:
        m.return_value = []
        assert "No Hermes captures matched" in await obc._handle_brief("x")
        assert "No recent Hermes captures" in await obc._handle_brief("")


@pytest.mark.asyncio
async def test_digest_formats_sections():
    with patch("gateway.open_brain.fetch_digest", new_callable=AsyncMock) as m:
        m.return_value = {
            "total_items": 3, "meeting_notes": 1, "session_summaries": 2,
            "decisions": [{"text": "Use seams", "reference": " [r1]"}],
            "actions": [{"text": "Migrate cmds"}],
        }
        out = await obc._handle_digest("")
        assert "🧠 **Digest**" in out
        assert "Decisions & outcomes:" in out and "Use seams [r1]" in out
        assert "Open loops:" in out and "Migrate cmds" in out
        m.assert_awaited_once_with(query=None, days=7)


@pytest.mark.asyncio
async def test_stale_clean_and_populated():
    with patch("gateway.open_brain.fetch_stale_items", new_callable=AsyncMock) as m:
        m.return_value = {"stale_actions": [], "stale_contacts": [], "action_days": 14}
        assert "Nothing stale" in await obc._handle_stale("")
        m.return_value = {
            "action_days": 14,
            "stale_actions": [{"age_days": 20, "text": "Follow up", "citation": "c"}],
            "stale_contacts": [],
        }
        out = await obc._handle_stale("")
        assert "🕰️ **Stale**" in out and "20d ago [c]: Follow up" in out


@pytest.mark.asyncio
async def test_finance_check_clean_and_anomalies():
    with patch("gateway.open_brain.fetch_finance_anomalies", new_callable=AsyncMock) as m:
        m.return_value = {"has_anomalies": False, "days": 30, "current_total": 1200}
        assert "No finance anomalies" in await obc._handle_finance_check("")
        m.return_value = {
            "has_anomalies": True, "days": 30, "current_total": 1500, "prior_total": 1000,
            "category_anomalies": [{"category": "Travel", "reason": "+200%"}],
            "large_transactions": [],
        }
        out = await obc._handle_finance_check("")
        assert "💰 **Finance check**" in out and "Travel: +200%" in out
        assert "+50%" in out


# --- Real-manager load rehearsal (Phase 5c.4) -------------------------------
# The tests above use a fake ctx. These exercise the *actual* PluginManager
# discovery + standalone-gating path, encoding the deploy invariant that broke
# on 2026-06-25: this is a ``standalone`` plugin, so removing the core dispatch
# branches (5c.3) means the four commands exist ONLY when ``openbrain-commands``
# is in ``plugins.enabled``. A restart with a config missing that entry silently
# drops /brief /digest /stale /finance-check.

_OB_COMMANDS = {"brief", "digest", "stale", "finance-check"}


def _load_manager_with_enabled(enabled):
    from hermes_cli.plugins import PluginManager

    mgr = PluginManager()
    with patch("hermes_cli.plugins._get_enabled_plugins", return_value=enabled):
        mgr.discover_and_load(force=True)
    return mgr


def test_manager_loads_commands_when_enabled():
    # The bundled standalone plugin must register all four commands once it is
    # listed in plugins.enabled — the exact thing the live config now does.
    mgr = _load_manager_with_enabled({"openbrain-commands"})
    registered = set(mgr._plugin_commands)
    assert _OB_COMMANDS <= registered, (
        f"missing {_OB_COMMANDS - registered}; got {sorted(registered)}"
    )
    for name in _OB_COMMANDS:
        assert mgr._plugin_commands[name].get("plugin") == "openbrain-commands"


def test_manager_skips_commands_when_not_enabled():
    # Regression guard: a config that does NOT enable the plugin must leave the
    # commands unregistered (so this failure mode is caught in CI, not on a live
    # gateway restart).
    mgr = _load_manager_with_enabled(set())
    assert _OB_COMMANDS.isdisjoint(set(mgr._plugin_commands))


# --- Proactive-surface feedback via the generic action seam (5c.3 Stage C) ----

@pytest.mark.asyncio
async def test_proactive_feedback_records_useful(monkeypatch):
    import gateway.open_brain as ob
    seen = {}

    async def _record(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(ob, "record_proactive_feedback", _record)
    msg = await obc._handle_proactive_feedback("prxa", "surface-1", {})
    assert msg == "Marked useful"
    assert seen == {"surface_id": "surface-1", "status": "acted_on"}


@pytest.mark.asyncio
async def test_proactive_feedback_records_dismissed(monkeypatch):
    import gateway.open_brain as ob
    seen = {}

    async def _record(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(ob, "record_proactive_feedback", _record)
    msg = await obc._handle_proactive_feedback("prxd", "surface-2", {})
    assert msg == "Dismissed"
    assert seen == {"surface_id": "surface-2", "status": "dismissed"}


@pytest.mark.asyncio
async def test_proactive_feedback_empty_token():
    assert await obc._handle_proactive_feedback("prxa", "", {}) == "This proactive prompt expired."


def test_register_wires_proactive_action_handlers():
    class RichCtx(_Ctx):
        def __init__(self):
            super().__init__()
            self.action_handlers = {}

        def register_action_handler(self, action_id, handler):
            self.action_handlers[action_id] = handler

    ctx = RichCtx()
    obc.register(ctx)
    assert {"prxa", "prxd", "cdone", "cdrop", "cseen"} <= set(ctx.action_handlers)
    assert ctx.action_handlers["prxa"] is obc._handle_proactive_feedback
    assert ctx.action_handlers["cdone"] is obc._handle_commitment_action
    assert ctx.action_handlers["cdrop"] is obc._handle_commitment_action
    assert ctx.action_handlers["cseen"] is obc._handle_commitment_action


# --- Numbered digest-commitment done/drop/seen buttons (generic action seam) --

@pytest.mark.asyncio
async def test_commitment_action_done(monkeypatch):
    import gateway.open_brain as ob
    seen = {}

    async def _update(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(ob, "update_commitment", _update)
    msg = await obc._handle_commitment_action("cdone", "commit-1", {})
    assert msg == "✅ Marked done"
    assert seen == {"commitment_id": "commit-1", "action": "done"}


@pytest.mark.asyncio
async def test_commitment_action_drop(monkeypatch):
    import gateway.open_brain as ob
    seen = {}

    async def _update(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(ob, "update_commitment", _update)
    msg = await obc._handle_commitment_action("cdrop", "commit-2", {})
    assert msg == "🗑 Dropped"
    assert seen == {"commitment_id": "commit-2", "action": "drop"}


@pytest.mark.asyncio
async def test_commitment_action_seen_snoozes_seven_days(monkeypatch):
    import gateway.open_brain as ob
    seen = {}

    async def _update(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(ob, "update_commitment", _update)
    msg = await obc._handle_commitment_action("cseen", "commit-3", {})
    assert msg == "👀 Snoozed 7 days"
    assert seen == {"commitment_id": "commit-3", "action": "snooze", "snooze_days": 7}


@pytest.mark.asyncio
async def test_commitment_action_empty_token():
    assert await obc._handle_commitment_action("cdone", "", {}) == "This commitment button expired."


@pytest.mark.asyncio
async def test_commitment_action_failure_is_swallowed(monkeypatch):
    import gateway.open_brain as ob

    async def _update(**kwargs):
        raise RuntimeError("mcp down")

    monkeypatch.setattr(ob, "update_commitment", _update)
    msg = await obc._handle_commitment_action("cdone", "commit-4", {})
    assert msg == "⚠️ Couldn't update."
