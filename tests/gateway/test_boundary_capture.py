"""Tests for the generic gateway boundary capturer (Phase 5c seam 1).

The capturer must be provider-agnostic: it drives whatever memory provider is
configured via ``memory.provider`` through the upstream
``build_capture_context`` builder, and honors the generic capture-consent
contract (``/private`` → ``eligible=False``).
"""

import pytest

from gateway.boundary_capture import GatewayBoundaryCapturer


class _FakeProvider:
    """Minimal MemoryProvider stand-in recording on_session_end calls."""

    name = "fake"

    def __init__(self):
        self.calls = []
        self.initialized = []

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        self.initialized.append((session_id, kwargs))

    def on_session_end(self, messages, *, capture_context=None):
        self.calls.append((list(messages), dict(capture_context or {})))


_MESSAGES = [
    {"role": "user", "content": "Let's plan the launch for next week."},
    {"role": "assistant", "content": "I'll draft the rollout checklist and send it over."},
]


def _capturer_with(provider):
    """Build a capturer bound to ``provider``, bypassing config/plugin lookup."""
    cap = GatewayBoundaryCapturer(provider_name="fake")
    from agent.memory_manager import MemoryManager

    mgr = MemoryManager()
    # Populate the provider list directly: add_provider enforces the full
    # MemoryProvider ABC, but the capturer only needs on_session_end +
    # initialize, which the fake supplies.
    mgr._providers = [provider]
    cap._manager = mgr
    cap._bound = True
    return cap


def test_disabled_when_no_provider_name():
    cap = GatewayBoundaryCapturer(provider_name="")
    assert cap.enabled is False
    assert cap.capture(
        session_id="s1", platform="telegram", messages=_MESSAGES,
        boundary_reason="reset",
    ) is False


def test_capture_drives_provider_on_session_end():
    provider = _FakeProvider()
    cap = _capturer_with(provider)

    ok = cap.capture(
        session_id="sess-1", platform="telegram", messages=_MESSAGES,
        boundary_reason="shutdown", user_id="u1", chat_id="c1",
    )

    assert ok is True
    assert len(provider.calls) == 1
    messages, ctx = provider.calls[0]
    assert messages == _MESSAGES
    assert ctx["session_id"] == "sess-1"
    assert ctx["boundary_reason"] == "shutdown"
    assert ctx["platform"] == "telegram"
    # The upstream builder populated a deterministic boundary id + records.
    assert ctx.get("boundary_id")
    assert "capture_records" in ctx
    # Eligible session keeps the builder's content-derived eligibility.
    assert "eligible" in ctx
    # Provider was initialized as a primary-context capture before the call.
    assert provider.initialized
    assert provider.initialized[-1][1].get("agent_context") == "primary"


def test_private_forces_ineligible():
    provider = _FakeProvider()
    cap = _capturer_with(provider)

    cap.capture(
        session_id="sess-2", platform="cli", messages=_MESSAGES,
        boundary_reason="reset", eligible=False,
    )

    _messages, ctx = provider.calls[0]
    assert ctx["eligible"] is False
    assert ctx["capture_skip_reason"] == "private_mode"


def test_empty_messages_skip():
    provider = _FakeProvider()
    cap = _capturer_with(provider)
    assert cap.capture(
        session_id="s", platform="cli", messages=[], boundary_reason="reset",
    ) is False
    assert provider.calls == []


def test_provider_exception_is_swallowed():
    provider = _FakeProvider()

    def _boom(messages, *, capture_context=None):
        raise RuntimeError("provider down")

    provider.on_session_end = _boom
    cap = _capturer_with(provider)

    # Best-effort: a provider failure must not raise out of capture(). The
    # MemoryManager swallows per-provider errors, so the boundary is still
    # considered "notified" (True) — the contract is "never raise", not "detect
    # the provider's internal failure".
    assert cap.capture(
        session_id="s", platform="cli", messages=_MESSAGES,
        boundary_reason="reset",
    ) is True
