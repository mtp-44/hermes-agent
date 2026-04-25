# open_brain + Hermes Agent — Planning Log

> **Canonical document for Hermes integration planning around open_brain and the Hermes/Ollama local AI stack.**
> Each session appends a dated entry. Always append here — never create separate files for this topic.

Documentation precedence:
- `~/ai/README.md` defines cross-project documentation order
- `/Users/mh/ai/open_brain/README.md` owns the canonical `open_brain` architecture
- this file owns integration planning only and must defer to `open_brain/README.md` for the MCP contract

---

## Stable Context (update when fundamentals change)

**Hardware:** Mac mini M4 Pro 48GB

**open_brain** (`/Users/mh/ai/open_brain`) — DO NOT MODIFY:
- Telegram capture → classify (GPT-4o mini) → confirm → save pipeline
- Supabase + pgvector: `thoughts`, `contacts`, `finance_records`, `home_items`, `life_items`, `records`, `user_memory`
- Canonical MCP design: Supabase Edge Function `open-brain-mcp`, authenticated via `MCP_ACCESS_KEY` / `x-brain-key`
- Retrieval QA already local (Qwen 3.5 via LM Studio)
- GPT-4o mini classifier: keep — cheap (~$0.001/call), fast, working. No local replacement.

**Local LLM stack:**
- Ollama serving `qwen3.6:27b`
- Hermes Agent (NousResearch) as the autonomous orchestration layer
- Hermes status: **not yet installed** (as of 2026-04-23)

---

## What Hermes Actually Is (agreed mental model)

Hermes sits at the intersection of three data sources and reasons across them:

```
[Web research]  [Calendar, read-only]  [open_brain MCP]
        ↘               ↓                ↙
              [Hermes + Qwen3.6:27b]
                       ↓
              [Telegram: push + pull]
```

- **open_brain**: your knowledge base — what you know, care about, have logged
- **Calendar**: your schedule — recurring events, upcoming commitments (read-only; to be set up)
- **Web**: ambient research — monitoring sources and topics Hermes knows you follow

Hermes connects these three into time-aware, actionable intelligence. It is not a duplicate of open_brain and not just a cron job wrapper.

**Canonical example:**
> Hermes knows (from open_brain thoughts) that you care about sprint planning. It monitors sources like The Liberators. It finds a new planning technique article. It checks the calendar, sees sprint planning is next week. Morning brief flags the article and suggests reading it by Thursday — enough time to build a Miro board.

---

## Memory Architecture (decided — important)

Two memory systems, different scopes, no conflict:

| Memory | Where | What it holds |
|---|---|---|
| Hermes operational memory | Internal to Hermes | Preferences, monitored topics, past suggestions, working state |
| open_brain knowledge memory | `user_memory` + `thoughts` tables | Life/work facts, contacts, events, decisions |

**Rule:** Hermes writes back to open_brain via MCP only when something is worth keeping permanently. Its own memory is working/operational state only.

---

## Delivery Model (decided)

- **Push**: Scheduled routines (morning brief, weekly digest, proactive flags) → Telegram
- **Pull**: Ad-hoc Q&A — you ask Hermes, it reasons over open_brain + web context and responds

---

## Session: 2026-04-23

### Decisions made

- Hermes as the platform (not custom Python scripts) — coherence, built-in memory, single system
- open_brain untouched — Hermes reads/writes only via MCP
- GPT-4o mini classifier stays — cheap and working, no local replacement needed
- Two-memory model agreed (see above)
- Calendar integration: user will maintain a calendar for recurring events; Hermes reads it (read-only)
- First priority: scheduled routines with intelligent cross-source reasoning (not just data dumps)

### Setup path (Phase 0)

1. Install Hermes Agent — `hermes-agent.nousresearch.com/docs/getting-started/quickstart`
2. Configure Ollama provider — `localhost:11434`, model `qwen3.6:27b`
3. Register open_brain MCP server — `https://icxyfzzbsrsiyaqnynum.supabase.co/functions/v1/open-brain-mcp` + `MCP_ACCESS_KEY` (`x-brain-key`)
4. Smoke test — ask Hermes something requiring `search_thoughts`; verify tool call + Qwen reasoning works

