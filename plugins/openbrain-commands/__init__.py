"""Open Brain read-only slash commands (Phase 5c.3 adapter).

Relocates the read-only Open Brain commands — ``/brief``, ``/digest``,
``/stale``, ``/finance-check`` — out of the gateway core and into an adapter
plugin. Each handler takes the raw command args and reads durable memory through
``gateway.open_brain``; none needs session state, so the thin
``fn(raw_args) -> str`` plugin-command contract is sufficient.

The session-aware commands ``/ob`` and ``/note`` stay in gateway core for now
(they need the transcript / message source, which the plugin-command handler
does not receive); ``/nosave``, ``/private``, ``/capture-status`` stay too —
they are generic capture-consent controls, not Open Brain behavior.

Enablement: this is a ``standalone`` plugin, so it loads only when listed in
``plugins.enabled`` in ``config.yaml``. The matching core ``CommandDef`` rows
and gateway dispatch branches were removed when this landed, so the deploy that
ships this plugin must also add ``openbrain-commands`` to ``plugins.enabled`` or
the commands disappear.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _format_brief_timestamp(raw_value: object) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return "unknown time"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


async def _handle_brief(raw_args: str) -> str:
    query = (raw_args or "").strip()
    try:
        from gateway.open_brain import OpenBrainConfigError, fetch_briefing

        items = await fetch_briefing(query=query or None, limit=3)
    except OpenBrainConfigError as exc:
        return f"Openbrain isn't configured for `/brief`: {exc}"
    except Exception as exc:
        logger.warning("Brief readback failed: %s", exc)
        return f"⚠️ Brief readback failed: {exc}"

    if not items:
        if query:
            return f"🧠 No Hermes captures matched `{query}`."
        return "🧠 No recent Hermes captures found yet."

    lines = ["🧠 **Brief**"]
    if query:
        lines.extend(["", f"Query: `{query}`"])
    for item in items:
        record_type = str(item.get("record_type") or "thought").replace("_", " ")
        timestamp = _format_brief_timestamp(item.get("created_at"))
        excerpt = str(item.get("excerpt") or "").strip()
        citation = str(item.get("citation") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        session_id = str(item.get("session_id") or "").strip()
        cite_bits = [bit for bit in (citation, source_id or session_id) if bit]
        cite_suffix = f" [{', '.join(cite_bits)}]" if cite_bits else ""
        lines.append(f"- {timestamp} · {record_type}{cite_suffix}: {excerpt}")
    return "\n".join(lines)


async def _handle_digest(raw_args: str) -> str:
    query = (raw_args or "").strip()
    try:
        from gateway.open_brain import OpenBrainConfigError, fetch_digest

        digest = await fetch_digest(query=query or None, days=7)
    except OpenBrainConfigError as exc:
        return f"Openbrain isn't configured for `/digest`: {exc}"
    except Exception as exc:
        logger.warning("Digest readback failed: %s", exc)
        return f"⚠️ Digest readback failed: {exc}"

    total_items = int(digest.get("total_items") or 0)
    if total_items <= 0:
        if query:
            return f"🧠 No Hermes captures matched `{query}` for this week's digest."
        return "🧠 No Hermes captures found for the last 7 days."

    lines = ["🧠 **Digest**"]
    if query:
        lines.extend(["", f"Query: `{query}`"])
    lines.extend([
        "",
        (
            f"Window: last 7 days · {total_items} captures "
            f"({int(digest.get('meeting_notes') or 0)} notes, "
            f"{int(digest.get('session_summaries') or 0)} summaries)"
        ),
    ])

    for label, key in (("Decisions & outcomes:", "decisions"),
                       ("Open loops:", "actions"),
                       ("Highlights:", "highlights")):
        entries = digest.get(key) or []
        if entries:
            lines.append("")
            lines.append(label)
            for item in entries:
                lines.append(f"- {item['text']}{item.get('reference') or ''}")
    return "\n".join(lines)


async def _handle_stale(raw_args: str) -> str:
    try:
        from gateway.open_brain import OpenBrainConfigError, fetch_stale_items

        report = await fetch_stale_items()
    except OpenBrainConfigError as exc:
        return f"Openbrain isn't configured for `/stale`: {exc}"
    except Exception as exc:
        logger.warning("Stale readback failed: %s", exc)
        return f"⚠️ Stale readback failed: {exc}"

    stale_actions = report.get("stale_actions") or []
    stale_contacts = report.get("stale_contacts") or []
    action_days = int(report.get("action_days") or 14)

    if not stale_actions and not stale_contacts:
        return f"✅ Nothing stale found in the last {action_days}+ days."

    lines = ["🕰️ **Stale**"]
    if stale_actions:
        lines.extend(["", f"Open loops older than {action_days} days:"])
        for item in stale_actions:
            age = int(item.get("age_days") or 0)
            text = str(item.get("text") or "").strip()
            citation = str(item.get("citation") or "").strip()
            cite_suffix = f" [{citation}]" if citation else ""
            lines.append(f"- {age}d ago{cite_suffix}: {text}")
    if stale_contacts:
        lines.extend(["", "Contacts not mentioned recently:"])
        for contact in stale_contacts:
            name = str(contact.get("name") or "").strip()
            excerpt = str(contact.get("excerpt") or "").strip()
            citation = str(contact.get("citation") or "").strip()
            cite_suffix = f" [{citation}]" if citation else ""
            lines.append(f"- {name}{cite_suffix}: last seen in \"{excerpt}\"")
    return "\n".join(lines)


async def _handle_finance_check(raw_args: str) -> str:
    try:
        from gateway.open_brain import OpenBrainConfigError, fetch_finance_anomalies

        report = await fetch_finance_anomalies()
    except OpenBrainConfigError as exc:
        return f"Openbrain isn't configured for `/finance-check`: {exc}"
    except Exception as exc:
        logger.warning("Finance check failed: %s", exc)
        return f"⚠️ Finance check failed: {exc}"

    days = int(report.get("days") or 30)
    if not report.get("has_anomalies"):
        current_total = report.get("current_total") or 0
        return (
            f"✅ No finance anomalies in the last {days} days. "
            f"Total spend: {current_total:.0f}."
        )

    current_total = float(report.get("current_total") or 0)
    prior_total = float(report.get("prior_total") or 0)
    lines = ["💰 **Finance check**", "", f"Period: last {days} days vs prior {days} days"]
    if prior_total > 0:
        overall_pct = (current_total - prior_total) / prior_total * 100
        sign = "+" if overall_pct >= 0 else ""
        lines.append(f"Total: {current_total:.0f} vs {prior_total:.0f} prior ({sign}{overall_pct:.0f}%)")
    else:
        lines.append(f"Total: {current_total:.0f} (no prior-period data)")

    category_anomalies = report.get("category_anomalies") or []
    if category_anomalies:
        lines.extend(["", "Category anomalies:"])
        for item in category_anomalies:
            lines.append(f"- {item['category']}: {item['reason']}")

    large_transactions = report.get("large_transactions") or []
    if large_transactions:
        lines.extend(["", "Large transactions:"])
        for txn in large_transactions:
            date_str = str(txn.get("date") or "").strip()
            desc = str(txn.get("description") or txn.get("category") or "").strip()
            amount = float(txn.get("amount") or 0)
            date_prefix = f"{date_str}: " if date_str else ""
            lines.append(f"- {date_prefix}{desc} ({amount:.0f})")
    return "\n".join(lines)


# Proactive-surface feedback (the ✅ Useful / 🙈 Dismiss buttons on daily-digest
# and weekly-review nudges) rides the generic message-action seam. The buttons are
# minted cross-repo by the open_brain scripts as ``act:prxa|prxd:<surface_id>``;
# Hermes consumes the press here (Phase 5c.3 step 2, Stage C). action_id carries
# the verdict, token carries the surface id.
_PROACTIVE_USEFUL = "prxa"
_PROACTIVE_DISMISS = "prxd"


async def _handle_proactive_feedback(action_id: str, token: str, _context) -> str:
    """Record a ✅ Useful / 🙈 Dismiss press on a proactive surface."""
    surface_id = (token or "").strip()
    if not surface_id:
        return "This proactive prompt expired."
    status = "acted_on" if action_id == _PROACTIVE_USEFUL else "dismissed"
    try:
        from gateway.open_brain import record_proactive_feedback

        await record_proactive_feedback(surface_id=surface_id, status=status)
    except Exception as exc:
        logger.warning("Failed to record proactive feedback: %s", exc)
        return "⚠️ Couldn't save feedback."
    return "Marked useful" if status == "acted_on" else "Dismissed"


def register(ctx) -> None:
    ctx.register_command(
        "brief", handler=_handle_brief,
        description="Show recent Hermes captures from Openbrain", args_hint="[query]",
    )
    ctx.register_command(
        "digest", handler=_handle_digest,
        description="Show a synthesized weekly digest from Hermes captures", args_hint="[query]",
    )
    ctx.register_command(
        "stale", handler=_handle_stale,
        description="Show stale action items and contacts from Openbrain",
    )
    ctx.register_command(
        "finance-check", handler=_handle_finance_check,
        description="Check for finance anomalies against the prior period",
    )
    # Proactive-surface ✅/🙈 feedback on the generic action seam (Stage C).
    if hasattr(ctx, "register_action_handler"):
        ctx.register_action_handler(_PROACTIVE_USEFUL, _handle_proactive_feedback)
        ctx.register_action_handler(_PROACTIVE_DISMISS, _handle_proactive_feedback)
