"""Tests for the openbrain-sessions plugin (Phase 5d).

Verifies the plugin registers /sessions and /session, that the subcommand
dispatch maps to the right gateway.open_brain session_* wrappers, and that the
current surface (client/user) is read from the session context. The hosted MCP
layer is mocked.
"""

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "openbrain-sessions"
_spec = importlib.util.spec_from_file_location(
    "openbrain_sessions_plugin", _PLUGIN_DIR / "__init__.py"
)
obs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(obs)


class _Ctx:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = {"handler": handler, "description": description, "args_hint": args_hint}


def test_register_adds_two_commands():
    ctx = _Ctx()
    obs.register(ctx)
    assert set(ctx.commands) == {"sessions", "session"}
    for entry in ctx.commands.values():
        assert callable(entry["handler"])


@pytest.mark.asyncio
async def test_sessions_lists_and_passes_status():
    with patch("gateway.open_brain.session_list", new_callable=AsyncMock) as m:
        m.return_value = [
            {"slug": "multi-ai", "title": "Multi-AI", "status": "active",
             "pinned": True, "updated_at": "2026-06-30T10:00:00Z"},
        ]
        out = await obs._handle_sessions("")
        assert "🧵 **Work sessions**" in out
        assert "`multi-ai`" in out
        assert "★ " in out
        m.assert_awaited_once_with(status="active", limit=20)


@pytest.mark.asyncio
async def test_sessions_all_maps_to_none_status():
    with patch("gateway.open_brain.session_list", new_callable=AsyncMock) as m:
        m.return_value = []
        out = await obs._handle_sessions("all")
        assert "No any work sessions." in out
        m.assert_awaited_once_with(status=None, limit=20)


@pytest.mark.asyncio
async def test_sessions_rejects_bad_status():
    out = await obs._handle_sessions("bogus")
    assert out.startswith("Usage: /sessions")


@pytest.mark.asyncio
async def test_session_new_creates_with_surface():
    with patch("gateway.open_brain.session_create", new_callable=AsyncMock) as m, \
         patch.object(obs, "_surface", return_value=("api_server", "mark")):
        m.return_value = {"slug": "ship-it", "status": "active", "goal": "ship"}
        out = await obs._handle_session("new Ship It")
        assert "Created and resumed:" in out
        assert "[session: ship-it]" in out
        m.assert_awaited_once_with(title="Ship It", client="api_server", user_id="mark")


@pytest.mark.asyncio
async def test_session_new_requires_title():
    assert await obs._handle_session("new") == "Usage: /session new <title>"


@pytest.mark.asyncio
async def test_session_resume_renders_checkpoint():
    with patch("gateway.open_brain.session_resume", new_callable=AsyncMock) as m, \
         patch.object(obs, "_surface", return_value=("telegram", "mark")):
        m.return_value = {
            "session": {"slug": "multi-ai", "status": "active", "goal": "route work"},
            "checkpoint": {"summary": "wired plugin", "next_action": "verify on desktop"},
        }
        out = await obs._handle_session("resume multi-ai")
        assert "Resumed:" in out
        assert "Current state: wired plugin" in out
        assert "Next: verify on desktop" in out
        m.assert_awaited_once_with(session_ref="multi-ai", client="telegram", user_id="mark")


@pytest.mark.asyncio
async def test_session_status_no_current():
    with patch("gateway.open_brain.session_current", new_callable=AsyncMock) as m:
        m.return_value = {"session": None, "checkpoint": None}
        out = await obs._handle_session("status")
        assert "No current work session" in out


@pytest.mark.asyncio
async def test_session_checkpoint_uses_current_session():
    with patch("gateway.open_brain.session_current", new_callable=AsyncMock) as cur, \
         patch("gateway.open_brain.session_checkpoint", new_callable=AsyncMock) as ck, \
         patch.object(obs, "_surface", return_value=("telegram", "mark")):
        cur.return_value = {"session": {"slug": "multi-ai", "status": "active"}, "checkpoint": None}
        ck.return_value = {"summary": "did a thing"}
        out = await obs._handle_session("checkpoint did a thing")
        assert "Checkpoint saved for `multi-ai`" in out
        ck.assert_awaited_once_with(session_ref="multi-ai", summary="did a thing", source="telegram")


@pytest.mark.asyncio
async def test_session_checkpoint_requires_current():
    with patch("gateway.open_brain.session_current", new_callable=AsyncMock) as cur:
        cur.return_value = {"session": None}
        out = await obs._handle_session("checkpoint something")
        assert "No current work session" in out


@pytest.mark.asyncio
async def test_session_pin_and_archive():
    with patch("gateway.open_brain.session_set_pinned", new_callable=AsyncMock) as pin, \
         patch("gateway.open_brain.session_set_status", new_callable=AsyncMock) as st:
        pin.return_value = {"slug": "multi-ai"}
        st.return_value = {"slug": "multi-ai"}
        assert "Pinned `multi-ai`." == await obs._handle_session("pin multi-ai")
        assert "Archived `multi-ai`." == await obs._handle_session("archive multi-ai")
        pin.assert_awaited_once_with(session_ref="multi-ai", pinned=True)
        st.assert_awaited_once_with(session_ref="multi-ai", status="archived")


@pytest.mark.asyncio
async def test_session_no_args_shows_usage():
    assert await obs._handle_session("") == obs._SESSION_USAGE


@pytest.mark.asyncio
async def test_session_error_is_surfaced():
    with patch("gateway.open_brain.session_resume", new_callable=AsyncMock) as m:
        m.side_effect = RuntimeError("Unknown work session: nope")
        out = await obs._handle_session("resume nope")
        assert "Unknown work session: nope" in out
