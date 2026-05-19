# Hermes — Personal Reference

Single source of truth for what Hermes is, how it's set up, and how to use it.
For planned improvements, see [hermes_roadmap.md](hermes_roadmap.md).

---

## What It Is

Self-improving AI agent built by Nous Research. Runs 24/7 on the Mac mini, reachable via Telegram. Handles conversation, memory, tool use, scheduled tasks, and code execution. Can delegate heavy work to cloud models when needed.

---

## My Setup

| Thing | Value |
|---|---|
| Hardware | Mac mini M4 Pro, 48 GB unified memory |
| Primary UI | Telegram |
| Local model | `qwen3.5:35b-a3b-nvfp4` via Ollama — always loaded in GPU memory |
| Memory backend | OpenBrain (Supabase MCP) |
| Gateway service | `ai.hermes.gateway` (launchd) — starts at login, always running |
| Hermes home | `~/.hermes/` |
| Repo | `~/ai/agents/hermes-agent/` |
| Branch convention | feature work on named branches, main for stable |

---

## Key Files & Locations

| File | Purpose |
|---|---|
| `~/.hermes/config.yaml` | Main config — model, gateway, memory, tools |
| `~/.hermes/.env` | All secrets and API keys |
| `~/.hermes/state.db` | SQLite session store (WAL mode) |
| `~/.hermes/SOUL.md` | Persona |
| `~/.hermes/MEMORY.md` | Shared memory |
| `~/.hermes/USER.md` | User profile |
| `~/.hermes/skills/` | User-created skills |
| `~/Library/LaunchAgents/com.mh.ollama.plist` | Owned Ollama launchd config |
| `~/.hermes/logs/ollama.log` | Ollama logs |
| `~/Library/LaunchAgents/com.mh.hermes-health-monitor.plist` | Health monitor launchd config |
| `~/.hermes/logs/health-monitor.jsonl` | Structured health monitor logs |

---

## Feature Status

| Feature | Status | Still needed to use it |
|---|---|---|
| Local Hermes chat via Ollama | live | owned Ollama LaunchAgent installed and running |
| Telegram gateway | live | `TELEGRAM_BOT_TOKEN` and gateway service running |
| OpenBrain-backed memory retrieval | live | `mcp_servers.open_brain` in `~/.hermes/config.yaml` and `MCP_ACCESS_KEY` in `~/.hermes/.env` |
| Explicit memory capture: `/note`, `/m` | live | same OpenBrain config as above |
| Session capture controls: `/nosave`, `/private`, `/capture-status` | live | nothing extra once gateway is running |
| Automatic Hermes session-end capture | live | same OpenBrain config as above |
| Claude Code Stop-hook capture | live | hook registered in Claude Code settings and Hermes/OpenBrain config available in the hook environment |
| Pull surfaces: `/brief`, `/digest`, `/stale`, `/finance-check` | live | same OpenBrain config as above and enough captured data to query |
| Route switching: `/claude`, `/opus` | live | valid OpenRouter credentials unless `model_routes` overrides them |
| Route switching: `/fast`, `/5.5` | live | valid `openai-codex` runtime auth |
| Jira pull surface: `/jira` | implemented | add a Jira MCP server entry with `url`, `cloudId`, and auth headers |
| Health monitor | live | LaunchAgent installed, Telegram home channel configured if you want alerts |
| Open Brain standalone Telegram bot | separate service, live if launched | its own `.env`, bot token, and allowed Telegram ID |
| Read-only calendar integration | not implemented yet | Phase `3.1` still open |
| Scheduled proactive brief/digest delivery | not implemented yet | Phase `5` still open |
| Provenance/conflict-aware answer synthesis | not implemented yet | Phase `6` still open |
| Web monitoring and pattern detection | not implemented yet | Phase `7` still open |

---

## Enable Everything Implemented

Minimum checklist for the full currently-implemented stack:

1. Install and run Ollama via [com.mh.ollama.plist](/Users/mh/ai/agents/hermes-agent/launchd/com.mh.ollama.plist).
2. Keep Hermes config in `~/.hermes/config.yaml` with the local model and `mcp_servers.open_brain`.
3. Put required secrets in `~/.hermes/.env`.
4. Run the Hermes gateway service with Telegram enabled.
5. Install the health monitor LaunchAgent.
6. Register the Claude Code Stop hook if you want automatic coding-session capture.
7. Add a Jira MCP server entry if you want `/jira`.

Recommended `~/.hermes/.env` keys:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_HOME_CHANNEL=...
MCP_ACCESS_KEY=...
OPENROUTER_API_KEY=...          # needed for /claude and /opus unless route config overrides auth
# plus whatever auth your openai-codex runtime uses for /fast and /5.5
# optional for Jira:
ATLASSIAN_MCP_TOKEN=...
```

If a feature appears in the command list but replies with "isn't configured", the missing piece is almost always one of:

- a missing `mcp_servers.*` config entry
- a missing secret in `~/.hermes/.env`
- a LaunchAgent that was not installed into `~/Library/LaunchAgents/`
- a service that is installed but not currently loaded with `launchctl`

---

## Current config.yaml

```yaml
model:
  default: qwen3.5:35b-a3b-nvfp4
  provider: custom
  base_url: http://localhost:11434/v1
  context_length: 131072
  ollama_keep_alive: -1        # keep model resident in GPU memory forever

reasoning:
  enabled: false               # thinking disabled — saves 10–25s per response

stream_output: true
terminal:
  backend: local
  cwd: /Users/mh/ai
  env_path: ~/.hermes/venv/bin/python3.11
streaming:
  enabled: true
  transport: edit
  edit_interval: 0.2
  buffer_threshold: 20
gateway:
  streaming:
    enabled: true
    transport: edit
    min_interval: 200
    chunk_size: 20
memory:
  provider: openbrain
stt:
  enabled: true
  provider: local
```

---

## Ollama Setup

| Setting | Value | Where |
|---|---|---|
| `OLLAMA_KEEP_ALIVE` | `-1` (forever) | `~/Library/LaunchAgents/com.mh.ollama.plist` |
| `OLLAMA_CONTEXT_LENGTH` | `131072` | same plist |
| `OLLAMA_FLASH_ATTENTION` | `1` | same plist |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | same plist |
| `ollama_keep_alive` | `-1` | `~/.hermes/config.yaml` (per-request override) |

The model loads into GPU on the first request after a restart and stays resident. `ollama ps` should always show `UNTIL: Forever` during normal operation.

To verify: `ollama ps` — expect `qwen3.5:35b-a3b-nvfp4 ... 100% GPU ... Forever`

To restart Ollama: `launchctl kickstart -k gui/$(id -u)/com.mh.ollama`

Install/update the owned LaunchAgent:
```bash
mkdir -p ~/.hermes/logs ~/Library/LaunchAgents
cp ~/ai/agents/hermes-agent/launchd/com.mh.ollama.plist ~/Library/LaunchAgents/com.mh.ollama.plist
launchctl bootout gui/$(id -u)/com.mh.ollama 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mh.ollama.plist
launchctl kickstart -k gui/$(id -u)/com.mh.ollama
```

If migrating from Homebrew, disable the old plist so it cannot reclaim port `11434` on the next login:
```bash
mv ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist ~/Library/LaunchAgents/homebrew.mxcl.ollama.plist.disabled-YYYY-MM-DD
```

---

## Mode Shortcuts

Use these phrases as shorthand for Ollama residency behavior:

| Phrase | Meaning |
|---|---|
| `always-on` | Keep the local Ollama model loaded indefinitely. Set `model.ollama_keep_alive: -1` in `~/.hermes/config.yaml` and `OLLAMA_KEEP_ALIVE=-1` in `~/Library/LaunchAgents/com.mh.ollama.plist`. Expected `ollama ps` result: `UNTIL: Forever`. |
| `not always on` | Allow the local Ollama model to unload after 5 minutes idle. Remove `model.ollama_keep_alive` from `~/.hermes/config.yaml` or set it to `300`, and remove `OLLAMA_KEEP_ALIVE` from `~/Library/LaunchAgents/com.mh.ollama.plist` or set it to `300`. Expected `ollama ps` result after idle: no loaded model, or a non-forever expiry while active. |

When asked to "switch to always on mode", apply the `always-on` settings above.
When asked to "switch to not always on mode", apply the `not always on` settings above.

---

## Gateway

Hermes gateway runs as a launchd service and handles all Telegram traffic.

```bash
# Restart gateway (picks up config.yaml changes)
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway

# Check it's running
launchctl list | grep hermes

# View logs
tail -f /opt/homebrew/var/log/hermes-gateway.log  # or wherever configured
```

What the gateway needs in practice:

- `TELEGRAM_BOT_TOKEN` for Telegram access
- `~/.hermes/config.yaml` present and readable
- `~/.hermes/.env` present if you rely on secrets there
- OpenBrain MCP config if you want memory-backed commands
- route-provider credentials if you want `/claude`, `/opus`, `/fast`, or `/5.5`

Useful health checks:
```bash
HERMES_HOME=~/.hermes hermes gateway status
launchctl print gui/$(id -u)/ai.hermes.gateway | sed -n '1,80p'
tail -n 80 ~/.hermes/logs/health-monitor.log
```

## Health Monitor

Hermes health monitoring runs as a separate launchd job and checks:

- Ollama local API
- Hermes gateway runtime state
- Telegram bot reachability
- Openbrain MCP authenticated and unauthenticated probes
- disk space and available memory thresholds

Install/update it:
```bash
mkdir -p ~/.hermes/logs ~/Library/LaunchAgents
cp ~/ai/agents/hermes-agent/launchd/com.mh.hermes-health-monitor.plist ~/Library/LaunchAgents/com.mh.hermes-health-monitor.plist
launchctl bootout gui/$(id -u)/com.mh.hermes-health-monitor 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mh.hermes-health-monitor.plist
```

Run it manually:
```bash
uv run python ~/ai/agents/hermes-agent/scripts/hermes_health_monitor.py
```

Behavior:

- short HTTP timeouts by default
- one restart request on first local service failure, then backoff
- one Telegram alert on persistent failure instead of repeated spam
- JSONL logs with timestamp, service, status, action, detail, and correlation ID

What it uses:

- `~/.hermes/.env` is loaded before checks
- OpenBrain URL is resolved from `OPEN_BRAIN_MCP_URL`, `SUPABASE_URL`, or `mcp_servers.open_brain.url`
- Telegram alerts go to `TELEGRAM_HOME_CHANNEL`

Current check set:

- `ollama`
- `gateway`
- `telegram`
- `openbrain`
- `disk`
- `memory`

---

## Slash Commands (Telegram)

| Command | What it does |
|---|---|
| `/new` or `/reset` | Start fresh conversation |
| `/note <text>` or `/m <text>` | Save an explicit note to OpenBrain immediately |
| `/nosave [on|off|status]` | Disable automatic session-end capture for this session |
| `/private [on|off|status]` | Keep automatic session capture off until re-enabled |
| `/capture-status` | Show whether automatic capture is currently eligible |
| `/brief [query]` | Show recent Hermes captures from OpenBrain |
| `/digest [query]` | Show a synthesized weekly digest from recent Hermes captures |
| `/stale` | Show stale action items and dormant contacts from OpenBrain |
| `/finance-check` | Compare recent finance records against the prior period |
| `/jira [filter]` | Show current sprint Jira issues from the configured Jira MCP server |
| `/model [provider:model]` | Switch model |
| `/local` | Return this session to the local route |
| `/claude` | Switch this session to the Claude Sonnet route |
| `/opus` | Switch this session to the Claude Opus route |
| `/fast` | Switch this session to the fast paid route |
| `/5.5` | Switch this session to GPT-5.5 route |
| `/stop` | Interrupt current work |
| `/retry` | Redo last turn |
| `/undo` | Remove last turn |
| `/compress` | Manually compress context |
| `/usage` | Token and cost stats for session |
| `/skills` | List available skills |
| `/<skill-name>` | Run a skill |
| `/status` | Gateway and connection status |
| `/sethome` | Set current chat as home channel |

---

## Model Switching

Switch to a cloud model for a heavy task:
```
/model anthropic/claude-opus-4.6
```
Switch back to local:
```
/model qwen3.5:35b-a3b-nvfp4
```
Or just ask Hermes to use a cloud model for a specific request — it can switch mid-session.

Gateway route shortcuts:

```text
/local
/claude
/opus
/fast
/5.5
```

Route credentials:

- `/claude` and `/opus` default to `openrouter` and therefore need working OpenRouter auth unless overridden in `model_routes`
- `/fast` and `/5.5` default to `openai-codex` runtime auth
- `/local` never spends money by itself; it only suggests `/fast` or `/claude` when richer synthesis might help

Optional config override shape:
```yaml
model_routes:
  claude:
    model: anthropic/claude-sonnet-4.6
    provider: openrouter
  opus:
    model: anthropic/claude-opus-4.6
    provider: openrouter
  fast:
    model: gpt-5.4-mini
    provider: openai-codex
  "5.5":
    model: gpt-5.5
    provider: openai-codex