### Phase 1: Core Routines

| Routine | Schedule | What Hermes does |
|---|---|---|
| Morning brief | 07:30 daily | Pulls open_brain (thoughts, upcoming items) + calendar + any flagged web findings → synthesised, time-aware summary → Telegram |
| Weekly finance digest | Sunday 20:00 | Aggregates week's `finance_records`, flags anomalies, totals by category |
| Stale contact scan | Monday 08:00 | Finds contacts with no activity in 60+ days → Telegram nudge |
| Overnight distillation | 02:00 nightly | Clusters recent uncategorised thoughts, writes back summary notes via MCP |

### Phase 2: Ambient Research + Calendar Awareness

- **Topic monitoring**: Hermes tracks sources/topics derived from open_brain interests (e.g. The Liberators for Scrum content)
- **Time-aware suggestions**: Connect new findings to upcoming calendar events with actionable deadlines
- **Calendar setup**: Decide on calendar format/tool (Google Calendar most likely) and wire up read-only access for Hermes

### Open questions (carry into next session)

- Does Hermes support Telegram natively as an output channel, or does it need a bridge?
- How does Hermes handle MCP auth headers (`MCP_ACCESS_KEY`)?
- Which calendar tool? Google Calendar API is the obvious choice — confirm before wiring up.
- Qwen3.6 tool use quality — verify in Phase 0 smoke test before building real routines on top.

---

## Session: 2026-04-24 16:03:42 CEST

### Decisions made

- Treat `open_brain` as foundational infrastructure that must be stabilized before Hermes is installed/configured against it
- Reframe Phase 0 so the first goal is a reliable local `open_brain` MCP contract, not a fast Hermes bootstrap
- Optimize for clean, stable, long-term usability rather than ad-hoc setup or temporary bootstrap paths

### Findings from machine audit

- `hermes-plan.md` existed at `/Users/mh/ai/agents/hermes-plan.md`; `/Users/mh/ai/agents/hermes-agent` was updated and synced to `origin/main`
- Hermes is not yet installed in a usable way on this machine
- The checked-in local `open_brain` MCP server is `mcp_server.py`
- The current local MCP endpoint exposed by that server is `http://127.0.0.1:8766/sse`
- The current local Python MCP server does not appear to implement `MCP_ACCESS_KEY` auth
- References to `MCP_ACCESS_KEY` do exist elsewhere in `open_brain`, but they appear to describe a different secured deployment path rather than the current local Python server
- At audit time, neither Ollama on `127.0.0.1:11434` nor the local `open_brain` MCP endpoint on `127.0.0.1:8766` was responding
- Ollama is installed, but no running daemon/model was available during the check

### Implication

- There is drift between the planning assumption and the actual local machine state
- Installing Hermes before resolving that drift would make debugging harder and would blur whether failures belong to Hermes, Ollama, or `open_brain`

### Revised Phase 0

1. Stabilize `open_brain` as a local service first
2. Verify the canonical MCP endpoint, transport, and auth model actually used locally
3. Define a reliable startup method for `open_brain` MCP
4. Run direct smoke tests against `open_brain` tools such as `search_thoughts`
5. Only after that, install/configure Hermes against the known-good local interface

### open_brain readiness checklist

- Canonical hosted MCP endpoint is documented and confirmed
- Auth model is explicit and verified: `x-brain-key` with `MCP_ACCESS_KEY`
- Startup method is reliable and repeatable
- Health check is defined
- Core MCP tool calls succeed directly before Hermes is introduced
- The canonical contract Hermes should use is documented in `open_brain/README.md`

### Working principle going forward

- Prefer standard, maintainable setup paths over temporary bootstrap flows
- Make short-term decisions that remain usable in the mid and long term
- Do not connect Hermes to `open_brain` until `open_brain` is behaving consistently on its own

---

## Session: 2026-04-24 16:14:00 CEST

### Decisions made

