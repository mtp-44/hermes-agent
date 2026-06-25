# Open Brain ↔ Hermes Touchpoint Inventory (Phase 5c.1)

Status: **complete** for Phase 5c.1 of
[`OPEN_BRAIN_9PLUS_PLAN.md`](../../OPEN_BRAIN_9PLUS_PLAN.md) §5c.1.

This document is assistant-neutral (Codex or Claude Code can act on it cold). It
classifies **every** Open Brain-specific touchpoint in the Hermes fork so that
Phase 5c.2/5c.3/5c.5 can proceed: each item is tagged as an **upstream seam**
(generic hook to add upstream), **adapter** (Open Brain behavior that should live
in a plugin/adapter), or **shim** (temporary compatibility glue to delete once a
seam exists).

## Method

Touchpoints were found with:

```bash
cd /Users/mh/ai/agents/hermes-agent
git grep -il -e open_brain -e open-brain -e openbrain \
  -e capture_thought -e query_brain -e x-brain-key
```

Each file was then classified against the upstream merge-base
`c06898098b865b5a8f48535c08ad9de5459211e4` (the `reference` remote =
`NousResearch/hermes-agent`) as **NEW (fork-only)** or **MODIFIED (upstream
core)**:

```bash
git diff --numstat <merge-base> -- <file>      # MODIFIED size
git cat-file -e <merge-base>:<file>            # exists upstream?
```

The distinction is the whole point of Phase 5c: **NEW** files are already
isolated and survive an upstream update untouched; **MODIFIED** upstream files
are the fragile delta a Hermes upgrade (checkout/reset to a release tag) can wipe.

## Upstream extension seams that already exist

Hermes upstream already provides three seams the fork partially uses:

- **Lifecycle hooks** — `gateway/hooks.py` fires `gateway:startup`,
  `session:start`, `session:end`, `session:reset`, `agent:start`, `agent:step`,
  `agent:end`, and `command:*`, discovered from `~/.hermes/hooks/`. **Limitation:**
  handler context carries only a 500-char-truncated `message`/`response` and
  errors "never block the main pipeline" (handlers cannot return a reply).
- **Plugin `transform_tool_result`** — used cleanly today by the
  `openbrain-query-brain-format` plugin.
- **`MemoryProvider`** (`agent/memory_provider.py`) — used cleanly today by the
  `plugins/memory/openbrain` provider.

These three seams are why the query-result formatter and the memory writer are
already fork-safe. The remaining core edits exist because **no upstream seam yet
carries full session-boundary fidelity, registers slash commands that return a
reply, or decorates platform messages / handles callback actions.**

## A. MODIFIED upstream-core files — the fragile delta (Phase 5c.5 targets)

| File | Δ vs upstream | Open Brain behavior injected | Destination |
|---|---:|---|---|
| `gateway/run.py` | +675 / −15 | (1) Session-end/shutdown capture **policy** — `_capture_session_summary_if_eligible` (lines ~10299+), wired at shutdown (~4594) and reset (~6052), calls fork-only `gateway/open_brain.save_session_summary`. (2) Slash-command **dispatch** for `/ob /note /brief /digest /stale /nosave /private /capture-status` (~7626–7745, ~8054–8106) returning replies. (3) Per-session privacy flags (`/nosave`, `/private`, capture-status). (4) Feedback-candidate staging glue (~17037–17367). | **Upstream seam** (1+2) → full-fidelity `session:boundary` event + reply-returning command seam; then move bodies to **adapter**. (4) → platform-decoration seam (see telegram). |
| `gateway/platforms/telegram.py` | +203 / −11 | Feedback inline buttons (`obf:g/b:<token>` 👍/👎, lines ~603), `prx:` proactive Useful/Dismiss callbacks, feedback-context token registry, `stage/pop/attach_open_brain_feedback`, callback dispatch (~2192) into `record_query_feedback` / `record_proactive_feedback`. | **Upstream seam** → generic platform message-decoration + callback/action-handler seam; then **adapter** owns button layout + MCP feedback calls. |
| `hermes_cli/commands.py` | +46 / −8 | Registers 8 `CommandDef`s: `ob`, `note`, `brief`, `digest`, `stale`, `nosave`, `private`, `capture-status`. | **Upstream seam** → external slash-command registration; then registration moves to **adapter** manifest. |
| `cli.py` | +35 / −5 | Local-CLI `/ob` manual capture: `_capture_open_brain_snapshot` (~46) tracks `_last_manual_capture_index` and calls `save_session_summary(boundary_reason="manual_capture")`. | **Upstream seam** (same command/boundary seams as run.py) — proves the second interface (CLI) must route through the **same adapter**, not a copy. |
| `gateway/platforms/base.py` | +12 / −0 | Pass-through: pops `pop_staged_open_brain_feedback(session_key)` and stuffs `open_brain_feedback` into thread metadata. | **Shim** — delete once the platform-decoration seam exists. |