```

Available local models (installed):
- `qwen3.5:35b-a3b-nvfp4` — 21 GB, primary, MoE sparse (fast)
- `qwen3.5:2b-mlx-bf16` — 4.4 GB, fast, lightweight tasks
- `qwen3.6:35b-a3b` — 23 GB, alternative 35B

---

## Memory — OpenBrain

Hermes uses OpenBrain (Supabase-hosted MCP) for persistent memory. The model queries it automatically on conversational/memory questions before answering.

Tables: `thoughts`, `contacts`, `finance_records`, `life_items`, `home_items`, `records`

Retrieval via `mcp_open_brain_query_brain` — natural language, searches across all tables.
Analytical queries (counts, totals, date ranges) via `mcp_open_brain_analyze_brain_query`.

Config in `~/.hermes/config.yaml`:
```yaml
mcp_servers:
  open_brain:
    url: https://icxyfzzbsrsiyaqnynum.supabase.co/functions/v1/open-brain-mcp
    headers:
      x-brain-key: ${MCP_ACCESS_KEY}
```

Optional Jira readback via MCP:
```yaml
mcp_servers:
  jira:
    url: https://mcp.atlassian.com/v1/mcp
    cloudId: your-atlassian-cloud-id
    headers:
      Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}
```

Hermes uses this for the read-only `/jira` command. It reads the current sprint on demand, cites Jira as the source, and does not mirror issue dumps into OpenBrain.

What depends on OpenBrain:

- automatic session-end summary capture from Hermes
- explicit note capture via `/note` and `/m`
- `/brief`
- `/digest`
- `/stale`
- `/finance-check`
- Claude Code Stop-hook capture

If OpenBrain is unavailable, normal chat still works, but the memory-backed features above will return a clear config or runtime error instead of silently writing elsewhere.

### Claude Code Stop Hook

The capture hook lives at [claude_code_stop_hook.py](/Users/mh/ai/agents/hermes-agent/scripts/claude_code_stop_hook.py).

What it expects:

- Claude Code passes a JSON payload on stdin with `session_id`, `transcript_path`, and `cwd`
- Hermes repo is still present at `/Users/mh/ai/agents/hermes-agent`
- OpenBrain is reachable through `~/.hermes/config.yaml` and `MCP_ACCESS_KEY`

What it logs:

- `~/.hermes/logs/claude_code_capture.log`

How to disable capture temporarily without unregistering the hook:

```bash
export HERMES_NO_CAPTURE=1
```

What to verify after a real Claude Code session ends:

```bash
tail -n 40 ~/.hermes/logs/claude_code_capture.log
```

You want to see `SAVED` or `DEDUP`, not repeated `ERROR`.

### Jira MCP

`/jira` is implemented but not usable until a Jira MCP server is configured in `~/.hermes/config.yaml`.

Minimum shape:
```yaml
mcp_servers:
  jira:
    url: https://mcp.atlassian.com/v1/mcp
    cloudId: your-atlassian-cloud-id
    headers:
      Authorization: Bearer ${ATLASSIAN_MCP_TOKEN}