- Keep the original `open_brain` MCP design as canonical
- Canonical MCP path for Hermes is the hosted Supabase Edge Function `open-brain-mcp`
- Treat the checked-in local Python MCP server as non-canonical and isolate it so it does not get confused with the real design

### Canonical MCP contract going forward

- Endpoint: `https://icxyfzzbsrsiyaqnynum.supabase.co/functions/v1/open-brain-mcp`
- Auth: `x-brain-key` header using `MCP_ACCESS_KEY`
- Role: stable shared data layer for Hermes and other MCP-compatible clients

### Isolation rule

- Anything local and Python-based that imitates MCP for `open_brain` must be marked experimental/non-canonical
- Hermes planning, setup, and smoke tests should target the hosted Supabase Edge Function design, not a local SSE server on the Mac mini
- The former top-level local Python MCP prototype has been moved to `open_brain/experimental/local_mcp_server.py`

### Revised Phase 0 note

1. Verify the hosted `open-brain-mcp` Edge Function is healthy and authenticated correctly
2. Verify `MCP_ACCESS_KEY` handling with a direct smoke test
3. Only then configure Hermes against that hosted MCP endpoint

---

## Session: 2026-04-24 16:14:42 CEST

### Decisions made

- `~/ai` now needs an explicit documentation hierarchy so cross-project docs cannot silently drift
- `open_brain/README.md` is the single source of truth for the `open_brain` MCP contract
- `hermes-plan.md` is the single source of truth for Hermes integration planning, but not for redefining `open_brain` architecture
- `open_brain_ideas/` is archival context, not operational truth

### Documentation governance rule

- When documents disagree, prefer workspace-level README, then project README, then active plan, then notes, then ideas/research
- Integration plans must reference upstream architecture docs instead of restating them independently when possible

---

## Session: 2026-04-24 16:22:19 CEST

### Phase 0 verification results

- Hosted endpoint `https://icxyfzzbsrsiyaqnynum.supabase.co/functions/v1/open-brain-mcp` is live and reachable from this machine
- Unauthenticated request returns `401` with `{"error":"Invalid or missing access key"}` as expected
- Authenticated request with the currently documented key changes behavior from `401` to protocol handling, which strongly indicates the key is accepted
- MCP transport requires `Accept: application/json, text/event-stream` or equivalent; otherwise the server returns `406 Not Acceptable`
- A minimal authenticated MCP `initialize` request succeeded with `200` and returned:
  - server name: `open-brain`
  - server version: `1.0.0`
  - protocol version: `2025-03-26`
  - capability signal: `tools.listChanged = true`

### Important drift discovered

- The deployed/checked-in TypeScript MCP implementation visible in this repo is `/Users/mh/ai/open_brain/wom/supabase/functions/work-operating-model-mcp/index.ts`
- That implementation exposes operating-model tools such as `start_operating_model_session`, `save_operating_model_layer`, `query_operating_model`, and `generate_operating_model_exports`
- This does not match the planning assumption that the hosted `open-brain-mcp` surface is centered on `search_thoughts` and general knowledge retrieval
- No checked-in `open-brain-mcp` TypeScript function source was found in the current repo snapshot

### Implication

- The hosted MCP contract is healthy at the transport/auth level
- But the currently visible implementation and tool surface appear to be for the work operating model, not the broader `open_brain` retrieval contract described in older planning and idea docs
- Before configuring Hermes routines around `search_thoughts`, confirm whether:
  - the deployed `open-brain-mcp` endpoint intentionally now fronts the operating-model server, or
  - the canonical README is ahead of the checked-in source, or
  - a separate `open-brain-mcp` function source exists outside this repo snapshot

### Next recommended step

1. Resolve the contract drift by identifying the true source and intended tool surface behind `open-brain-mcp`
2. Only after that, wire Hermes to the endpoint and build routines against the confirmed tool set

---

## Session: 2026-04-24 16:24:44 CEST

### Contract drift resolution

