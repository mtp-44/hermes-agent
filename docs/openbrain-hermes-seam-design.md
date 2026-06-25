# Hermes Extension Seam Design (Phase 5c.2)

Status: **design for review** (no code yet). Implements Phase 5c.2 of
[`OPEN_BRAIN_9PLUS_PLAN.md`](../../OPEN_BRAIN_9PLUS_PLAN.md) §5c.2.

Builds on the touchpoint inventory in
[`openbrain-hermes-touchpoint-inventory.md`](openbrain-hermes-touchpoint-inventory.md).
Assistant-neutral. **Delivery decision (user, 2026-06-25):** land each seam as an
**isolated, generic, Open-Brain-free fork commit** on `main` — self-contained and
immediately usable, upstreamable to `NousResearch/hermes-agent` later. Every seam
below is specified so its commit mentions no Open Brain concept.

## Correction to the 5c.1 inventory

Reading the upstream subsystems changed two of the three "missing seam" claims.
Two seams **already exist upstream**; the fork simply does not route its own
behavior through them. Only one seam is genuinely absent. This *reduces* the
5c.2 upstream surface and moves most of the work to 5c.3 (relocate to adapter)
and 5c.5 (delete core edits).

| Inventory claim (5c.1) | Reality after reading upstream | Real 5c.2 gap |
|---|---|---|
| Seam #1 session-boundary event missing | `MemoryProvider.on_session_end(messages, capture_context={boundary_reason, platform, session_id, …})` **exists** (`agent/memory_provider.py:165`), is driven by `agent/memory_manager.py:729`, and the openbrain provider **already implements it** (`plugins/memory/openbrain/__init__.py:145`). The 500-char truncation is only on the `gateway/hooks.py` *event* hooks, not this callback. | **Narrow:** the gateway only delivers `on_session_end` to a **resident** agent's provider. Evicted/idle/expired sessions and gateway shutdown bypass it, so the fork built a parallel Open-Brain capture in `run.py`. Gap = a generic gateway boundary-capture that drives the provider for non-resident sessions. |
| Seam #2 reply-returning command handler missing | `command:*` hooks with `emit_collect` (return-value-collecting) **exist** (`gateway/hooks.py:200`), and `run.py:7983` already fires `command:{canonical}` and uses the results. | **Narrow:** external **command registration** — `CommandDef`s are hardcoded in `hermes_cli/commands.py`; an adapter cannot add `/ob` etc. to the registry/help, and the dispatch still hardcodes each handler. |
| Seam #3 platform decoration + callbacks missing | Confirmed missing. No upstream way to attach inline buttons to an outbound message or route their presses back. | **Real seam to add.** This is the bulk of the genuine 5c.2 work. |

## Seam 1 — Generic gateway session-boundary capture

**Problem.** `AIAgent`/`MemoryProvider` instances are per-session and the gateway
evicts idle ones (LRU + idle-TTL). At gateway boundaries — Telegram `/reset`,
session expiry, gateway shutdown — the provider is frequently **not resident**, so
`on_session_end` never fires for that session. The fork worked around this with
`run.py:_capture_session_summary_if_eligible` (+ `/nosave` / `/private` /
`capture-status` policy), which imports `gateway.open_brain.save_session_summary`
directly — Open Brain wired into gateway core.

**Generic seam (Open-Brain-free).** A gateway boundary-capture helper that, for
any ending session, reconstructs the transcript and invokes the **configured
memory provider's** existing `on_session_end(messages, capture_context=…)`:

- `capture_context` carries the generic boundary metadata the provider interface
  already documents: `boundary_reason` (`reset` | `expiry` | `shutdown` |
  `manual`), `platform`/`origin`, `session_id`, and user identity when known.
- **Privacy policy is generic, not Open Brain.** The per-session capture flags
  (`capture_nosave`, `capture_private`, `capture_eligible`) and the
  `/nosave` `/private` `/capture-status` commands are ordinary capture-consent
  controls that belong in Hermes core. The gateway computes eligibility and
  passes the flags through `capture_context`; an ineligible session simply isn't
  delivered to the provider (or is delivered with `capture_eligible: false` so a
  provider can honor a softer policy). No provider names appear.
- The provider (the Open Brain adapter, in our case) decides what a boundary
  means — extract, summarize, dedup — exactly as `on_session_end` already allows.

**Commit shape.** New core helper (e.g. `gateway/session_capture.py` or a method
on the runner) that loads the transcript from `session_store` and calls
`memory_manager`'s boundary path with `capture_context`. Generic; no `open_brain`
import. Wire it at the three existing boundary sites (`run.py:~4594` shutdown,
`~6052` reset, plus expiry/eviction) **in place of** the Open-Brain-specific call.

