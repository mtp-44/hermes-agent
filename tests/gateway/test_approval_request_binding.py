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

import sys
import threading
import time
from pathlib import Path

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

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
