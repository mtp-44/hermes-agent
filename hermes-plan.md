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

---

## Session: 2026-04-26 (model recovery + Qwen3.6:35b-a3b migration)

### What broke

- Hermes gateway had been configured to use local Ollama at `http://localhost:11434/v1` with `qwen3.6:27b`
- Hermes had been working earlier in the day against that model
- Failure began at `2026-04-26 20:21:22` when Hermes started receiving:
  - `HTTP 404: model 'qwen3.6:27b' not found`
- Root cause: the local Ollama model state had been disrupted during an attempted model switch/removal; Hermes config still pointed at `qwen3.6:27b`, but Ollama no longer had that tag registered

### Initial forensics

- Confirmed Hermes config in `~/.hermes/config.yaml` still pointed to:
  - `model.default: qwen3.6:27b`
  - `model.provider: custom`
  - `model.base_url: http://localhost:11434/v1`
  - `model.context_length: 65536`
- Confirmed Ollama server was running, but:
  - `ollama list` returned no registered models
  - `ollama ps` returned no loaded model
  - `curl http://127.0.0.1:11434/api/tags` returned `{"models":[]}`
- Confirmed from `~/.hermes/logs/agent.log` that Hermes had been healthy earlier and only failed after the model disappeared

### Decision

- Switched target model from dense `qwen3.6:27b` to MoE `qwen3.6:35b-a3b`
- Chosen runtime target:
  - `context_length: 131072`
  - `ollama_num_ctx: 131072`

### Model pull and registration

- Pulled the model successfully with:
  - `ollama pull qwen3.6:35b-a3b`
- Verified registration:
  - `ollama list` showed `qwen3.6:35b-a3b`
  - `curl http://127.0.0.1:11434/api/tags` showed the same tag via the Ollama API

### Hermes config changes

- Updated `~/.hermes/config.yaml` to:

```yaml
model:
  default: qwen3.6:35b-a3b
  provider: custom
  base_url: http://localhost:11434/v1
  context_length: 131072
  ollama_num_ctx: 131072
```

- Verified `hermes status` reported:
  - model `qwen3.6:35b-a3b`
  - provider `Custom endpoint`

### Important runtime mismatch discovered

The config change alone was not enough.

During live tests:

- First successful Hermes CLI run returned `HERMES OK`, but `ollama ps` showed:
  - `CONTEXT 65536`
- After one restart path, a later successful Hermes CLI run still returned `HERMES OK`, but `ollama ps` showed:
  - `CONTEXT 32768`

This proved the model path was healthy, but the effective Ollama runtime context was not following Hermes config.

### Root cause of the context mismatch

Two separate launchd plist realities existed:

1. `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`
2. `/opt/homebrew/opt/ollama/homebrew.mxcl.ollama.plist`

Findings:

- The running Ollama process initially had:
  - `OLLAMA_CONTEXT_LENGTH=65536`
- After `brew services restart ollama`, the running service came back with:
  - `OLLAMA_CONTEXT_LENGTH=0`
- Ollama log then showed:
  - `vram-based default context ... default_num_ctx=32768`

This happened because Homebrew's source plist did not contain `OLLAMA_CONTEXT_LENGTH`, so `brew services restart ollama` wiped the manual override from the active launch agent.

### Final Ollama fix

- Added `OLLAMA_CONTEXT_LENGTH=131072` to:
  - `/opt/homebrew/opt/ollama/homebrew.mxcl.ollama.plist`
  - `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`
- Restarted Ollama with:
  - `brew services restart ollama`

Verified final live process environment:

- `ps eww -p <ollama-pid>` showed:
  - `OLLAMA_CONTEXT_LENGTH=131072`

Verified final Ollama server log:

- `server config` showed `OLLAMA_CONTEXT_LENGTH:131072`

### End-to-end verification

Ran a real Hermes request through the CLI:

- Command:
  - `hermes chat -Q --accept-hooks --ignore-rules -q "Reply with exactly: HERMES OK"`
- Successful final response:
  - `HERMES OK`
- Final verified session id:
  - `20260426_215539_612c1e`

While that request was live:

- `ollama ps` showed:
  - model `qwen3.6:35b-a3b`
  - `PROCESSOR 100% GPU`
  - `CONTEXT 131072`

