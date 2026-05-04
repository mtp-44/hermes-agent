# Hermes ↔ Open Brain Session Capture Status

Last updated: 2026-05-04

This note documents the current implementation state of Hermes session-boundary
and manual capture into Open Brain, what was verified live, and what still
needs work.

## Goal

Hermes remains the only main interface.
Open Brain remains the canonical durable memory layer.

The implemented paths are:

`Telegram/CLI/TUI/session boundary -> Hermes capture policy -> Open Brain MCP durable write`

and

`/ob manual snapshot -> Hermes capture policy -> Open Brain MCP durable write`

## What Is Implemented

### 1. Shared capture context and policy

Hermes now builds a structured session-boundary capture context in:

- [agent/session_capture.py](/Users/mh/ai/agents/hermes-agent/agent/session_capture.py:1)

This includes:

- boundary metadata
- provenance
- stable `boundary_id`
- lightweight `message_refs`
- first-pass extraction into:
  - `session_summary`
  - `action_item`
  - `decision_record`
  - `durable_fact`
- confidence + `routing` (`canonical` or `pending`)

It also persists local JSON artifacts to:

- `$HERMES_HOME/session_captures/<boundary_id>.json`

### 2. Session-boundary integration in Hermes

Hermes now threads the capture context through existing session-boundary paths:

- [run_agent.py](/Users/mh/ai/agents/hermes-agent/run_agent.py:4625)
- [cli.py](/Users/mh/ai/agents/hermes-agent/cli.py:709)
- [gateway/run.py](/Users/mh/ai/agents/hermes-agent/gateway/run.py:2453)
- [tui_gateway/server.py](/Users/mh/ai/agents/hermes-agent/tui_gateway/server.py:283)

The memory provider interface was upgraded to allow:

- legacy providers: `on_session_end(messages)`
- opt-in providers: `on_session_end(messages, capture_context=...)`

Relevant files:

- [agent/memory_provider.py](/Users/mh/ai/agents/hermes-agent/agent/memory_provider.py:151)
- [agent/memory_manager.py](/Users/mh/ai/agents/hermes-agent/agent/memory_manager.py:394)

### 3. Open Brain memory provider

A first-pass external memory provider now exists at:

- [plugins/memory/openbrain/__init__.py](/Users/mh/ai/agents/hermes-agent/plugins/memory/openbrain/__init__.py:1)
- [plugins/memory/openbrain/plugin.yaml](/Users/mh/ai/agents/hermes-agent/plugins/memory/openbrain/plugin.yaml:1)

Current scope:

- consumes `capture_context["capture_records"]`
- writes each record into the hosted `open-brain-mcp`
- uses `tools/call -> capture_thought`
- stores a local sync ledger at:
  - `$HERMES_HOME/openbrain_sync.json`

Current auth/config sources:

- `OPENBRAIN_MCP_KEY`
- fallback `MCP_ACCESS_KEY`
- `OPENBRAIN_MCP_URL`
- fallback hosted default
- fallback read from `$HERMES_HOME/.env`

### 4. Gateway reset fix for Telegram

There was a real Telegram failure mode:

- user sends a message
- user sends `/new` quickly
- gateway invalidates the active run before the normal transcript flush completes
- session-close capture sees no meaningful history

That is now patched in:

- [gateway/run.py](/Users/mh/ai/agents/hermes-agent/gateway/run.py:6816)

The fix attempts a pre-reset `commit_memory_session(...)` using the old
agent's in-memory `_session_messages` before cleanup and session reset.

### 5. Manual `/ob` capture without reset

Hermes now supports a manual durable-memory capture command:

- `/ob`

Current semantics:

- captures durable memory from the current conversation without resetting it
- first `/ob` in a session captures from the start of the current conversation
- later `/ob` calls capture only the uncaptured suffix since the last `/ob`
- conversation continuity remains intact after capture

Relevant files:

- [hermes_cli/commands.py](/Users/mh/ai/agents/hermes-agent/hermes_cli/commands.py:65)
- [cli.py](/Users/mh/ai/agents/hermes-agent/cli.py:5016)
- [gateway/run.py](/Users/mh/ai/agents/hermes-agent/gateway/run.py:6957)
- [gateway/session.py](/Users/mh/ai/agents/hermes-agent/gateway/session.py:425)

## Live Verification Performed

The following were verified live against the real hosted Open Brain MCP
endpoint and the real Telegram gateway:

### Gateway/runtime

- Hermes gateway restarted successfully
- Telegram connected successfully
- `memory.provider: openbrain` is active in the real Hermes home

### Open Brain provider

- provider discovered successfully in the real Hermes runtime
- provider marked `is_available() == True`
- live `capture_thought` write succeeded
- live `search_thoughts` read-back succeeded

### Session-end provider path