- Queried the live hosted endpoint with authenticated MCP `tools/list`
- The live `open-brain-mcp` endpoint advertises the expected Open Brain tool surface, including:
  - `search_thoughts`
  - `list_thoughts`
  - `thought_stats`
  - `capture_thought`
  - `search_contacts`
  - `search_life_items`
  - `search_home_items`
  - `search_finance_records`
  - `search_records`
  - `search_thoughts_by_contact`
  - corresponding add, update, and delete tools for the structured tables
- This confirms the hosted endpoint contract is aligned with the older Open Brain retrieval/storage design rather than the checked-in work operating model function

### Updated interpretation

- The previous drift concern is now narrowed:
  - the live hosted `open-brain-mcp` contract is correct for Hermes planning
  - the missing piece is source visibility, not endpoint behavior
- In other words, the actual hosted MCP endpoint Hermes should use is healthy and exposes the expected knowledge tools
- The checked-in `wom/supabase/functions/work-operating-model-mcp/index.ts` appears to be a separate MCP server in the repo, not the live source behind `open-brain-mcp`

### New Phase 0 status

- Endpoint health: verified
- Auth behavior: verified
- MCP initialize handshake: verified
- Live tool surface for Hermes: verified
- Remaining gap before Hermes setup:
  - identify or document where the real deployed `open-brain-mcp` source is maintained, if source-of-truth code visibility is required

### Recommended next step

1. Move to Hermes-side configuration using the hosted `open-brain-mcp` endpoint and `x-brain-key` auth
2. Keep a note that the deployed source location is still not visible in this repo snapshot

---

## Session: 2026-04-25 18:43:48 CEST

### Setup completed

- Installed Ollama via Homebrew (`ollama` 0.21.2_1)
- Started Ollama as a Homebrew LaunchAgent: `homebrew.mxcl.ollama`
- Stopped the older app-backed daemon (`/Applications/Ollama.app/Contents/Resources/ollama serve`) that was occupying `127.0.0.1:11434`
- Confirmed the existing downloaded model is still visible to the Homebrew service:
  - `qwen3.6:27b`
  - model store: `/Users/mh/.ollama/models`
- Updated Hermes config at `~/.hermes/config.yaml`:
  - `model.default: qwen3.6:27b`
  - `model.provider: custom`
  - `model.base_url: http://localhost:11434/v1`
  - `model.context_length: 65536`
- Added `OLLAMA_CONTEXT_LENGTH=65536` to the Homebrew Ollama LaunchAgent and reloaded it with `launchctl`
- Confirmed running Ollama environment includes:
  - `OLLAMA_CONTEXT_LENGTH => 65536`
  - `OLLAMA_FLASH_ATTENTION => 1`
  - `OLLAMA_KV_CACHE_TYPE => q8_0`
- Confirmed `ollama ps` reports `qwen3.6:27b` loaded with `CONTEXT 65536` and 100% GPU

### Repo update status

- `/Users/mh/ai/open_brain`: `git pull --ff-only` succeeded; already up to date with origin, with one local commit ahead
- `/Users/mh/ai/agents/hermes-agent`: `git pull --ff-only` refused because local `main` has diverged from `origin/main`
  - local branch: ahead 1, behind 340 after fetch
  - no merge/rebase was performed

### Hermes/Open Brain verification

- `hermes mcp test open_brain` succeeded against the hosted endpoint:
  - endpoint: `https://icxyfzzbsrsiyaqnynum.supabase.co/functions/v1/open-brain-mcp`
  - auth header: `x-brain-key`
  - tools discovered: 26
- Ollama OpenAI-compatible endpoint succeeded:
  - `GET http://127.0.0.1:11434/v1/models`
  - returned `qwen3.6:27b`
- End-to-end Hermes smoke test succeeded:
  - command: read-only `hermes chat` query asking Hermes to search Open Brain for "sprint planning"
  - Hermes used `mcp_open_brain_search_thoughts`
  - result: 4 relevant thoughts found
  - session id: `20260425_183912_2bfa2a`
  - duration: 4m 27s

### Important note

- During setup, `brew services restart ollama` regenerated the plist and dropped the manually added `OLLAMA_CONTEXT_LENGTH`
- The stable current state was achieved by re-adding `OLLAMA_CONTEXT_LENGTH=65536` to `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` and reloading with:
  - `launchctl bootout gui/501/homebrew.mxcl.ollama`
  - `launchctl bootstrap gui/501 ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`