```

Notes:

- the server name does not have to be exactly `jira`, but that is the expected default
- Hermes currently calls a read-only Jira search tool and filters results locally for `/jira <filter>`
- no Jira issue dump is written into OpenBrain by this feature
- if `cloudId` is missing, `/jira` will fail fast with a config error

### Calendar

Calendar is still not implemented in Hermes. There is no working calendar-backed command yet, and `/brief` does not currently use calendar context.

---

## Tools

40+ tools available. Key ones:

| Category | Tools |
|---|---|
| Files | read, write, patch, list |
| Terminal | bash, Python execution |
| Browser | CDP-based automation, screenshot, OCR |
| Web | search (Exa/Firecrawl), web fetch |
| Memory | OpenBrain MCP tools |
| Delegation | spawn subagents, parallel workstreams |
| Scheduling | cron jobs with Telegram delivery |
| Voice | STT transcription (local faster-whisper) |
| Sessions | FTS5 search across conversation history |

---

## Skills

Skills are procedural memory — reusable workflows Hermes can run or create.

Location: `~/.hermes/skills/`

Run a skill: `/<skill-name>` in Telegram or CLI.
Create a skill: ask Hermes to create one after completing a complex task.

---

## Streaming

Hermes streams responses back to Telegram using progressive message editing — the message appears immediately and is updated as tokens arrive rather than waiting for the full response.

Config (already active):
```yaml
streaming:
  enabled: true
  transport: edit        # edits the message in-place as tokens arrive
  edit_interval: 0.2     # seconds between edits
  buffer_threshold: 20   # tokens to buffer before first edit
gateway:
  streaming:
    enabled: true
    transport: edit
    min_interval: 200    # ms
    chunk_size: 20
```

---

## STT (Voice Messages)

Enabled with local `faster-whisper`. Send a voice note to Telegram — Hermes transcribes it, shows a `🎤 Heard: "..."` preview, and processes the content.

Config: `stt: { enabled: true, provider: local }`

---

## Automation (Cron + Webhooks)

Hermes has a built-in scheduler and webhook platform. No daily limits — constrained only by API budget.

**Cron jobs:**
```bash
# Daily morning brief at 07:30
hermes cron create "30 7 * * *" "Morning brief: pull open_brain context, check calendar, flag anything time-sensitive" --name "morning-brief" --deliver telegram

