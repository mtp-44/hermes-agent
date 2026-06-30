"""Durable Hermes work sessions slash commands (Phase 5d).

Interface threads (Telegram, Hermes Desktop) are temporary; work sessions are
durable. This adapter plugin gives every Hermes surface one shared work-session
layer: ``/sessions`` to list and ``/session new|resume|status|checkpoint|pin|
archive`` to manage. The durable state lives in open_brain (the hosted
``session_*`` MCP tools over Supabase); this plugin only calls that contract
through ``gateway.open_brain`` and renders the results.

Unlike the read-only ``openbrain-commands`` plugin, these commands are
*session-aware*: they must know which surface and user issued them so the
per-client "current session" pointer is correct. The thin ``fn(raw_args) -> str``
command contract does not pass that, so the handlers read it from the gateway's
task-local session context (``gateway.session_context``): the originating
platform becomes the session ``client`` (e.g. ``telegram``, ``api_server`` for
Desktop) and the user id becomes ``user_id``.

Enablement: this is a ``standalone`` plugin — add ``openbrain-sessions`` to
``plugins.enabled`` in ``config.yaml`` or the commands do not load.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_VALID_STATUSES = {"active", "paused", "archived"}
_DEFAULT_USER_ID = "mark"
_SESSION_USAGE = "Usage: /session new|resume|status|checkpoint|pin|archive ..."


def _surface() -> tuple[str, str]:
    """Resolve (client, user_id) for the current surface from the session context.

    The gateway sets these task-local vars per inbound message. Falls back to a
    stable ``cli`` client and the primary user when unset (CLI/cron/tests).
    """
    try:
        from gateway.session_context import get_session_env

        client = (get_session_env("HERMES_SESSION_PLATFORM") or "").strip() or "cli"
        user_id = (get_session_env("HERMES_SESSION_USER_ID") or "").strip() or _DEFAULT_USER_ID
        return client, user_id
    except Exception:  # pragma: no cover - defensive; context module always present
        return "cli", _DEFAULT_USER_ID


def _render_summary(session: dict, checkpoint: dict | None = None) -> str:
    """Compact, chat-friendly status block. Mirrors open_brain.render_session_summary."""
    slug = session.get("slug") or session.get("id") or "?"
    lines = [
        f"[session: {slug}]",
        f"Status: {session.get('status', 'active')}",
        f"Goal: {session.get('goal') or '(no goal set)'}",
    ]
    repos = session.get("repos") or []
    if repos:
        lines.append("Repos: " + ", ".join(str(r) for r in repos))
    if checkpoint:
        lines.append("Current state: " + (checkpoint.get("summary") or "(no summary)"))
        lines.append("Next: " + (checkpoint.get("next_action") or "(not set)"))
    return "\n".join(lines)


def _render_list(items: list[dict], status: str | None) -> str:
    if not items:
        return f"No {status or 'any'} work sessions."
    lines = ["🧵 **Work sessions**", "_/session resume <slug>_"]
    for item in items:
        slug = str(item.get("slug") or item.get("id") or "")
        title = str(item.get("title") or slug)
        state = str(item.get("status") or "active")
        pin = "★ " if item.get("pinned") else ""
        updated = str(item.get("updated_at") or "")[:10]
        tail = f" · {updated}" if updated else ""
        lines.append(f"{pin}`{slug}` **{state}** · {title}{tail}")
    return "\n".join(lines)


async def _handle_sessions(raw_args: str) -> str:
    args = (raw_args or "").split()
    status_arg = args[0].strip().lower() if args else "active"
    status = None if status_arg in {"all", "any"} else status_arg
    if status not in {None, *_VALID_STATUSES}:
        return "Usage: /sessions [active|paused|archived|all]"
    try:
        from gateway.open_brain import OpenBrainConfigError, session_list

        items = await session_list(status=status, limit=20)
    except OpenBrainConfigError as exc:
        return f"Openbrain isn't configured for `/sessions`: {exc}"
    except Exception as exc:
        logger.warning("Session list failed: %s", exc)
        return f"⚠️ Couldn't list sessions: {exc}"
    return _render_list(items, status)


async def _handle_session(raw_args: str) -> str:
    args = [a for a in (raw_args or "").split() if a]
    if not args:
        return _SESSION_USAGE
    action = args[0].lower()
    client, user_id = _surface()

    try:
        from gateway.open_brain import (
            OpenBrainConfigError,
            session_checkpoint,
            session_create,
            session_current,
            session_resume,
            session_set_pinned,
            session_set_status,
        )
    except Exception as exc:  # pragma: no cover - import guard
        return f"Openbrain session tools unavailable: {exc}"

    try:
        if action == "new":
            title = " ".join(args[1:]).strip()
            if not title:
                return "Usage: /session new <title>"
            session = await session_create(title=title, client=client, user_id=user_id)
            return "Created and resumed:\n" + _render_summary(session)

        if action == "resume":
            if len(args) < 2:
                return "Usage: /session resume <slug>"
            result = await session_resume(session_ref=args[1], client=client, user_id=user_id)
            return "Resumed:\n" + _render_summary(result.get("session") or {}, result.get("checkpoint"))

        if action == "status":
            result = await session_current(client=client, user_id=user_id)
            session = result.get("session")
            if not session:
                return "No current work session. Use /session new <title> or /session resume <slug>."
            return _render_summary(session, result.get("checkpoint"))

        if action == "checkpoint":
            summary = " ".join(args[1:]).strip()
            if not summary:
                return "Usage: /session checkpoint <summary>"
            result = await session_current(client=client, user_id=user_id)
            session = result.get("session")
            if not session:
                return "No current work session. Use /session new <title> first."
            slug = str(session.get("slug") or session.get("id"))
            await session_checkpoint(session_ref=slug, summary=summary, source=client)
            return f"Checkpoint saved for `{slug}`:\n{summary}"

        if action == "pin":
            if len(args) < 2:
                return "Usage: /session pin <slug>"
            session = await session_set_pinned(session_ref=args[1], pinned=True)
            return f"Pinned `{session.get('slug') or args[1]}`."

        if action == "archive":
            if len(args) < 2:
                return "Usage: /session archive <slug>"
            session = await session_set_status(session_ref=args[1], status="archived")
            return f"Archived `{session.get('slug') or args[1]}`."

        return _SESSION_USAGE
    except OpenBrainConfigError as exc:
        return f"Openbrain isn't configured for `/session`: {exc}"
    except Exception as exc:
        logger.warning("Session command '%s' failed: %s", action, exc)
        return f"⚠️ {exc}"


def register(ctx) -> None:
    ctx.register_command(
        "sessions",
        handler=_handle_sessions,
        description="List durable Hermes work sessions",
        args_hint="[active|paused|archived|all]",
    )
    ctx.register_command(
        "session",
        handler=_handle_session,
        description="Manage the current durable work session",
        args_hint="new|resume|status|checkpoint|pin|archive ...",
    )