This is the first fully confirmed state where:

- Hermes config points to `qwen3.6:35b-a3b`
- Hermes requests succeed end to end
- Ollama is actually allocating `131072` context at runtime

### Gateway service state

- Restarted Hermes gateway service after the model switch:
  - `hermes gateway restart`
- Verified launchd service `ai.hermes.gateway` is running again
- Verified the long-running gateway PID changed after restart, so the background service is no longer using stale pre-migration process state

### Stable current state

- Hermes default model: `qwen3.6:35b-a3b`
- Hermes provider: local custom Ollama endpoint
- Hermes context config: `131072`
- Hermes Ollama request context: `131072`
- Ollama service context cap: `131072`
- Ollama model registered: `qwen3.6:35b-a3b`
- Hermes gateway service: running

### Operational warning

If Ollama behavior ever regresses to `32768` or `65536` again, check these first:

1. `ps eww -p <ollama-pid>` for live `OLLAMA_CONTEXT_LENGTH`
2. `/opt/homebrew/opt/ollama/homebrew.mxcl.ollama.plist`
3. `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`
4. `/opt/homebrew/var/log/ollama.log` for:
   - `server config`
   - `vram-based default context`
   - `KvSize`

### New status

- Hermes is now running on `qwen3.6:35b-a3b`
- The local Ollama runtime is correctly honoring `131072` context
- End-to-end CLI verification is complete
- Background gateway service has been restarted onto the new model

---

## Session: 2026-04-27 (MLX enablement + comparison-model preparation)

### Goal

- Verify that Ollama's MLX backend works on this Apple Silicon machine
- Keep Hermes stable on the already-working `qwen3.6:35b-a3b` path
- Prepare a heavyweight MLX-side comparison model before considering any Hermes model switch

### Local MLX state discovered

- Installed versions:
  - `ollama 0.21.2`
  - `mlx-c 0.6.0_2`
- Problem discovered:
  - `/opt/homebrew/opt/mlx-c/lib/libmlxc.dylib` existed
  - `/opt/homebrew/opt/ollama/bin/libmlxc.dylib` did not
- This matched the known Homebrew/Ollama MLX dynamic-library issue on Apple Silicon: MLX runtime was present, but Ollama did not have the dylib where it expected to load it

### MLX enablement fix

- Added the missing symlink:

```bash
ln -sf /opt/homebrew/opt/mlx-c/lib/libmlxc.dylib /opt/homebrew/opt/ollama/bin/libmlxc.dylib
```

- Verified the symlink existed afterward
- Verified the Ollama API stayed healthy after the change

### MLX backend proof

Used a small official MLX-tagged model as the backend probe:

- Pulled:
  - `qwen3.5:2b-mlx-bf16`
- Confirmed registration in `ollama list`
- Ran a real generation:
  - prompt requested exact output `MLX OK`
  - model returned `MLX OK`

### Evidence that MLX actually ran

`/opt/homebrew/var/log/ollama.log` showed:

- `starting mlx runner subprocess`
- `MLX engine initialized`
- `mlx runner is ready`

This is the key verification that the MLX path was genuinely used, not just a normal GGUF runner path.

At runtime, `ollama ps` showed:

- model `qwen3.5:2b-mlx-bf16`
- `PROCESSOR 100% GPU`
- `CONTEXT 262144`

### Important conclusion

- MLX is working on this machine now
- Hermes was **not** switched to an MLX model
- The small 2B model was only a backend verification probe

### Heavyweight comparison-model selection

To compare something closer to the current Hermes model, the nearest practical MLX/NVFP4-side peer chosen was:

- `qwen3.5:35b-a3b-nvfp4`

Reason for selecting it:

- same `35b-a3b` mixture-of-experts family shape as the current `qwen3.6:35b-a3b`
- official Ollama MLX/NVFP4-style heavyweight model
- much more apples-to-apples than the small `2b-mlx-bf16` probe

### Heavyweight pull behavior

- Started pull of `qwen3.5:35b-a3b-nvfp4`
- Pull was interrupted once and later resumed
- Resume confirmed that Ollama continued from partial data instead of starting over
- Final result:
  - `qwen3.5:35b-a3b-nvfp4` finished successfully
  - model is now registered in `ollama list`

