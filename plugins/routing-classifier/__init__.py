"""Local-by-default routing classifier for Hermes.

This plugin exposes an explicit ``/route`` command that explains how Hermes
would route a task under the multi-AI playbook. It is intentionally
deterministic and observe/propose-only: it does not switch models, gather large
context, or spend frontier calls.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Literal

Route = Literal["local_by_default", "hard_escalate", "uncertain"]
TaskType = Literal[
    "commit_msg",
    "docstring",
    "doc_edit",
    "code_trace",
    "diff_summary",
    "test_scaffold",
    "other",
]
Target = Literal["local", "opus", "codex", "none"]
Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    task_type: TaskType
    target: Target
    reason: str
    confidence: Confidence = "medium"
    requires_confirmation: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "route": self.route,
            "task_type": self.task_type,
            "target": self.target,
            "reason": self.reason,
            "confidence": self.confidence,
            "requires_confirmation": self.requires_confirmation,
        }


_LOCAL_RULES: tuple[tuple[TaskType, tuple[str, ...], str], ...] = (
    (
        "commit_msg",
        ("commit message", "conventional commit", "commit msg", "write a commit"),
        "Commit messages are on the evidence-backed local-by-default list.",
    ),
    (
        "docstring",
        ("docstring", "doc string", "function documentation string"),
        "Docstrings are on the evidence-backed local-by-default list.",
    ),
    (
        "doc_edit",
        (
            "documentation edit",
            "doc edit",
            "docs cleanup",
            "readme",
            "tighten the wording",
            "improve the docs",
            "update the docs",
            "documentation",
        ),
        "Documentation edits are on the evidence-backed local-by-default list.",
    ),
    (
        "code_trace",
        (
            "where is",
            "where does",
            "used",
            "usage",
            "trace",
            "call path",
            "find references",
            "explain where",
        ),
        "Code search and trace explanations are local-by-default.",
    ),
    (
        "diff_summary",
        (
            "summarize the diff",
            "diff summary",
            "pr description",
            "reviewer summary",
            "summarize what changed",
            "changelog",
            "change log",
        ),
        "Diff summaries are on the evidence-backed local-by-default list.",
    ),
)

_LOCAL_OTHER: tuple[tuple[str, str], ...] = (
    ("todo", "TODO triage is local unless safety-adjacent."),
    ("rename", "Renaming suggestions are local unless safety-adjacent."),
    ("explain", "Explaining unfamiliar code is local unless safety-adjacent."),
    ("collect facts", "Collecting repo facts is local unless safety-adjacent."),
    ("context brief", "Preparing a frontier context brief is local work."),
)

_HARD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "auth",
            "permission",
            "permissions",
            "secret",
            "token",
            "oauth",
            "keychain",
            "credential",
            "api key",
        ),
        "Auth, permissions, secrets, and tokens are hard escalation categories.",
    ),
    (
        (
            "migration",
            "schema",
            "database",
            "supabase",
            "sql",
            "drop table",
            "delete data",
            "data deletion",
            "rls",
        ),
        "Database schema, migrations, and data deletion require frontier review.",
    ),
    (
        (
            "public api",
            "api contract",
            "contract",
            "endpoint",
            "breaking change",
            "tool schema",
            "mcp contract",
        ),
        "Public API contracts can break downstream callers.",
    ),
    (
        (
            "concurrency",
            "race",
            "locking",
            "deadlock",
            "scheduler",
            "cron",
            "queue",
            "parallel",
        ),
        "Concurrency, locking, and scheduling are hard escalation categories.",
    ),
    (
        ("payment", "billing", "quota", "usage enforcement", "credits", "subscription"),
        "Payment, billing, quota, and usage enforcement have high impact.",
    ),
    (
        ("production deploy", "deploy", "release", "launchctl", "service restart", "prod"),
        "Production deploy and release mechanics need stronger review.",
    ),
    (
        (
            "cross-repo",
            "multiple repos",
            "open brain and hermes",
            "open_brain and hermes",
            "hermes and open brain",
            "hermes and open_brain",
            "architecture decision",
        ),
        "Cross-repo architecture decisions require broader synthesis.",
    ),
    (
        ("security dependency", "dependency security", "cve", "vulnerability", "supply chain"),
        "Security-sensitive dependency changes require frontier review.",
    ),
)

_CHANGE_VERBS = (
    "add",
    "change",
    "create",
    "delete",
    "deploy",
    "drop",
    "fix",
    "implement",
    "modify",
    "remove",
    "ship",
    "switch",
    "update",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _looks_like_change_request(text: str) -> bool:
    return _contains_any(text, _CHANGE_VERBS)


def _matches_test_scaffold(text: str) -> bool:
    patterns = (
        "add test",
        "add tests",
        "write test",
        "write tests",
        "scaffold test",
        "scaffold tests",
        "unit test",
        "pytest",
        "test file",
        "test scaffold",
    )
    return _contains_any(text, patterns)


def classify_message(
    text: str,
    *,
    repo: str | None = None,
    has_diff: bool = False,
    has_test_output: bool = False,
) -> RouteDecision:
    """Classify a task under the local/frontier routing playbook."""
    msg = _norm(text)
    repo_text = _norm(repo or "")
    combined = f"{msg} {repo_text}".strip()

    if not msg:
        return RouteDecision(
            route="uncertain",
            task_type="other",
            target="none",
            reason="No task text was provided.",
            confidence="low",
        )

    if _matches_test_scaffold(combined):
        return RouteDecision(
            route="uncertain",
            task_type="test_scaffold",
            target="none",
            reason=(
                "Runnable test scaffolds are on hold: qwen3 failed this harness "
                "category by redefining functions under test."
            ),
            confidence="high",
        )

    read_only_summary = _contains_any(
        combined,
        (
            "summarize",
            "explain",
            "trace",
            "where is",
            "where does",
            "diff summary",
            "pr description",
        ),
    )

    if not read_only_summary:
        for needles, reason in _HARD_RULES:
            if _contains_any(combined, needles) and _looks_like_change_request(combined):
                return RouteDecision(
                    route="hard_escalate",
                    task_type="other",
                    target="opus",
                    reason=reason,
                    confidence="high",
                    requires_confirmation=True,
                )

    for task_type, needles, reason in _LOCAL_RULES:
        if _contains_any(combined, needles) or (task_type == "diff_summary" and has_diff):
            return RouteDecision(
                route="local_by_default",
                task_type=task_type,
                target="local",
                reason=reason,
                confidence="high",
            )

    for needle, reason in _LOCAL_OTHER:
        if needle in combined:
            return RouteDecision(
                route="local_by_default",
                task_type="other",
                target="local",
                reason=reason,
                confidence="medium",
            )

    if has_test_output:
        return RouteDecision(
            route="uncertain",
            task_type="other",
            target="none",
            reason=(
                "Test output is present. Try local diagnosis first, then escalate "
                "only if the same check fails twice."
            ),
            confidence="medium",
        )

    return RouteDecision(
        route="uncertain",
        task_type="other",
        target="none",
        reason="No local-by-default or hard-escalation rule matched confidently.",
        confidence="low",
    )


def format_route_decision(decision: RouteDecision) -> str:
    """Render a route decision for chat."""
    lines = [
        "**Routing decision**",
        f"Route: `{decision.route}`",
        f"Task type: `{decision.task_type}`",
        f"Target: `{decision.target}`",
        f"Confidence: `{decision.confidence}`",
        f"Reason: {decision.reason}",
    ]
    if decision.route == "local_by_default":
        lines.extend(
            [
                "",
                "Next: handle this locally with qwen3. No frontier call needed.",
            ]
        )
    elif decision.route == "hard_escalate":
        target = "Opus" if decision.target == "opus" else "Codex"
        provider_line = (
            "Provider/profile: anthropic/default"
            if target == "Opus"
            else "Provider/profile: openai/default"
        )
        lines.extend(
            [
                "",
                "I recommend a frontier call.",
                "",
                f"Target: {target}",
                provider_line,
                f"Reason: {decision.reason}",
                "Question: prepare a focused context brief before calling.",
                "Expected output: review, decision, or patch depending on the task.",
                "Cost control: single confirmed call; no automatic spend.",
                "",
                "Proceed?",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Next: try a local pass first, then escalate only on a stuck signal.",
            ]
        )
    return "\n".join(lines)


def _handle_route(raw_args: str) -> str:
    text = (raw_args or "").strip()
    if not text:
        return "Usage: /route <task text>"
    return format_route_decision(classify_message(text))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _auto_preflight_enabled() -> bool:
    """Return whether automatic preflight context injection is enabled."""
    env = os.getenv("HERMES_ROUTING_PREFLIGHT", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    try:
        from hermes_cli.config import cfg_get, load_config

        cfg = load_config() or {}
        return _truthy(cfg_get(cfg, "routing_classifier", "auto_preflight", default=False))
    except Exception:
        return False


def _preflight_context(decision: RouteDecision) -> str:
    """Compact ephemeral context injected into the current user turn."""
    lines = [
        "[Routing preflight]",
        f"Route: {decision.route}",
        f"Task type: {decision.task_type}",
        f"Target: {decision.target}",
        f"Confidence: {decision.confidence}",
        f"Reason: {decision.reason}",
        "Mode: propose-only; do not spend frontier calls automatically.",
    ]
    if decision.route == "hard_escalate":
        lines.append(
            "Instruction: recommend a confirmed frontier call before making or approving changes."
        )
    elif decision.route == "local_by_default":
        lines.append("Instruction: handle locally; no frontier call is needed.")
    else:
        lines.append("Instruction: try a local pass first and escalate only on a stuck signal.")
    return "\n".join(lines)


def on_pre_llm_call(**kwargs) -> dict[str, str] | None:
    """Inject observe/propose-only routing context into normal turns when enabled."""
    if not _auto_preflight_enabled():
        return None
    user_message = str(kwargs.get("user_message") or "").strip()
    if not user_message or user_message.startswith("/"):
        return None
    decision = classify_message(user_message)
    return {"context": _preflight_context(decision)}


def register(ctx) -> None:
    ctx.register_command(
        "route",
        handler=_handle_route,
        description="Classify a task as local-by-default, hard-escalate, or uncertain",
        args_hint="<task text>",
    )
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