- If `brew services restart ollama` is used later, re-check that `OLLAMA_CONTEXT_LENGTH` is still present in the plist and running launchd environment

### New status

- Hermes is installed and configured enough for local Qwen + hosted Open Brain MCP read-only reasoning
- The Open Brain MCP contract is verified from Hermes
- Next practical step: configure Hermes messaging/gateway delivery to Telegram, then create the first scheduled routine

---

## Session: 2026-04-25 (git repo cleanup)

### hermes-agent repo — stable state

- Dropped the diverged local commit ("Add bundled skills and new tool integrations") — it contained upstream NousResearch content with minor regressions vs upstream, nothing user-specific
- Reset local `main` to `origin/main` — now fully in sync with upstream
- Created personal fork at `https://github.com/mtp-44/hermes-agent`
- Configured split remote on `origin`:
  - fetch: `https://github.com/NousResearch/hermes-agent.git` (upstream)
  - push: `https://github.com/mtp-44/hermes-agent.git` (personal fork)
- `git pull` / `git fetch origin` tracks upstream; `git push` goes to personal fork

### Caution

- `brew services restart ollama` regenerates the Homebrew plist and drops `OLLAMA_CONTEXT_LENGTH=65536`
- If Ollama is restarted via brew services, manually re-add the env var to `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` and reload with launchctl (see 2026-04-25 18:43:48 entry above)

### New status

- All repos clean and pushed: `open_brain` and `hermes-agent` fork both up to date
- Hermes stack is fully operational: Ollama + Qwen3.6:27b + hosted Open Brain MCP verified
- Next: Telegram delivery configuration, then first scheduled routine

---

## Session: 2026-04-25 (Telegram gateway setup)

### Context

- open_brain Telegram bot (`run_bot.py`, token `8648221297:...`) was already running as a persistent process since earlier
- Telegram only allows one poller per bot token — a separate bot was created via @BotFather for Hermes
- User's Telegram ID: `8406358795` (already known from open_brain)

### Setup steps

1. Created a new Telegram bot via @BotFather → obtained a new bot token
2. Added three env vars to `~/.hermes/.env`:
   - `TELEGRAM_BOT_TOKEN=<hermes-bot-token>`
   - `TELEGRAM_ALLOWED_USERS=8406358795`
   - `TELEGRAM_HOME_CHANNEL=8406358795`
3. Ran `hermes gateway start` — it registered as a launchd service (`ai.hermes.gateway`)
4. Initial startup failed: `python-telegram-bot not installed`
   - Root cause: Hermes was installed without the `messaging` extra
   - Fix: `uv pip install --python ~/.hermes/venv/bin/python ".[messaging]"` from the hermes-agent repo
   - This installed `python-telegram-bot==22.7` (and discord.py, slack-bolt, etc.)
5. Restarted gateway — started cleanly, stable PID, no errors in log
6. Smoke test: sent a message to the Hermes bot in Telegram → received a response (slow, ~expected for local Qwen3.6:27b cold start)

### Important notes

- The Hermes gateway runs as a launchd service: `ai.hermes.gateway`
- Logs: `~/.hermes/logs/gateway.log` and `~/.hermes/logs/gateway.error.log`
- `hermes gateway start / stop / restart / status` manages the service
- `OnDemand = true` in the plist — launchd manages restarts on crash
- First response from Qwen3.6:27b will be slow (cold model load); subsequent turns in the same session are faster

### Architecture note

- Two separate Telegram bots are now running concurrently on this machine:
  - `open_brain` bot (`8648221297:...`): capture pipeline — user sends → classify → confirm → save
  - Hermes bot (separate token): reasoning + delivery — pull Q&A and scheduled routines
- No conflict: each bot has its own token and polling loop

### New status

- Hermes is fully operational end-to-end: Telegram ↔ Hermes gateway ↔ Qwen3.6:27b ↔ open_brain MCP
- Next: create the first scheduled routine (morning brief at 07:30 daily)