### Current local model inventory

- `qwen3.6:35b-a3b` — current Hermes model
- `qwen3.5:2b-mlx-bf16` — MLX verification probe
- `qwen3.5:35b-a3b-nvfp4` — heavyweight MLX comparison candidate

### Stable current state

- Hermes remains on `qwen3.6:35b-a3b`
- Hermes path is healthy and unchanged from the verified 2026-04-26 state
- MLX backend is verified and usable on this machine
- Heavyweight MLX comparison model is now downloaded and available locally

### Next logical step

Run a head-to-head comparison between:

1. `qwen3.6:35b-a3b`
2. `qwen3.5:35b-a3b-nvfp4`

Compare:

- cold-start behavior
- first-token latency
- throughput
- obedience on short exact-format prompts
- quality on small Hermes-relevant reasoning/tool-style prompts

---

## Session: 2026-04-27 (model switch to qwen3.5:35b-a3b-nvfp4)

### Comparison results

Head-to-head battery run against both 35b-a3b models with `think: false`, temperature 0.0:

| Metric | qwen3.6:35b-a3b (GGUF) | qwen3.5:35b-a3b-nvfp4 (MLX) |
|---|---|---|
| Cold-start load | 6.8s | 5.1s |
| Throughput (300 tok) | 31.2 tok/s | 68.7 tok/s |
| Prompt processing (38 tok) | ~200ms | ~900ms |

Quality: identical across all tests — exact-format obedience, JSON output, and tool-selection reasoning all matched. The nvfp4 model is ~2.2x faster on generation with no observable quality regression.

### Decision

Switched Hermes to `qwen3.5:35b-a3b-nvfp4`.

- Updated `~/.hermes/config.yaml`: `model.default: qwen3.5:35b-a3b-nvfp4`
- Restarted gateway (`ai.hermes.gateway`)
- Verified `hermes status` reports new model active

### Note on prompt processing

nvfp4 has slower prompt processing (~900ms vs ~200ms for short prompts). For Hermes routines with large context this may compound slightly, but generation speedup far outweighs it in practice.

### Caution

`brew services restart ollama` will drop the `OLLAMA_CONTEXT_LENGTH=131072` override from the LaunchAgent plist (same issue as 2026-04-26). Re-add manually to both plists if Ollama is ever restarted via brew services. See 2026-04-26 entry for the procedure.

### Stable current state

- Hermes default model: `qwen3.5:35b-a3b-nvfp4`
- Hermes provider: local custom Ollama endpoint
- Hermes context config: `131072`
- Ollama service context cap: `131072`
- Hermes gateway: running

### Next

Create the first scheduled routine: morning brief at 07:30 daily.

---

## Session: 2026-04-29 (manual model routing plan)

### Goal

Turbo-charge Hermes with optional cloud models while keeping local inference as the default.

### Routing principle

- Manual routing only at first
- Plain messages always use the local model
- Scheduled routines default to the local model
- Cloud models are used only after an explicit user command
- No automatic model switching until real usage patterns have been audited

### Session-scoped route commands

| Command | Route | Intended model | Meaning |
|---|---|---|---|
| `/local` | local | `qwen3.5:35b-a3b-nvfp4` | Return current session to local Ollama |
| `/fast` | fast | fast OpenAI model, likely `gpt-5.4-mini` | Use fast cloud model for the current session |
| `/5.5` | 5.5 | `gpt-5.5` | Use highest-capability OpenAI model for the current session |

Important behavior:

- Route changes apply to the current chat/session, not just one prompt
- New sessions should default back to local
- The user must intentionally switch away from local
- The user can always return to local with `/local`

### Reply markers

Every Hermes reply should begin with exactly one route marker so the active model is obvious in Telegram transcripts:

| Marker | Route |
|---|---|
| 🏠 | local |
| 🏃‍♂️ | fast |
| 💡 | 5.5 |

Examples:

- `🏠 Here is the local summary...`
- `🏃‍♂️ Switched this session to fast mode.`
- `💡 Switched this session to GPT-5.5.`

Marker namespace rule:

- These exact three emojis are reserved exclusively for model route identity
- Hermes must not use `🏠`, `🏃‍♂️`, or `💡` anywhere else as decorative/status/section emojis
- Similar or adjacent emojis are allowed for other meanings, but these exact markers must always mean model route
- The marker should appear once at the beginning of each Hermes reply and should not be repeated elsewhere in the same reply unless discussing the routing system itself

### Local timeout escalation prompt

Hermes should not auto-switch away from local, but it should proactively offer escalation if local inference is slow.

Initial threshold idea:

- 45s: internal slow marker only
- 75s: ask user whether to keep waiting or switch
- 150s: ask again only if still no reply

Suggested Telegram copy:

```text
🏠 Local model has been working for 75s without a reply. Reply /fast to switch this session to the fast model, /5.5 to switch to GPT-5.5, or /wait to keep waiting.
```

### Usage tracking

Track every non-local route use and every local timeout escalation prompt.

Fields to capture:

- timestamp
- session id
- route: local, fast, or 5.5
- command used: `/fast`, `/5.5`, `/local`, `/wait`
- model used
- user request summary
- reason for escalation, if available
- duration / timeout elapsed
- rough token and cost estimate, if available
- outcome: useful, overkill, not enough, or unknown

### Weekly audit loop

Once per week, review usage together and decide whether any recurring categories are safe to automate.

Audit questions:

- Which tasks were escalated to `/fast` or `/5.5`?
- Which escalations were clearly worth it?
- Which were overkill and could have stayed local?
- Which local timeout prompts led to an escalation?
- Are there repeated task types where automatic routing would be safe?

### Implementation posture

Start deliberately simple:

- no automatic router
- no silent cloud fallback
- no cloud usage without an explicit user command
- transcripts must make route/model obvious through emoji markers
- grow automatic routing only from observed weekly evidence

### Implementation update

Implemented in the Hermes gateway code on 2026-04-29:

- `/local`, `/fast`, and `/5.5` are session-scoped gateway model routes
- `/wait` acknowledges local timeout prompts without changing route
- all gateway responses are prefixed with the active route marker
- streamed responses and interim assistant commentary also receive route markers
- local route emits escalation prompts after 75s and 150s without visible streamed output
- non-local turns and timeout choices are appended to `~/.hermes/logs/model-routing.jsonl`
- exact emojis `🏠`, `🏃‍♂️`, and `💡` were removed from core non-routing UI/tool usage

Default route config is code-backed but can be overridden from `~/.hermes/config.yaml`:

```yaml
model_routes:
  fast:
    model: gpt-5.4-mini
    provider: openai-codex
    base_url: https://chatgpt.com/backend-api/codex
    api_mode: codex_responses
  "5.5":
    model: gpt-5.5
    provider: openai-codex
    base_url: https://chatgpt.com/backend-api/codex
    api_mode: codex_responses
```

Current credential note:

- Hermes should use the ChatGPT/Codex subscription login path for `/fast` and `/5.5`
- Run `hermes auth add openai-codex` and complete the browser device-code login at `https://auth.openai.com/codex/device`
- Tokens are stored in Hermes' own `~/.hermes/auth.json`; this avoids sharing refresh tokens with Codex CLI or VS Code
- 2026-04-30 status: `openai-codex` login completed and Hermes reports it as logged in
- Smoke test passed with `gpt-5.4-mini` through `openai-codex`: prompt requested exact `CODEX OK`, response was `CODEX OK`
- 2026-04-30 follow-up fix: gateway `/fast` dispatch now bypasses the old Hermes Priority Processing command path
- `/fast <prompt>`, `/5.5 <prompt>`, and `/local <prompt>` now switch the session route first, then answer the prompt on that route
- 2026-04-30 Telegram follow-up: `/5.5` parsing is now tolerant of Telegram-style command splitting, and `/55` remains the Telegram-safe alias/menu form

---

## Session: 2026-05-04

### Context

- Reviewed `OB1` as a reference repo, not as a replacement architecture
- Agreed that Hermes remains the primary interface
- Agreed that `open_brain` remains the canonical durable memory layer
- Decided to evaluate any borrowed pattern only by whether it improves Hermes-to-Open-Brain capture and retrieval

### Key architectural decision

Hermes should operate as a two-layer memory system:

| Layer | Role | Primary qualities |
|---|---|---|
| Hermes memory | operational/session memory | fast, local, provisional, continuity-oriented |
| `open_brain` | durable/canonical memory | persistent, structured where useful, cross-session, cross-interface |