# Weekly finance digest
hermes cron create "0 20 * * 0" "Aggregate this week's finance_records, flag anomalies, total by category" --name "weekly-finance" --deliver telegram
```

**Script injection** — run a Python script before the agent; stdout becomes context:
```bash
hermes cron create "every 1h" "If CHANGE DETECTED summarise what changed. If NO_CHANGE reply [SILENT]." --script ~/.hermes/scripts/watch-site.py --deliver telegram
```

**Webhooks:**
```bash
hermes webhook subscribe pr-review --events "pull_request" --prompt "Review PR #{pull_request.number}" --deliver github_comment
```

**Delivery targets:**
```
--deliver telegram                        # home channel
--deliver telegram:-1001234567890:42      # specific forum topic
--deliver discord / slack / sms:+1555...
--deliver local                           # file only, no notification
```

**Planned routines (not yet active):**
- Morning brief — 07:30 daily — open_brain + calendar + web flags → Telegram
- Weekly finance digest — Sunday 20:00
- Stale contact scan — Monday 08:00 — contacts with no activity in 60+ days
- Overnight distillation — 02:00 nightly — cluster recent thoughts, write summaries back via MCP

Current status:

- Hermes scheduling exists
- the stack now has the pull surfaces those jobs would rely on
- proactive delivery jobs themselves are still intentionally not wired as default recurring automations
- the right time to enable them is after production validation of `/brief`, `/digest`, `/stale`, `/finance-check`, and later calendar

---

## Verification Checklist

Use this after machine restart or config changes:

```bash
launchctl print gui/$(id -u)/com.mh.ollama | sed -n '1,40p'
launchctl print gui/$(id -u)/ai.hermes.gateway | sed -n '1,60p'
launchctl print gui/$(id -u)/com.mh.hermes-health-monitor | sed -n '1,40p'
curl -sS http://127.0.0.1:11434/api/version
curl -sS http://127.0.0.1:11434/api/tags
uv run python ~/ai/open_brain/scripts/hosted_mcp_smoke.py --key "$MCP_ACCESS_KEY"
```

Manual feature smoke tests:

```text
/note remember this deployment detail
/brief
/digest
/stale
/finance-check
/claude explain this briefly
/local
/jira               # only after Jira MCP is configured
```

Expected current limitations:

- `/jira` is not usable until Jira MCP config is added
- calendar-backed context is not built yet
- proactive scheduled delivery is not enabled by default
- Phase `6` provenance/conflict handling is still future work, so normal answers are not yet doing the final upgraded conflict-resolution pass

---

## Performance Notes _(2026-05-07)_

- **Cold-start delay eliminated**: `OLLAMA_KEEP_ALIVE=-1` keeps the model in GPU memory permanently. No more 10–30s load time on first message.
- **Thinking disabled**: `reasoning: { enabled: false }` sends `think: false` to Ollama. Removes extended internal monologue (was causing 30s+ responses). Model is still 35B — quality unchanged for conversational tasks. Re-enable with `reasoning: { enabled: true }` if deep multi-step reasoning is needed for a session.
- **Typical response latency**: 5–10s end-to-end for conversational queries.

---

## Troubleshooting

**Model not loaded / slow first response**
```bash
ollama ps  # should show Forever
# If empty, trigger a load:
curl -s http://localhost:11434/api/generate -d '{"model":"qwen3.5:35b-a3b-nvfp4","prompt":"hi","stream":false,"keep_alive":-1}'
```

**Gateway not responding**
```bash
launchctl list | grep hermes  # check PID
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```

**Restart both**
```bash
launchctl kickstart -k gui/$(id -u)/com.mh.ollama
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```

**Ollama context regresses or service restarts with wrong env**

The owned LaunchAgent should be the only service definition in play:
```bash
launchctl print gui/$(id -u)/com.mh.ollama | sed -n '1,60p'
```
If the live process still looks wrong, verify the running environment and recent log output:
```bash
ps eww -p $(pgrep ollama) | tr ' ' '\n' | grep OLLAMA  # live env
grep "server config\|vram-based\|KvSize" ~/.hermes/logs/ollama.log | tail -5
```

**MLX not running (symptoms: slower generation, log shows GGUF runner instead of MLX)**

The MLX dylib symlink may be missing:
```bash
ln -sf /opt/homebrew/opt/mlx-c/lib/libmlxc.dylib /opt/homebrew/opt/ollama/bin/libmlxc.dylib
```
Verify MLX is active: `grep "mlx runner is ready" ~/.hermes/logs/ollama.log`