**What this deletes later (5c.3/5c.5).** `run.py`'s `_capture_session_summary_if_eligible`
Open-Brain import collapses to the generic provider invocation; the `+675` delta
shrinks to generic policy code. `cli.py`'s `_capture_open_brain_snapshot` (manual
`/ob`) routes through the same helper with `boundary_reason="manual"`.

**Acceptance.** A gateway `/reset` / expiry / shutdown on a session whose agent was
evicted still drives the configured memory provider's `on_session_end` with full
messages + boundary metadata + privacy flags, with no `open_brain` symbol in any
modified core file.

## Seam 2 — External slash-command registration — **NO CORE GAP (corrected 2026-06-25)**

**Original premise (wrong).** The 5c.1 inventory assumed external command
registration was a missing core seam.

**Reality after reading the code.** Upstream **already has a complete external
slash-command system**, and it is richer than the `command:*`/HOOK.yaml path:

- `hermes_cli.plugins.PluginContext.register_command(name, handler,
  description, args_hint)` (`hermes_cli/plugins.py:415`) registers an in-session
  slash command whose `handler(raw_args) -> str | None` returns the reply
  directly. Stored in the plugin manager's `_plugin_commands`.
- `is_gateway_known_command()` (`hermes_cli/commands.py`) already returns True
  for plugin-registered commands via `_iter_plugin_command_entries()`, so they
  receive the full `command:<name>` lifecycle, slash-access gating, and menu
  surfacing (Telegram/Slack/Discord) — identical to built-ins.
- The gateway **already dispatches them**: `gateway/run.py:8351` does
  `get_plugin_command_handler(name)` → `handler(user_args)` → awaits if async →
  `return str(result)` as the reply.
- The `command:<name>` `emit_collect` decision path (`run.py:7983`/`8017`) is a
  *separate, complementary* hook for deny/handled/rewrite policy.

**Conclusion.** There is **no generic core change to make for seam 2.** What
remains is entirely Phase 5c.3 adapter work + 5c.5 deletion:

- Register `/ob /note /brief /digest /stale /nosave /private /capture-status` in
  an Open Brain plugin's `register(ctx)` via `ctx.register_command(...)`, each
  with a handler that returns the reply (the bodies move out of `run.py` /
  `gateway/slash_commands.py` largely unchanged).
- Delete the eight `CommandDef` rows from `hermes_cli/commands.py` and the eight
  hardcoded `if canonical == …` branches from the gateway dispatch.

> **Supersedes the 2026-06-25 "extend HOOK.yaml" decision for seam 2.** That
> decision presumed a core registration gap that does not exist. The plugin
> system already satisfies "one adapter package owns its commands + handlers."
> HOOK.yaml's only true limitation (it can declare `events:` but not
> `commands:`) is moot here because the plugin system — already the home of
> `plugins/memory/openbrain` and `plugins/openbrain-query-brain-format` — is the
> natural and complete vehicle.

**Acceptance (now a 5c.3 acceptance).** The eight commands work from an Open
Brain plugin with no `CommandDef`/dispatch edits in `hermes_cli/commands.py` or
the gateway core; deleting the plugin removes them cleanly.

## Seam 3 — Platform message decoration + callback/action handling

**Problem (the one genuinely missing seam).** Open Brain's 👍/👎 query feedback
and `prx:` proactive Useful/Dismiss buttons require attaching inline buttons to an
outbound message and routing the press back. Upstream has no interface for this,
so the fork hand-wrote it into `gateway/platforms/telegram.py` (+203) and added a
metadata pass-through shim in `gateway/platforms/base.py` (+12).

**Generic seam (Open-Brain-free).** Two platform-agnostic capabilities:

1. **Outbound decoration.** Let a producer attach a generic `actions` structure
   to an outbound message — a list of `{label, action_id, payload-token}` — that
   the platform layer renders natively (Telegram → `InlineKeyboardMarkup`; other
   platforms → their equivalent or a no-op). A pre-send decoration hook
   (`message:outbound` or a `decorate_outbound(message, metadata) -> actions?`
   plugin hook) supplies it; the `base.py` pass-through becomes this generic hook.
2. **Inbound action dispatch.** When a user presses an action, the platform
   translates it into a generic `action:invoked` event (`action_id`, token,
   platform context) and routes it to the registered handler, whose return value
   (ack text / message edit) the platform applies. Telegram's
   `CallbackQueryHandler` becomes the translator; the callback-token registry and
   `obf:`/`prx:` parsing move out of core.

**Commit shape.** Add the generic outbound-`actions` field + decoration hook to
`gateway/platforms/base.py` and an `action:invoked` dispatch path; implement the
Telegram translation in `telegram.py` generically (render actions ↔ inline
keyboard, callback ↔ `action:invoked`). No `open_brain` import, no `obf:`/`prx:`
literals in core.