This is not a dual-source-of-truth model.
It is a seamless two-layer model with different responsibilities.

### Canonical role split

**Hermes is responsible for:**

- conversational continuity
- short-term working memory
- active task/session state
- deciding when knowledge should be promoted to durable memory
- retrieving from both layers
- explaining answers with provenance and appropriate confidence

**`open_brain` is responsible for:**

- durable long-term storage
- semantic retrieval across time
- structured records and entities
- facts, decisions, summaries, action items, and other cross-session knowledge
- a memory layer that other interfaces can also rely on

### Source-of-truth rule

When the two layers differ:

1. current-session nuance belongs to Hermes memory
2. durable facts belong to `open_brain`
3. if both agree, Hermes may answer confidently
4. if they conflict, Hermes should prefer:
   - Hermes memory for clearly newer in-session updates
   - `open_brain` for stable historical fact
5. if the conflict is material and cannot be resolved safely, Hermes should say so explicitly

Hermes must not flatten conflicting evidence into one overconfident answer.

### Trust and provenance model

Hermes retrieval should distinguish internally among:

- `session_memory`
- `open_brain`
- `synthesized_from_both`
- `inference`

User-facing answers should reflect this distinction when it matters.

Example behaviors:

- "Earlier in this session, you said..."
- "Your Open Brain shows..."
- "Both your current session and Open Brain point to..."
- "I found partial signals, but they conflict."

Trustworthiness is more important than smoothness.

### Capture model

Not everything from Hermes should be promoted into `open_brain`.

**Keep only in Hermes memory:**

- raw chatter
- low-signal brainstorming fragments
- abandoned paths
- temporary scaffolding
- speculative interpretations that were never confirmed

**Promote into `open_brain` when one of these is true:**

- the user states a durable fact
- a preference appears stable and important
- a decision is made or accepted
- an action item is real and worth tracking
- a structured entity should be created or updated
- a session summary would be useful later
- the content should survive across interfaces or future sessions

### Preferred durable capture shapes

Hermes should mostly write these forms into `open_brain`:

- single durable facts
- action items
- structured entity updates
- concise decision records
- concise session summaries

This should avoid dumping raw session text into the durable store.

### Retrieval model

Hermes should not always query both layers the same way.

**Fast path**

Use Hermes memory first when the question is obviously about:

- current session context
- active task state
- something just discussed

**Durable path**

Use `open_brain` first when the question is about:

- history
- known facts
- structured records
- previously made decisions
- information that should survive across sessions

**Blended path**

Use both when the question needs both short-term and long-term context.

Examples:

- "What are we doing next on this project?"
- "What do you know about Marcus and what changed today?"
- "Summarize this plan using what we discussed and what is already in my brain."

### Retrieval arbitration sequence

When both layers are queried, Hermes should:

1. retrieve session-local evidence
2. retrieve durable `open_brain` evidence
3. compare overlap, novelty, and conflict
4. synthesize an answer with source awareness
5. explicitly flag contradictions instead of hiding them

### Hermes compatibility constraint

This is now a core constraint for future work:

Hermes is the interface used most often, so any memory-related change must preserve or improve seamless Hermes-to-Open-Brain operation.

That means:

- no second primary interface
- no second canonical memory store
- no borrowed `OB1` pattern that bypasses Hermes
- no architectural drift that makes `open_brain` harder for Hermes to reason about

Every proposed change should be tested against this question:

> Does this make Hermes better at capturing to, retrieving from, and explaining `open_brain`?

If not, it is probably not worth doing.

### `OB1` reuse filter

Agreed filter for ideas borrowed from `OB1`:

**Worth reusing soon**

- content fingerprint dedup
- auto-capture protocol
- contribution packaging pattern

**Worth reusing later**

- Slack capture, if Hermes actually benefits from another capture surface
- dashboard ideas, if they help inspect or audit `open_brain`

**Not a priority right now**

- multi-user RLS patterns
- extension curriculum unrelated to Hermes/Open-Brain flow
- any product surface that creates a second main interface

### Priority order

The current recommended order is:

1. auto-capture convention
2. content fingerprint dedup
3. contribution packaging pattern
4. Slack capture only if a real capture need appears

