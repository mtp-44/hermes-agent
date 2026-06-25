"""Generic gateway session-boundary capture.

The gateway evicts idle per-session agents (LRU + idle-TTL), so at gateway
session boundaries — ``/reset``, session expiry, and shutdown — the agent that
owns a session's :class:`~agent.memory_provider.MemoryProvider` is frequently no
longer resident, and ``MemoryProvider.on_session_end()`` never fires for that
session.

This module closes that gap generically: for any ending gateway session it
reconstructs the capture context with the *same* upstream builder the resident
agent path uses (:func:`agent.session_capture.build_capture_context`) and hands
it to the configured memory provider's ``on_session_end()``. No specific memory
backend is referenced here — any provider selected via ``memory.provider`` in
config receives the boundary.

Consent policy is generic capture-consent, not tied to any provider:

* A session marked ``/nosave`` is never delivered (the caller skips it).
* A session in ``/private`` mode is delivered with ``eligible=False`` so a
  provider that honors the flag performs no durable write while still being
  notified of the boundary.

Idempotency is the provider's responsibility: ``build_capture_context`` derives
a deterministic ``boundary_id`` from the session, reason, and message refs, so a
provider that dedups on it (as the bundled providers do) will not double-write a
replayed boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from hermes_cli.config import cfg_get, get_hermes_home, load_config

logger = logging.getLogger(__name__)


class GatewayBoundaryCapturer:
    """Bind the configured memory provider and drive ``on_session_end`` for
    non-resident gateway sessions.

    The provider is resolved and bound lazily on first capture so gateway
    startup never pays for it and a misconfigured provider degrades to a no-op
    instead of breaking the gateway.
    """

    def __init__(self, provider_name: Optional[str] = None) -> None:
        # ``None`` means "resolve from config on first use"; an explicit string
        # (including "") is honored as-is, which keeps tests hermetic.
        self._provider_name = provider_name
        self._manager: Any = None
        self._bound = False

    @property
    def enabled(self) -> bool:
        """True when a memory provider is configured (cheap; reads config)."""
        return bool(self._resolve_provider_name())

    def _resolve_provider_name(self) -> str:
        if self._provider_name is not None:
            return self._provider_name.strip()
        try:
            name = cfg_get(load_config(), "memory", "provider", default="")
            return str(name or "").strip()
        except Exception as exc:  # pragma: no cover - config read is best-effort
            logger.debug("Gateway boundary capture: config read failed: %s", exc)
            return ""

    def _manager_or_none(self):
        if self._bound:
            return self._manager
        self._bound = True
        provider_name = self._resolve_provider_name()
        if not provider_name:
            return None
        try:
            from agent.memory_manager import MemoryManager
            from plugins.memory import load_memory_provider

            provider = load_memory_provider(provider_name)
            if provider is None or not provider.is_available():
                logger.info(
                    "Gateway boundary capture disabled: provider '%s' unavailable",
                    provider_name,
                )
                return None
            manager = MemoryManager()
            manager.add_provider(provider)
            self._manager = manager
        except Exception as exc:
            logger.warning(
                "Gateway boundary capture: failed to bind provider '%s': %s",
                provider_name,
                exc,
            )
            self._manager = None
        return self._manager

    def capture(
        self,
        *,
        session_id: str,
        platform: str,
        messages: List[Dict[str, Any]],
        boundary_reason: str,
        eligible: bool = True,
        user_id: str = "",
        chat_id: str = "",
    ) -> bool:
        """Drive the configured provider's ``on_session_end`` for one boundary.

        Returns ``True`` when a provider was notified (regardless of whether it
        chose to write), ``False`` when capture was skipped (no provider, no
        messages, or a failure). Best-effort: never raises.
        """
        manager = self._manager_or_none()
        if manager is None or not messages:
            return False

        try:
            from agent.session_capture import build_capture_context

            context = build_capture_context(
                session_id=session_id,
                boundary_reason=boundary_reason,
                platform=platform,
                messages=messages,
                user_id=user_id,
                chat_id=chat_id,
            )
        except Exception as exc:
            logger.warning(
                "Gateway boundary capture: context build failed for %s: %s",
                session_id,
                exc,
            )
            return False

        # Consent gate: /private notifies the provider but forces no durable
        # write. /nosave never reaches here (the caller skips it entirely).
        if not eligible:
            context["eligible"] = False
            context["capture_skip_reason"] = "private_mode"

        hermes_home = str(get_hermes_home())
        try:
            for provider in manager.providers:
                provider.initialize(
                    session_id,
                    platform=platform,
                    hermes_home=hermes_home,
                    agent_context="primary",
                )
        except Exception as exc:
            logger.debug(
                "Gateway boundary capture: provider initialize failed for %s: %s",
                session_id,
                exc,
            )

        try:
            manager.on_session_end(messages, capture_context=context)
            return True
        except Exception as exc:
            logger.warning(
                "Gateway boundary capture failed for %s: %s", session_id, exc
            )
            return False