**What this deletes later.** All Open Brain feedback specifics (button labels,
`record_query_feedback`/`record_proactive_feedback` calls, token registry,
`stage/pop/attach_open_brain_feedback`) move into the adapter as one consumer of
the generic decoration + action seam; the `base.py` shim is deleted.

**Acceptance.** A generic test producer attaches two actions to a Telegram
message, a simulated press yields an `action:invoked` with the right `action_id`
and token, and the handler's reply is applied — with no Open Brain reference in
`telegram.py` or `base.py`.

## Sequencing (recommended)

1. **Seam 1** first — largest, most update-fragile delta (`run.py` +675) and it
   mostly *removes* code by routing to an existing provider callback.
2. **Seam 2** — small registry change; unblocks moving eight handlers to the
   adapter.
3. **Seam 3** — the only net-new subsystem; do last, it's self-contained.

Each lands as its own generic fork commit with tests, **before** the matching
5c.3 adapter move, so core and adapter are never broken in the same commit. The
existing fork-only tests (`tests/gateway/test_session_boundary_hooks.py`,
`test_capture_commands.py`, `test_telegram_open_brain_feedback.py`, etc.) become
the regression net and graduate into the 5c.4 conformance smoke.

## Open questions for review

1. **Seam 1 eligibility semantics** — **RESOLVED 2026-06-25 (user): match current
   fork behavior.** Skip the provider call entirely for `nosave`; deliver with
   `capture_eligible: false` for `private` and let the provider decide. The
   gateway computes eligibility; the provider never overrides a `nosave` skip.
2. **Seam 2 registration transport** — **RESOLVED 2026-06-25 (user): extend the
   `~/.hermes/hooks/HOOK.yaml` discovery** so one adapter package owns its
   commands and handlers together (no second discovery mechanism). `CommandDef`
   rows are contributed via the HOOK.yaml manifest and merge into the registry
   used by `/commands`/help; dispatch falls through to `emit_collect`.
3. **Seam 3 action-token lifetime** — still open: keep the bounded in-memory token
   registry (current fork behavior, 200-entry LRU) as the generic default, or make
   persistence pluggable? (Recommend: generic in-memory default, pluggable later.)
   Decide at seam-3 implementation time.

---

## Phase 5c.3 adapter-relocation feasibility (added 2026-06-25)

After the three generic-core seams landed, reading the plugin/command system
surfaced two constraints that shape how Open Brain behavior can move into an
adapter. Recorded here so 5c.3 is executed with eyes open.

### Constraint 1 — plugin command handlers get only `raw_args`

`PluginContext.register_command(name, handler, …)` dispatches `handler(raw_args)
-> str | None` (`gateway/run.py:8351`). That is enough for the **read-only** Open
Brain commands, which need only the query string and a `gateway.open_brain` call:

- `/brief`, `/digest`, `/stale`, `/finance-check` — cleanly movable.

It is **not** enough for the **session-aware** commands:

- `/ob` (capture the conversation suffix) needs the session transcript.
- `/note` needs `event.source` (capture metadata) + the session's `capture_private`
  flag.

Options for the session-aware pair: (a) leave them as documented gateway glue;
(b) add a small generic upstream seam giving plugin command handlers a richer
context object (`event`/source/session), which also benefits any plugin. This is
a genuine follow-on seam, not mechanical relocation.

### Constraint 2 — standalone plugins are opt-in via `plugins.enabled`

`PluginManifest.kind` defaults to `standalone`, which loads **only when listed in
`plugins.enabled`** in `config.yaml` (`hermes_cli/plugins.py:248`). `backend` and
`platform` kinds auto-load; `standalone` does not. So moving commands into a new
bundled plugin makes the gateway's `config.yaml` part of the adapter contract: if
the `plugins.enabled` entry is missing on the live host (mini-mh), the migrated
commands silently disappear. This must be coordinated with the deploy and covered
by the 5c.4 update rehearsal.

### Recommended 5c.3 sequencing

1. Migrate the four read-only commands into a bundled `standalone` Open Brain
   commands plugin; remove their `CommandDef`s + hardcoded dispatch branches from
   core; add the `plugins.enabled` entry; verify on a fresh load. **Not deployed**
   until the rehearsal confirms the commands survive a gateway restart.
2. Migrate `obf:`/`prx:` feedback onto the seam-3 `actions` protocol
   (`set_action_handler` + `metadata["actions"]`); then delete the bespoke
   telegram feedback code + the `base.py` shim.
3. Decide the session-aware command pair (`/ob`, `/note`): richer-context seam vs
   retained core glue.
4. Relocate `gateway/open_brain.py` / `open_brain_feedback.py` under the adapter
   package and delete `save_session_summary`/`distill_session_transcript` (now
   dead after seam 1).

The consent commands `/nosave`, `/private`, `/capture-status` are **generic
capture-consent controls, not Open Brain behavior** — they stay in core.
