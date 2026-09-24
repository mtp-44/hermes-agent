"""Approval taps are bound to the prompt they were shown with.

Gateway approvals queue per session.  ``resolve_gateway_approval`` without an
id pops the OLDEST entry (FIFO) — right for a typed ``/approve``, wrong for a
button: a late tap on an expired prompt used to approve whatever newer prompt
was pending, and tapping the second of two prompts approved the first.  Every
queued entry now carries a ``request_id`` that button surfaces (Telegram,
the PWA's ``approval.respond``) resolve by.

Also covers the lock-commit race: the resolver commits ``entry.result``
under ``_lock`` and the waiter reads it under the same lock when it leaves
the queue, so a choice acked to the user can never be reported as a timeout.
"""

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from tests.gateway.test_telegram_approval_buttons import _make_adapter  # noqa: E402
from tools import approval as mod  # noqa: E402

SESSION = "agent:main:telegram:dm:12345"


@pytest.fixture(autouse=True)
def _clean_queues():
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()
    yield
    # Wake anything a failing test left blocked so threads can exit.
    for entries in list(mod._gateway_queues.values()):
        for entry in entries:
            entry.event.set()
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()


def _set_timeout(monkeypatch, seconds):
    monkeypatch.setattr(
        mod, "_get_approval_config",
        lambda: {"mode": "manual", "gateway_timeout": seconds, "timeout": seconds},
    )


class _Waiter:
    """Run ``_await_gateway_decision`` for one command on a thread."""

    def __init__(self, command, session_key=SESSION):
        self.command = command
        self.session_key = session_key
        self.notified = threading.Event()
        self.data = None
        self.decision = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _notify(self, data):
        self.data = data
        self.notified.set()

    def _run(self):
        self.decision = mod._await_gateway_decision(
            self.session_key, self._notify,
            {"command": self.command, "description": "test",
             "pattern_key": "k", "pattern_keys": ["k"]},
        )

    def start(self):
        self.thread.start()
        assert self.notified.wait(5), f"{self.command!r} was never notified"
        return self

    @property
    def request_id(self):
        return self.data["request_id"]

    def join(self, timeout=5):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), f"{self.command!r} wait did not return"
        return self.decision


def _pending_commands(session_key=SESSION):
    return [e.data["command"] for e in mod._gateway_queues.get(session_key, [])]


# ---------------------------------------------------------------------------
# Core queue / resolve
# ---------------------------------------------------------------------------

