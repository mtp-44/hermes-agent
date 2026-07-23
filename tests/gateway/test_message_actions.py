"""Tests for the generic platform message-action seam (Phase 5c seam 3).

Covers the pure wire-format module and the Telegram adapter's generic
render/dispatch wiring (without a live bot).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.platforms import actions as A


# --- Pure module --------------------------------------------------------------

def test_encode_decode_roundtrip():
    cb = A.encode_action_callback("good", "tok1")
    assert cb == "act:good:tok1"
    assert A.decode_action_callback(cb) == ("good", "tok1")
    assert A.is_action_callback(cb)


def test_decode_rejects_other_families():
    assert A.decode_action_callback("obf:g:x") is None
    assert A.decode_action_callback("prx:useful:x") is None
    assert A.decode_action_callback("") is None
    assert A.decode_action_callback("act:") is None  # empty action_id


def test_encode_rejects_colon_and_oversize():
    with pytest.raises(ValueError):
        A.encode_action_callback("a:b", "t")
    with pytest.raises(ValueError):
        A.encode_action_callback("", "t")
    with pytest.raises(ValueError):
        A.encode_action_callback("x", "y" * 80)  # exceeds 64-byte budget


def test_empty_token_is_allowed():
    assert A.encode_action_callback("ack") == "act:ack:"
    assert A.decode_action_callback("act:ack:") == ("ack", "")


def test_action_rows_from_metadata_flat_and_rows():
    flat = {"actions": [
        {"label": "👍", "action_id": "good", "token": "t"},
        {"label": "👎", "action_id": "bad", "token": "t"},
    ]}
    rows = A.action_rows_from_metadata(flat)
    assert len(rows) == 1 and len(rows[0]) == 2
    assert rows[0][0].callback_id() == "act:good:t"

    multi = {"action_rows": [
        [{"label": "A", "action_id": "a"}],
        [{"label": "B", "action_id": "b"}],
    ]}
    assert len(A.action_rows_from_metadata(multi)) == 2


def test_action_rows_drops_malformed_and_empty():
    assert A.action_rows_from_metadata(None) == []
    assert A.action_rows_from_metadata({}) == []
    # Missing label/action_id entries are dropped, leaving no rows.
    assert A.action_rows_from_metadata({"actions": [{"label": "x"}]}) == []


# --- Telegram wiring ----------------------------------------------------------

def _adapter():
    """A TelegramAdapter instance with only the attributes the seam touches.

    Deliberately avoids ``check_telegram_requirements()`` (which rebinds the
    module's telegram globals and would leak the real telegram package into
    sibling test files that rely on the gateway conftest stub). The markup test
    monkeypatches the keyboard classes; the dispatch tests construct none.
    """
    from plugins.platforms.telegram.adapter import TelegramAdapter
    from gateway.platforms.base import Platform

    a = object.__new__(TelegramAdapter)
    a.platform = Platform.TELEGRAM  # backs the read-only ``name`` property
    a._action_handler = None
    # Action presses are authorized like other gated callbacks; default the
    # dispatch tests to an authorized caller (a dedicated test covers deny).
    a._is_callback_user_authorized = lambda *args, **kwargs: True
    return a


def test_markup_builds_from_actions_metadata(monkeypatch):
    # The gateway test harness stubs the ``telegram`` module, so capture the
    # keyboard the builder assembles with simple fakes rather than relying on
    # the real InlineKeyboardMarkup internals.
    import plugins.platforms.telegram.adapter as tg

    class _FakeBtn:
        def __init__(self, label, callback_data=None):
            self.label = label
            self.callback_data = callback_data

    class _FakeMarkup:
        def __init__(self, keyboard):
            self.inline_keyboard = keyboard

    a = _adapter()  # rebinds the telegram globals; patch *after* it
    monkeypatch.setattr(tg, "InlineKeyboardButton", _FakeBtn)
    monkeypatch.setattr(tg, "InlineKeyboardMarkup", _FakeMarkup)

    md = {"actions": [
        {"label": "👍 Good", "action_id": "good", "token": "tok"},
        {"label": "👎 Bad", "action_id": "bad", "token": "tok"},
    ]}
    markup = a._actions_markup_from_metadata(md)
    assert markup is not None
    buttons = markup.inline_keyboard[0]
    assert [b.callback_data for b in buttons] == ["act:good:tok", "act:bad:tok"]
    assert [b.label for b in buttons] == ["👍 Good", "👎 Bad"]


def test_markup_none_when_no_actions_or_disabled():
    a = _adapter()
    assert a._actions_markup_from_metadata({"open_brain_feedback": {}}) is None
    assert a._actions_markup_from_metadata({"actions": [{"label": "x", "action_id": "y"}]}, enabled=False) is None


@pytest.mark.asyncio
async def test_dispatch_action_invokes_handler_and_answers():
    a = _adapter()
    seen = {}

    async def handler(action_id, token, ctx):
        seen["call"] = (action_id, token, ctx["user_id"])
        return "Thanks!"

    a.set_action_handler(handler)
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat_id=42, message_thread_id=None),
        from_user=SimpleNamespace(id=7),
    )

    handled = await a._dispatch_action_callback(query, "act:good:tok9")

    assert handled is True
    assert seen["call"] == ("good", "tok9", "7")
    query.answer.assert_awaited_once_with(text="Thanks!")


@pytest.mark.asyncio
async def test_dispatch_clears_only_pressed_row_in_multi_row_message(monkeypatch):
    # A digest message with one row per numbered commitment: pressing item #2's
    # button must leave items #1 and #3 actionable, not wipe the whole keyboard.
    import plugins.platforms.telegram.adapter as tg

    class _FakeBtn:
        def __init__(self, label, callback_data=None):
            self.label = label
            self.callback_data = callback_data

    class _FakeMarkup:
        def __init__(self, keyboard):
            self.inline_keyboard = keyboard

    monkeypatch.setattr(tg, "InlineKeyboardMarkup", _FakeMarkup)

    a = _adapter()
    a.set_action_handler(AsyncMock(return_value="done"))

    existing_markup = _FakeMarkup([
        [_FakeBtn("1 ✅", "act:cdone:1")],
        [_FakeBtn("2 ✅", "act:cdone:2")],
        [_FakeBtn("3 ✅", "act:cdone:3")],
    ])
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat_id=1, message_thread_id=None, reply_markup=existing_markup),
        from_user=SimpleNamespace(id=1),
        edit_message_reply_markup=AsyncMock(),
    )

    handled = await a._dispatch_action_callback(query, "act:cdone:2")

    assert handled is True
    remaining = query.edit_message_reply_markup.await_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].callback_data for row in remaining] == ["act:cdone:1", "act:cdone:3"]


@pytest.mark.asyncio
async def test_dispatch_clears_entire_markup_when_last_row_pressed(monkeypatch):
    # Single-row messages (proactive ✅/🙈, query 👍/👎) must behave exactly as
    # before: pressing their only row clears the keyboard entirely.
    import plugins.platforms.telegram.adapter as tg

    class _FakeBtn:
        def __init__(self, label, callback_data=None):
            self.label = label
            self.callback_data = callback_data

    class _FakeMarkup:
        def __init__(self, keyboard):
            self.inline_keyboard = keyboard

    monkeypatch.setattr(tg, "InlineKeyboardMarkup", _FakeMarkup)

    a = _adapter()
    a.set_action_handler(AsyncMock(return_value="ok"))

    existing_markup = _FakeMarkup([[_FakeBtn("👍", "act:good:tok9")]])
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat_id=1, message_thread_id=None, reply_markup=existing_markup),
        from_user=SimpleNamespace(id=1),
        edit_message_reply_markup=AsyncMock(),
    )

    await a._dispatch_action_callback(query, "act:good:tok9")

    assert query.edit_message_reply_markup.await_args.kwargs["reply_markup"] is None


def test_markup_without_row_handles_missing_markup():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    assert TelegramAdapter._markup_without_row(SimpleNamespace(reply_markup=None), "act:x:1") is None
    assert TelegramAdapter._markup_without_row(SimpleNamespace(), "act:x:1") is None


@pytest.mark.asyncio
async def test_dispatch_denies_unauthorized_press():
    a = _adapter()
    a._is_callback_user_authorized = lambda *args, **kwargs: False
    handler = AsyncMock()
    a.set_action_handler(handler)
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat_id=1, chat=None, message_thread_id=None),
        from_user=SimpleNamespace(id=666, first_name="x"),
    )
    handled = await a._dispatch_action_callback(query, "act:good:tok")
    assert handled is True  # it was an action callback, just denied
    handler.assert_not_called()
    query.answer.assert_awaited_once()
    assert "authorized" in (query.answer.await_args.kwargs.get("text") or "").lower()


@pytest.mark.asyncio
async def test_dispatch_ignores_non_action_callbacks():
    a = _adapter()
    a.set_action_handler(AsyncMock())
    query = SimpleNamespace(answer=AsyncMock())
    assert await a._dispatch_action_callback(query, "obf:g:x") is False
    query.answer.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_without_handler_just_acks():
    a = _adapter()  # no handler registered
    query = SimpleNamespace(answer=AsyncMock(), message=None, from_user=SimpleNamespace(id=1))
    assert await a._dispatch_action_callback(query, "act:good:t") is True
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_dispatch_swallows_handler_error():
    a = _adapter()

    async def boom(action_id, token, ctx):
        raise RuntimeError("nope")

    a.set_action_handler(boom)
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat_id=1, message_thread_id=None),
        from_user=SimpleNamespace(id=1),
    )
    assert await a._dispatch_action_callback(query, "act:good:t") is True
    query.answer.assert_awaited_once_with()


# --- Plugin registration seam (Phase 5c.3 step 2 / Stage A) -------------------

def _plugin_ctx():
    from hermes_cli.plugins import PluginManager, PluginContext, PluginManifest

    mgr = PluginManager()
    ctx = PluginContext(PluginManifest(name="test-adapter", path=None), mgr)
    return mgr, ctx


def test_register_action_handler_and_lookup():
    mgr, ctx = _plugin_ctx()

    def h(action_id, token, c):
        return "ok"

    ctx.register_action_handler("obg", h)
    assert mgr.get_action_handler("obg") is h
    assert mgr.get_action_handler("missing") is None


def test_register_action_handler_validates():
    _mgr, ctx = _plugin_ctx()
    with pytest.raises(ValueError):
        ctx.register_action_handler("obg", "not-callable")
    with pytest.raises(ValueError):
        ctx.register_action_handler("", lambda *a: None)
    with pytest.raises(ValueError):
        ctx.register_action_handler("has:colon", lambda *a: None)


def test_register_outbound_decorator_and_list():
    mgr, ctx = _plugin_ctx()

    def deco(context):
        return [{"label": "👍", "action_id": "obg", "token": "t"}]

    ctx.register_outbound_decorator(deco)
    decos = mgr.get_outbound_decorators()
    assert decos == [deco]
    assert decos[0]({}) == [{"label": "👍", "action_id": "obg", "token": "t"}]
    with pytest.raises(ValueError):
        ctx.register_outbound_decorator("nope")


@pytest.mark.asyncio
async def test_dispatch_routes_to_plugin_handler(monkeypatch):
    # A plugin-registered handler for the action_id takes precedence over the
    # single set_action_handler fallback.
    import plugins.platforms.telegram.adapter as tg

    seen = {}

    def plugin_handler(action_id, token, ctx):
        seen["plugin"] = (action_id, token)
        return "from-plugin"

    class _FakeMgr:
        def get_action_handler(self, action_id):
            return plugin_handler if action_id == "obg" else None

    monkeypatch.setattr(tg, "get_plugin_manager", lambda: _FakeMgr(), raising=False)
    # Also patch the import site used inside _dispatch_action_callback.
    import hermes_cli.plugins as plugmod
    monkeypatch.setattr(plugmod, "get_plugin_manager", lambda: _FakeMgr())

    a = _adapter()
    a.set_action_handler(AsyncMock(return_value="from-setter"))
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(chat_id=1, message_thread_id=None),
        from_user=SimpleNamespace(id=9),
    )
    handled = await a._dispatch_action_callback(query, "act:obg:tok")
    assert handled is True
    assert seen["plugin"] == ("obg", "tok")
    query.answer.assert_awaited_once_with(text="from-plugin")


# --- Generic stage/pop/attach actions (Stage A) -------------------------------

def test_stage_and_pop_actions_generation_guarded():
    a = _adapter()
    a._staged_actions = {}
    acts = [{"label": "👍", "action_id": "obg", "token": "t"}]
    a.stage_actions("sess1", acts, generation=3)
    # Wrong generation does not pop.
    assert a.pop_staged_actions("sess1", generation=2) is None
    # Right generation pops once.
    assert a.pop_staged_actions("sess1", generation=3) == acts
    assert a.pop_staged_actions("sess1", generation=3) is None


def test_stage_actions_ignores_empty():
    a = _adapter()
    a._staged_actions = {}
    a.stage_actions("s", [], generation=1)
    a.stage_actions("", [{"label": "x", "action_id": "y"}])
    assert a._staged_actions == {}


@pytest.mark.asyncio
async def test_attach_actions_edits_markup(monkeypatch):
    import plugins.platforms.telegram.adapter as tg

    class _FakeBtn:
        def __init__(self, label, callback_data=None):
            self.label = label
            self.callback_data = callback_data

    class _FakeMarkup:
        def __init__(self, keyboard):
            self.inline_keyboard = keyboard

    a = _adapter()
    monkeypatch.setattr(tg, "InlineKeyboardButton", _FakeBtn)
    monkeypatch.setattr(tg, "InlineKeyboardMarkup", _FakeMarkup)
    a._bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())

    ok = await a.attach_actions("100", "200", [
        {"label": "👍", "action_id": "obg", "token": "tk"},
    ])
    assert ok is True
    kwargs = a._bot.edit_message_reply_markup.await_args.kwargs
    assert kwargs["chat_id"] == 100 and kwargs["message_id"] == 200
    assert kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "act:obg:tk"


@pytest.mark.asyncio
async def test_attach_actions_noop_without_actions():
    a = _adapter()
    a._bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
    assert await a.attach_actions("1", "2", []) is False
    a._bot.edit_message_reply_markup.assert_not_called()