### Immediate implementation guidance

**Auto-capture convention**

- easiest and quickest win
- should define a Hermes behavior where session close writes:
  - one concise session summary
  - separate durable action items
  - only high-signal outputs

**Content fingerprint dedup**

- highest leverage infrastructure improvement
- should protect against:
  - duplicate imports
  - retry-based duplicate capture
  - overlapping write surfaces
  - repeated summaries or action-item writes

**Contribution packaging pattern**

- worth adopting lightly as organization, not as a replatforming exercise
- useful conceptual folders:
  - `recipes/`
  - `skills/`
  - `integrations/`
  - `primitives/`

### Final position from this session

The correct model is not "Hermes or `open_brain`."
It is:

- Hermes for interaction and short-term intelligence
- `open_brain` for durable memory and retrieval
- one seamless experience across both

This should be treated as the design standard future work must preserve.

---

## Session: 2026-05-04 (ASAP execution plan for unified retrieval)

### Goal

Get unified cross-table retrieval working end-to-end as fast as possible so:

- Hermes can answer conversational memory questions with one Open Brain retrieval call
- the Open Brain bot can retrieve Strava and other structured records without relying on thought-only search
- future memory work builds on one shared retrieval layer instead of two diverging ones

### What "done" means

This is considered up and running when all of the following are true:

1. the hosted `open-brain-mcp` exposes `query_brain`
2. Hermes can answer mixed memory questions by using `query_brain`
3. the Open Brain bot's retrieval path uses `query_brain` instead of local `search_thoughts`-only context building
4. Strava-backed `life_items` are retrievable from both Hermes and the Open Brain bot
5. shadow thoughts are still allowed during initial rollout, but no longer required for successful retrieval

### Scope cut for speed

To ship fast, Phase 1 should be intentionally narrow.

**Do now**

- add `query_brain`
- search all current durable tables
- merge and rank results simply and predictably
- return explicit provenance
- wire both clients to use it
- verify against a fixed evaluation set

**Do not block initial launch on**

- perfect ranking
- semantic embeddings for every structured table
- dedup polish beyond obvious cross-table duplicates
- shadow-thought removal
- new capture flows
- auto-capture, fingerprints, or dashboard work

### Critical path

There is one real blocker and three implementation steps.

1. **Confirm editable source for the hosted `open-brain-mcp` function**
2. **Add and deploy `query_brain`**
3. **Update Hermes retrieval behavior**
4. **Update Open Brain bot retrieval path**

Everything else is secondary until those are complete.

### Immediate blocker to clear first

The live hosted MCP contract is verified, but the actual checked-in source for `open-brain-mcp`
is not visible in the current repo snapshot.

Fastest path:

- locate the real deployed function source if it exists outside this snapshot
- if found, implement `query_brain` there directly
- if not found quickly, create a new visible source-of-truth folder for the hosted MCP in `open_brain` and deploy from that

Do not start by rewriting Open Brain bot retrieval locally. That would recreate the split architecture we are trying to remove.

### Fastest implementation order

#### Step 0 — Source and deployment sanity check

Deliverable:
- known editable source path for `open-brain-mcp`
- known deploy command
- one successful no-op or low-risk deploy path confirmed

Checks:
- source path is documented in `open_brain`
- deployment target is the same hosted endpoint already used by Hermes

If this step is blocked, stop and resolve it before touching client code.

#### Step 1 — Add `query_brain` to hosted MCP

Deliverable:
- new MCP tool: `query_brain(query: string, tables?: string[])`

Phase-1 behavior:
- embed the query using the same embedding model already used for `thoughts`
- query `thoughts` via existing `match_thoughts`
- query `life_items`, `records`, `contacts`, `finance_records`, and `home_items` with simple text/field matching
- score each table's candidates into a shared `0..1` range
- merge, cap, and return results with `table`, `id`, `score`, `content_summary`, `created_at`, and `metadata`
- include warnings for partial failures instead of failing the whole request

Implementation rule:
- keep ranking policy explicit and simple
- optimize for inspectability and good-enough mixed retrieval, not elegance

#### Step 2 — Validate hosted retrieval directly before any client changes

Deliverable:
- direct MCP tests against the hosted endpoint showing `query_brain` works

