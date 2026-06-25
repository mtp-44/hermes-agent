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