A direct provider-level `on_session_end(...)` smoke test succeeded and
produced synced record IDs in:

- [openbrain_sync.json](/Users/mh/.hermes/openbrain_sync.json:1)

### Real Telegram reset-path test

A real Telegram conversation about Signal vs WhatsApp followed by `/new`
produced a successful `session_reset` artifact:

- [e9cdb150f200862e0bfcd60f.json](/Users/mh/.hermes/session_captures/e9cdb150f200862e0bfcd60f.json:1)

That artifact shows:

- `eligible: true`
- `session_summary: 1`
- `action_item: 2`
- `decision_record: 1`
- `durable_fact: 1`

This confirms the reset-path capture fix works for the Telegram `/new` case.

## Current Behavior

### Conversation vs durable capture

Hermes now has two different user intents represented separately:

- `/new` or `/reset`
  - end the current conversation
  - trigger session-boundary capture
  - start fresh
- `/ob`
  - capture durable memory from the current conversation
  - do not reset the thread
  - continue the conversation immediately afterward

This separation avoids overloading `/new` with both "remember this" and
"stop this conversation."

### Durable write model

The current Open Brain MCP surface available to Hermes is:

- `capture_thought(content, domain, category, subcategory)`

So Hermes currently stores each capture record as a structured thought entry
in Open Brain, not as first-class native `action_item` or `decision_record`
tables.

The structured metadata is embedded in the captured thought content so the
record remains reconstructible.

### Dedup model

Current protection exists at two layers:

1. local stable `boundary_id`
2. local synced-record ledger in `openbrain_sync.json`

For `/ob`, Hermes also keeps a rolling local cursor so repeated manual
captures in the same conversation only process newly added messages.

This is enough to avoid naive duplicate retries from Hermes itself, even
though full content-fingerprint dedup in Open Brain is not implemented yet.

## Known Issues

### 1. Extraction quality needs tightening

The system works, but the current heuristic extractor is still too broad.

Observed on the real Telegram Signal example:

- good:
  - durable fact captured
  - decision captured
  - real user action item captured
- not ideal:
  - assistant-generated follow-up text was also captured as an action item
  - the durable fact `value` swallowed too much trailing sentence content

### 2. Duplicate local artifacts can still happen across boundary reasons

For the same logical session, both of these may appear:

- `session_reset`
- later `gateway_shutdown`

Open Brain remote duplication is mitigated by the local sync ledger, but the
local artifact store may still contain multiple related boundary captures.

### 3. Retrieval arbitration is not implemented yet

Open Brain writes now work.
Source-aware retrieval across:

- `session_memory`
- `open_brain`
- `synthesized_from_both`
- `inference`

is still pending.

## Files To Know

### Core implementation

- [agent/session_capture.py](/Users/mh/ai/agents/hermes-agent/agent/session_capture.py:1)
- [agent/memory_manager.py](/Users/mh/ai/agents/hermes-agent/agent/memory_manager.py:394)
- [agent/memory_provider.py](/Users/mh/ai/agents/hermes-agent/agent/memory_provider.py:151)
- [run_agent.py](/Users/mh/ai/agents/hermes-agent/run_agent.py:4625)
- [gateway/run.py](/Users/mh/ai/agents/hermes-agent/gateway/run.py:6816)

### Open Brain provider

- [plugins/memory/openbrain/__init__.py](/Users/mh/ai/agents/hermes-agent/plugins/memory/openbrain/__init__.py:1)
- [plugins/memory/openbrain/plugin.yaml](/Users/mh/ai/agents/hermes-agent/plugins/memory/openbrain/plugin.yaml:1)

### Tests

- [tests/agent/test_memory_provider.py](/Users/mh/ai/agents/hermes-agent/tests/agent/test_memory_provider.py:336)
- [tests/agent/test_session_capture.py](/Users/mh/ai/agents/hermes-agent/tests/agent/test_session_capture.py:1)
- [tests/plugins/memory/test_openbrain_provider.py](/Users/mh/ai/agents/hermes-agent/tests/plugins/memory/test_openbrain_provider.py:1)

## How To Real-World Test

1. Send a meaningful Telegram message with:
   - one preference
   - one decision
   - one next step
2. If you want to preserve the conversation and keep going, use `/ob`
3. If you want to close the session and start fresh, use `/new` or `/reset`
4. Check:
   - latest file in `$HERMES_HOME/session_captures/`
   - [openbrain_sync.json](/Users/mh/.hermes/openbrain_sync.json:1)
5. Optionally query Open Brain for the phrase you used

## Recommended Next Work

Before moving into retrieval, tighten capture quality:

1. prevent assistant-only suggestions from becoming action items unless the user adopts them
2. tighten durable-fact extraction so values stop at the intended clause
3. separate user decision text from trailing action text more cleanly
4. only then move on to retrieval arbitration