Required evaluation questions:
- "How far did I ride last month?"
- "What did I say about retrieval architecture last week?"
- "When did I last mention Sam?"
- "What subscriptions did I pay for recently?"
- "Show me anything about the garage bike trainer"

Pass condition:
- each query returns sensible cross-table results
- Strava-backed `life_items` appear without needing handcrafted table choice
- failure in one table still returns partial results

#### Step 3 — Update Hermes to prefer `query_brain`

Deliverable:
- Hermes uses `query_brain` for conversational memory questions

Minimum change:
- add retrieval guidance so Hermes prefers `query_brain` for conversational recall
- keep individual `search_*` tools for explicit filtered or structured operations

Verification:
- Hermes answers a ride-history question with `query_brain`
- Hermes answers a mixed personal-memory question with `query_brain`
- Hermes still uses table-specific tools when explicitly asked for filtered records

#### Step 4 — Update Open Brain bot to call `query_brain`

Deliverable:
- `core/engine.py` no longer builds retrieval context from local `search_thoughts` alone

Minimum implementation:
- replace `_build_retrieval_context` with a hosted MCP call
- keep the current downstream answer-generation model flow
- map `query_brain` results into a compact retrieval context block for the answering LLM

Important constraint:
- do not rewrite the bot into a second retrieval orchestrator
- the bot should be a thin caller of the shared hosted retrieval layer

Verification:
- the bot answers a Strava question successfully
- the bot still answers thought-only questions at least as well as before

#### Step 5 — Stabilization window

Deliverable:
- one short period of real use before removing any bridge logic

Keep during this window:
- shadow thought writes on structured saves

Watch for:
- missing structured hits
- noisy ranking
- contradictions between Hermes and bot answers

#### Step 6 — Retire shadow-thought dependency

Deliverable:
- structured retrieval no longer depends on `ref_table` / `ref_id` bridge thoughts

Safe rollout rule:
- first confirm unified retrieval answers the evaluation set reliably
- then remove the shadow-thought write from structured save paths
- if provenance records are still useful, replace them deliberately rather than keeping the old hidden workaround

### Code touchpoints already confirmed

These are the main places the implementation will hit:

- `open_brain/core/engine.py`
  - `_build_retrieval_context` is still local `thoughts`-only retrieval
  - structured saves still write shadow thoughts via `ref_table` / `ref_id`
- `open_brain/core/storage.py`
  - current local retrieval helper is `search_thoughts` only
- hosted MCP function source
  - exact path still needs to be confirmed before implementation starts

### Test strategy

Keep the test plan small and ruthless.

**Hosted MCP**

- add focused tests for:
  - thoughts-only retrieval
  - structured-table retrieval
  - mixed-result ranking
  - partial failure handling
  - `tables` filter behavior

**Open Brain bot**

- update retrieval-path tests so they no longer assume `thoughts` is the only source of truth
- do not remove existing routing tests until shadow thoughts are intentionally retired

**End-to-end**

- one Hermes smoke test
- one Open Brain bot smoke test
- both must answer at least one Strava query correctly

### Recommended calendar

If execution stays focused, this should fit into a short sequence:

**Day 0**
- confirm hosted MCP source path and deploy path

**Day 1**
- implement and deploy `query_brain`
- run direct MCP evaluation set

**Day 2**
- wire Hermes to prefer `query_brain`
- wire Open Brain bot to call `query_brain`
- run end-to-end smoke tests

**Day 3**
- use it in the real loop
- inspect misses
- decide whether shadow-thought retirement is safe yet

### Priority if time gets tight

If we need to cut further to get something live fast, cut in this order:

1. keep `tables` filter but allow it to be minimal
2. keep score normalisation simple
3. defer shadow-thought removal
4. defer any ranking refinement
5. defer any broader memory cleanup work

Do not cut:

- hosted shared retrieval as the single source of retrieval truth
- provenance in results
- Strava / structured-table coverage
- direct evaluation before client rollout

### Execution posture

The shortest path is:

1. make the hosted MCP smarter once
2. make Hermes and the bot thinner
3. keep the bridge logic until real retrieval proves itself
4. remove workarounds only after success is visible

That is the fastest route to "working now" without creating another architecture we have to undo a week later.
