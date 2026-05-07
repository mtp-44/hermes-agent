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
| `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` | Ollama launchd config |
| `/opt/homebrew/var/log/ollama.log` | Ollama logs |

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
| `OLLAMA_KEEP_ALIVE` | `-1` (forever) | `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` |
| `OLLAMA_CONTEXT_LENGTH` | `131072` | same plist |
| `OLLAMA_FLASH_ATTENTION` | `1` | same plist |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | same plist |
| `ollama_keep_alive` | `-1` | `~/.hermes/config.yaml` (per-request override) |

The model loads into GPU on the first request after a restart and stays resident. `ollama ps` should always show `UNTIL: Forever` during normal operation.

To verify: `ollama ps` — expect `qwen3.5:35b-a3b-nvfp4 ... 100% GPU ... Forever`

To restart Ollama: `launchctl kickstart -k gui/$(id -u)/homebrew.mxcl.ollama`

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

---

## Slash Commands (Telegram)

| Command | What it does |
|---|---|
| `/new` or `/reset` | Start fresh conversation |
| `/model [provider:model]` | Switch model |
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

## STT (Voice Messages)

Enabled with local `faster-whisper`. Send a voice note to Telegram — Hermes transcribes it, shows a `🎤 Heard: "..."` preview, and processes the content.

Config: `stt: { enabled: true, provider: local }`

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
launchctl kickstart -k gui/$(id -u)/homebrew.mxcl.ollama
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway
```