## B. NEW fork-only files — already isolated (Phase 5c.3 = formalize)

| File | Role | Classification |
|---|---|---|
| `plugins/memory/openbrain/` (`__init__.py` 395 L, `plugin.yaml`) | Session-close capture writer → hosted `capture_thought`, via upstream `MemoryProvider`. | **Adapter — clean.** Already rides an upstream seam. No action beyond keeping it as the canonical capture path. |
| `plugins/openbrain-query-brain-format/` (`__init__.py`, `plugin.yaml`) | Formats `query_brain` results, via upstream plugin `transform_tool_result`. | **Adapter — clean.** Already rides an upstream seam. |
| `gateway/open_brain.py` | Open Brain MCP client: `save_session_summary`, `capture_meeting_note`, `fetch_briefing/digest/stale/finance_anomalies`, `record_query_feedback`, `record_proactive_feedback`; **MCP header env-interpolation + fail-loud `OpenBrainConfigError`** (the 2026-06-10 401 fix, lines ~80) lives here, self-contained. | **Adapter.** Logic is correct and fork-only, but sits in the `gateway/` core dir — relocate under the Open Brain adapter package so "delete the adapter → Hermes core is clean" holds. |
| `gateway/open_brain_feedback.py` | Feedback-candidate capture/staging (`pop_feedback_candidate`). | **Adapter.** Relocate alongside `open_brain.py`. |
| `tools/strava_tool.py` | Strava → Open Brain model tool (direct Supabase, service-role). | **Adapter** (Open Brain model tool). Fork-only; keep, document as adapter-owned. |
| `scripts/claude_code_stop_hook.py` | Claude Code Stop-hook → Open Brain session capture (the Claude MCP client leg). | **Adapter** (integration script). |
| `scripts/hermes_update_guard.py` | Pre/post update guard: branch/dirty checks, Open Brain MCP tool-floor (≥30) probe, gateway health freshness. | **Adapter/ops — Phase 5c.4 groundwork already exists.** |
| `scripts/hermes_health_monitor.py` | Gateway health monitor (references Open Brain connectivity). | **Adapter/ops** (could be partly generic; low risk, fork-only). |

## C. Tests and docs (fork-only; track with their subject)

Tests: `tests/gateway/test_capture_commands.py`,
`test_open_brain_capture_metadata.py`, `test_session_boundary_hooks.py`,
`test_telegram_open_brain_feedback.py`, `test_claude_code_stop_hook.py`,
`test_runner_startup_failures.py`; `tests/plugins/memory/test_openbrain_provider.py`,
`tests/plugins/test_openbrain_query_brain_format_plugin.py`;
`tests/scripts/test_hermes_update_guard_feedback_wiring.py`. These become the
**conformance smoke** (Phase 5c.4) and must keep passing through the seam moves.

Docs: `docs/hermes-update-runbook.md`, `docs/openbrain-session-capture-status.md`,
`docs/unified-retrieval-architecture.md`, plus this file.

## Result (Phase 5c.1 acceptance)

No Open Brain behavior in Hermes remains unclassified. Every core-file edit has a
destination:

- **Three upstream seams unblock all 5 modified core files:**
  1. **Full-fidelity session-boundary event** — full message list + boundary
     reason (`manual_capture` / `reset` / `shutdown`) + source metadata + session
     id (the existing `session:end`/`session:reset` hooks truncate to 500 chars).
     Unblocks `gateway/run.py` capture policy and `cli.py`.
  2. **Slash-command registration + reply-returning command handler** — unblocks
     `hermes_cli/commands.py`, the `run.py` command dispatch, and `cli.py` `/ob`.
  3. **Platform message-decoration + callback/action-handler seam** — unblocks
     the `telegram.py` feedback buttons/callbacks and deletes the
     `gateway/platforms/base.py` shim.
- **Adapter already exists in two clean cases** (memory provider, query-brain
  formatter); the rest of the Open Brain client code (`gateway/open_brain.py`,
  `open_brain_feedback.py`, `tools/strava_tool.py`, the scripts) is fork-only and
  only needs **relocation under one adapter package**, not redesign.
- **One shim** (`gateway/platforms/base.py`, 12 lines) is scheduled for deletion
  once seam 3 lands.

**Next step (Phase 5c.2):** specify the three upstream seams as generic Hermes
hooks/events that do not mention Open Brain, and prefer upstream PRs or isolated
generic fork commits over Open Brain-specific patches in Hermes core. The
highest-leverage seam is #1 (full session-boundary event): it is the single
largest core delta (`gateway/run.py`, +675) and the one most likely to be
silently dropped by an upstream update.