class TestRequestIdCore:
    def test_each_queued_approval_gets_a_unique_request_id(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        a = _Waiter("rm -rf /a").start()
        b = _Waiter("rm -rf /b").start()
        assert a.request_id and b.request_id
        assert a.request_id != b.request_id
        assert mod.is_gateway_approval_pending(SESSION, a.request_id)
        assert mod.pending_gateway_approval_count(SESSION) == 2
        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        a.join(), b.join()

    def test_late_tap_on_expired_prompt_resolves_nothing(self, monkeypatch):
        _set_timeout(monkeypatch, 1)
        a = _Waiter("rm -rf /expired").start()
        assert a.join()["resolved"] is False  # timed out, entry dropped
        assert not mod.is_gateway_approval_pending(SESSION, a.request_id)

        _set_timeout(monkeypatch, 30)
        b = _Waiter("rm -rf /newer").start()

        # The late tap carries A's id: nothing may be resolved in its place.
        assert mod.resolve_gateway_approval(SESSION, "once", request_id=a.request_id) == 0
        assert _pending_commands() == ["rm -rf /newer"]
        assert b.thread.is_alive()

        assert mod.resolve_gateway_approval(SESSION, "deny", request_id=b.request_id) == 1
        assert b.join()["choice"] == "deny"

    def test_tapping_second_of_two_prompts_resolves_only_the_second(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        first = _Waiter("rm -rf /first").start()
        second = _Waiter("rm -rf /second").start()

        assert mod.resolve_gateway_approval(SESSION, "once", request_id=second.request_id) == 1
        d = second.join()
        assert d["resolved"] is True and d["choice"] == "once"
        assert _pending_commands() == ["rm -rf /first"]
        assert first.thread.is_alive()

        mod.resolve_gateway_approval(SESSION, "deny", request_id=first.request_id)
        assert first.join()["choice"] == "deny"

    def test_request_id_is_scoped_to_its_session(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        other = _Waiter("rm -rf /other", session_key="other-session").start()
        assert mod.resolve_gateway_approval(SESSION, "once", request_id=other.request_id) == 0
        assert _pending_commands("other-session") == ["rm -rf /other"]
        mod.resolve_gateway_approval("other-session", "deny", resolve_all=True)
        other.join()

    def test_fifo_without_request_id_resolves_oldest(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        first = _Waiter("rm -rf /first").start()
        second = _Waiter("rm -rf /second").start()

        assert mod.resolve_gateway_approval(SESSION, "once") == 1
        assert first.join()["choice"] == "once"
        assert _pending_commands() == ["rm -rf /second"]

        mod.resolve_gateway_approval(SESSION, "deny")
        assert second.join()["choice"] == "deny"

    def test_approve_all_resolves_every_pending(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        waiters = [_Waiter(f"rm -rf /{i}").start() for i in range(3)]
        assert mod.resolve_gateway_approval(SESSION, "session", resolve_all=True) == 3
        for w in waiters:
            d = w.join()
            assert d["resolved"] is True and d["choice"] == "session"
        assert SESSION not in mod._gateway_queues


class TestLockCommit:
    def test_resolve_commits_result_while_holding_the_lock(self, monkeypatch):
        """Every outcome write in resolve happens inside ``_lock``."""

        class _TrackingLock:
            def __init__(self):
                self._lock = threading.Lock()
                self.held = False

            def __enter__(self):
                self._lock.acquire()
                self.held = True
                return self

            def __exit__(self, *exc):
                self.held = False
                self._lock.release()

        lock = _TrackingLock()
        monkeypatch.setattr(mod, "_lock", lock)
        writes = []

        class _Event:
            def set(self):
                writes.append(("event", lock.held))

        class _Entry:
            data = {"request_id": "r1"}
            event = _Event()

            def __setattr__(self, name, value):
                writes.append((name, lock.held))
                object.__setattr__(self, name, value)

        mod._gateway_queues[SESSION] = [_Entry()]
        assert mod.resolve_gateway_approval(SESSION, "deny", reason="no") == 1
        assert writes, "resolve wrote nothing"
        assert all(held for _, held in writes), writes

    def test_choice_committed_after_deadline_is_an_answer_not_a_timeout(self, monkeypatch):
        """A resolve that lands after the waiter's deadline check but before
        it leaves the queue was counted (and acked to the user); the waiter
        must honour it instead of reporting a timeout."""
        _set_timeout(monkeypatch, 0)  # loop breaks on the deadline immediately
        counts = []

        def _notify(data):
            # The user answers before the waiter reaches its drop.
            counts.append(mod.resolve_gateway_approval(SESSION, "once"))

        d = mod._await_gateway_decision(
            SESSION, _notify,
            {"command": "rm -rf /x", "description": "t",
             "pattern_key": "k", "pattern_keys": ["k"]},
        )
        assert counts == [1]
        assert d["resolved"] is True
        assert d["choice"] == "once"

    def test_resolve_after_waiter_left_reports_nothing_pending(self, monkeypatch):
        _set_timeout(monkeypatch, 0)
        seen = {}
        d = mod._await_gateway_decision(
            SESSION, lambda data: seen.update(data),
            {"command": "rm -rf /x", "description": "t",
             "pattern_key": "k", "pattern_keys": ["k"]},
        )
        assert d["resolved"] is False
        assert mod.resolve_gateway_approval(SESSION, "once", request_id=seen["request_id"]) == 0
        assert mod.resolve_gateway_approval(SESSION, "once") == 0


# ---------------------------------------------------------------------------
# Telegram buttons, end to end through the real queue
# ---------------------------------------------------------------------------

def _tap(adapter, data):
    query = AsyncMock()
    query.data = data
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.from_user = MagicMock()
    query.from_user.first_name = "Mark"
    query.from_user.id = "12345"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    return update, query


async def _send_prompt(adapter, waiter):
    """What gateway/run.py's notify callback does for a button adapter."""
    from gateway.run import _exec_approval_request_kwargs

    msg = MagicMock()
    msg.message_id = 1
    adapter._bot.send_message = AsyncMock(return_value=msg)
    before = set(adapter._approval_state)
    await adapter.send_exec_approval(
        chat_id="12345", command=waiter.command, session_key=SESSION,
        **_exec_approval_request_kwargs(adapter, waiter.data),
    )
    (approval_id,) = set(adapter._approval_state) - before
    return approval_id


class TestTelegramBinding:
    @pytest.mark.asyncio
    async def test_late_tap_on_expired_prompt_does_not_approve_newer_one(self, monkeypatch):
        adapter = _make_adapter()
        _set_timeout(monkeypatch, 1)
        a = _Waiter("rm -rf /expired").start()
        a_id = await _send_prompt(adapter, a)
        assert a.join()["resolved"] is False  # prompt A timed out

        _set_timeout(monkeypatch, 30)
        b = _Waiter("rm -rf /newer").start()
        await _send_prompt(adapter, b)

        update, query = _tap(adapter, f"ea:once:{a_id}")
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, MagicMock())

        # B is untouched and still waiting for its own answer.
        assert _pending_commands() == ["rm -rf /newer"]
        assert b.thread.is_alive()
        # A's message says so instead of "Approved".
        rendered = query.edit_message_text.call_args[1]["text"]
        assert "Approved" not in rendered
        assert "no longer pending" in rendered
        assert a_id not in adapter._approval_state
        assert a_id not in adapter._approval_request_ids

        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        b.join()

    @pytest.mark.asyncio
    async def test_tap_on_second_prompt_approves_the_second_only(self, monkeypatch):
        adapter = _make_adapter()
        _set_timeout(monkeypatch, 30)
        first = _Waiter("rm -rf /first").start()
        await _send_prompt(adapter, first)
        second = _Waiter("rm -rf /second").start()
        second_id = await _send_prompt(adapter, second)

        update, query = _tap(adapter, f"ea:once:{second_id}")
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False):
            await adapter._handle_callback_query(update, MagicMock())

        d = second.join()
        assert d["resolved"] is True and d["choice"] == "once"
        assert _pending_commands() == ["rm -rf /first"]
        assert first.thread.is_alive()
        assert "Approved once" in query.edit_message_text.call_args[1]["text"]

        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        first.join()

    @pytest.mark.asyncio
    async def test_approval_state_is_bounded(self, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setattr(type(adapter), "_APPROVAL_STATE_MAX", 3)
        msg = MagicMock()
        msg.message_id = 1
        adapter._bot.send_message = AsyncMock(return_value=msg)
        # Five prompts whose requests are not pending (already expired).
        for i in range(5):
            await adapter.send_exec_approval(
                chat_id="12345", command=f"c{i}", session_key=SESSION,
                request_id=f"gone-{i}",
            )
        assert len(adapter._approval_state) == 3
        assert set(adapter._approval_request_ids) == set(adapter._approval_state)
        # The newest survive.
        assert sorted(adapter._approval_request_ids.values()) == ["gone-2", "gone-3", "gone-4"]

    def test_wiring_passes_request_id_only_to_adapters_that_accept_it(self):
        from gateway.run import _exec_approval_request_kwargs

        class _Legacy:
            async def send_exec_approval(self, chat_id, command, session_key,
                                         description="", metadata=None):
                pass

        assert _exec_approval_request_kwargs(_make_adapter(), {"request_id": "r"}) == {"request_id": "r"}
        assert _exec_approval_request_kwargs(_Legacy(), {"request_id": "r"}) == {}
        assert _exec_approval_request_kwargs(_make_adapter(), {}) == {}


# ---------------------------------------------------------------------------
# Text fallback (Signal etc.) — say which prompt /approve targets
# ---------------------------------------------------------------------------

class TestTextPromptQueueNote:
    def test_no_note_with_a_single_pending_approval(self, monkeypatch):
        from gateway.run import _approval_text_queue_note

        _set_timeout(monkeypatch, 30)
        a = _Waiter("rm -rf /a").start()
        assert _approval_text_queue_note(SESSION) == ""
        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        a.join()

    def test_note_names_oldest_first_when_several_pending(self, monkeypatch):
        from gateway.run import _approval_text_queue_note

        _set_timeout(monkeypatch, 30)
        ws = [_Waiter("rm -rf /a").start(), _Waiter("rm -rf /b").start()]
        note = _approval_text_queue_note(SESSION, "!")
        assert "2 approvals are pending" in note
        assert "OLDEST" in note
        assert "`!approve all`" in note
        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        for w in ws:
            w.join()


# ---------------------------------------------------------------------------
# PWA / tui_gateway approval.respond
# ---------------------------------------------------------------------------

class TestTuiApprovalRespond:
    def _respond(self, monkeypatch, params):
        from tui_gateway import server

        monkeypatch.setattr(server, "_sess", lambda p, rid: ({"session_key": SESSION}, None))
        return server._methods["approval.respond"]("r1", params)

    def test_emitted_request_carries_request_id(self, monkeypatch):
        from tui_gateway import server

        emitted = {}
        monkeypatch.setattr(
            server, "_emit",
            lambda event, sid, payload=None: emitted.update({"payload": payload}),
        )
        _set_timeout(monkeypatch, 30)
        w = _Waiter("rm -rf /a")
        w._notify = lambda data: (server._emit_approval_request("sid", data), setattr(w, "data", data), w.notified.set())
        w.start()
        assert emitted["payload"]["request_id"] == w.request_id
        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        w.join()

    def test_respond_with_request_id_targets_that_prompt(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        first = _Waiter("rm -rf /first").start()
        second = _Waiter("rm -rf /second").start()

        out = self._respond(monkeypatch, {"choice": "once", "session_id": "s",
                                          "request_id": second.request_id})
        assert out["result"]["resolved"] == 1
        assert second.join()["choice"] == "once"
        assert _pending_commands() == ["rm -rf /first"]

        # A stale id (already answered) resolves nothing.
        out = self._respond(monkeypatch, {"choice": "once", "session_id": "s",
                                          "request_id": second.request_id})
        assert out["result"]["resolved"] == 0
        assert _pending_commands() == ["rm -rf /first"]

        mod.resolve_gateway_approval(SESSION, "deny", resolve_all=True)
        first.join()

    def test_respond_without_request_id_keeps_fifo(self, monkeypatch):
        _set_timeout(monkeypatch, 30)
        first = _Waiter("rm -rf /first").start()
        second = _Waiter("rm -rf /second").start()
        out = self._respond(monkeypatch, {"choice": "once", "session_id": "s"})
        assert out["result"]["resolved"] == 1
        assert first.join()["choice"] == "once"
        out = self._respond(monkeypatch, {"choice": "deny", "session_id": "s", "all": True})
        assert out["result"]["resolved"] == 1
        assert second.join()["choice"] == "deny"

    def test_respond_rejects_non_string_request_id(self, monkeypatch):
        out = self._respond(monkeypatch, {"choice": "once", "session_id": "s", "request_id": 5})
        assert out["error"]["code"] == 4006
