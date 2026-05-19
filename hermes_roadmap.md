# Hermes — Roadmap

Single source of truth for planned improvements, open questions, and architectural ideas.
When an item is implemented, move the relevant facts to [hermes.md](hermes.md) and remove it here.

Format: items logged with date. If multiple entries on the same day, add `_HH:MM`.

---

## Active / In Progress

### Unified Retrieval Architecture _(2026-05-04)_

Branch: `unified-retrieval-start`

**Problem:** Three separate retrieval paths diverge: Open Brain bot (vector search on `thoughts` only), Hermes (9 separate MCP tools), direct Supabase. When the data model changes, all three must update. Strava data written to `life_items` is invisible to the Open Brain bot. Hermes has to guess which of 9 tools to call.

**Solution:** One `query_brain` implementation in the hosted Supabase MCP edge function. All surfaces call the same endpoint. Capture stays per-surface (each has real strengths). Only retrieval unifies.

**How it works:**
1. Embed the query (`text-embedding-3-small`)
2. Parallel search: vector on `thoughts`, FTS on all structured tables
3. Score, merge, return ranked results with provenance
4. Signature: `query_brain(query: string, tables?: string[]) → results[]`

**Why hosted MCP:** always available (not a local process), already the Hermes interface, direct Supabase credentials, no extra hops.

**Status:** architecture designed, partial implementation on branch. Hermes side has `query_brain` and `analyze_brain_query` wired. Open Brain bot not yet updated.

---

## Backlog — Features

### OpenAI-Compatible API Server _(2026-05-07)_

Expose Hermes as an OpenAI-compatible endpoint so any chat frontend (Open WebUI, LobeChat, LibreChat, AnythingLLM, etc.) can use it as a backend with no custom adapters.

**Endpoints:** `POST /v1/chat/completions`, `POST /v1/responses`, `GET /v1/models`, `GET /health`

**Architecture:** New `gateway/platforms/api_server.py` adapter — fits the existing platform pattern, reuses session management, auth, context building. aiohttp.web server (already a dep).

**Key design decisions:**
- Stateless by default (messages array is the conversation); opt-in persistent sessions via `X-Session-ID` header
- Phase 1: non-streaming (run agent, return full result as single SSE chunk) — compatible with all frontends
- Phase 2: real token-by-token SSE via `stream_callback` injected into AIAgent
- Bearer token auth via `Authorization: Bearer <key>` / `API_SERVER_KEY` env var
- Model passthrough optional — frontend can request a specific model or use whatever's configured

**Config:**
```yaml
api_server:
  enabled: true
  port: 8642
  host: "127.0.0.1"
  key: "your-secret-key"
  allow_model_override: false
  max_concurrent: 5
```

**Files:** `gateway/platforms/api_server.py` (new), `gateway/config.py` (+Platform.API_SERVER), `gateway/run.py` (+adapter registration), `cli-config.yaml.example`

---

### Gemini OAuth Provider _(2026-05-07)_

Add a first-class `gemini` provider authenticated via Google OAuth (standard Gemini API at `generativelanguage.googleapis.com/v1beta`, not Cloud Code Assist). Browser-based auth, no manual API key copy.

**Flow:** Authorization Code + PKCE (S256), localhost callback server on port 8085, fallback manual URL paste for headless/WSL.
**Scopes:** `cloud-platform`, `userinfo.email`
**Token storage:** `~/.hermes/gemini_oauth.json` (0o600), auto-refresh 5 min before expiry, file-locked for concurrent sessions.

**Key files:** `agent/google_oauth.py` (new, ~200 lines), updates to `hermes_cli/auth.py`, `hermes_cli/models.py`, `hermes_cli/runtime_provider.py`, `hermes_cli/main.py`, `run_agent.py`, `agent/auxiliary_client.py`.

**Prerequisite:** Nous Research GCP project with Desktop OAuth client registered (or `HERMES_GEMINI_CLIENT_ID` env var override).

---

## Backlog — Performance & Stability

### Git Hygiene — Stop Tracking Build Artifacts _(2026-05-07)_

**Problem:** Generated copies under `build/lib/` are tracked in git. They inflate diffs, create review noise, and make upstream syncs harder than they need to be.

**Solution:** Remove tracked build outputs from version control, regenerate only during packaging/release steps, and add a CI check that prevents accidental recommit of generated artifacts.

---

### SQLite Write Contention _(2026-05-07)_

**Problem:** Multiple concurrent writers to `~/.hermes/state.db` (gateway handling several Telegram messages simultaneously, or CLI + gateway both active). Current mitigation is application-level retries with 20–150ms jitter. Under >10 concurrent writes, risk of TUI freezes.

**Options:**
- Serialize all writes through a single async queue (low effort, keeps SQLite)
- Migrate to PostgreSQL for distributed/multi-instance setups (high effort)

**Impact:** Low for current single-user single-device use. Revisit if gateway load increases.

---

### Async-First AIAgent _(2026-05-07)_

**Problem:** `run_conversation()` in `run_agent.py` is synchronous. Gateway (async) wraps it via a thread pool — one blocking thread per active conversation. Under concurrent users this creates thread explosion and head-of-line blocking.

**Solution:** Refactor `AIAgent` / `run_conversation()` to be async-first. Threaded fallback only for genuinely sync-only tools.

**Impact:** Low for current single-user setup. High value if gateway ever serves multiple users.

---

### Context Compressor on Hot Path _(2026-05-07)_

**Problem:** Context overflow triggers an immediate blocking LLM call (the compressor) on the critical path. For 100K+ token contexts this stalls the agent loop waiting for summarisation.

**Solution:** Background async compression. If fresh compression isn't ready, fall back to the previous summary and continue; apply the fresh one on the next turn.

---

### Connection Pool TTLs _(2026-05-07)_

**Problem:** `httpx` clients in `agent/auxiliary_client.py` are reused indefinitely with no refresh cycle. Long-lived gateways can accumulate stale HTTP keep-alive connections.

**Solution:** Periodic client refresh (e.g. every 1000 requests or 1 hour). Explicit connection pool bounds.

---

### OAuth Token Refresh Races _(2026-05-07)_

**Problem:** OAuth tokens are refreshed on-demand with no distributed lock. If two platform workers simultaneously detect token expiry, both attempt refresh — duplicate calls, possible token invalidation.

**Solution:** Guard refresh with a per-credential async lock inside `credential_pool.py`.

---

### Memory Provider Failure Modes _(2026-05-07)_

**Problem:** Failures in `prefetch()`, `sync_turn()`, `handle_tool_call()` are caught and logged but don't surface to the user. Memory can silently stop persisting.

**Solution:** Optional strict mode — fail fast in dev. At minimum, surface a warning in the Telegram response when memory sync fails.

---

### Subagent Resource Limits _(2026-05-07)_

**Problem:** `max_concurrent_children` (default 8) is enforced but no per-child memory or CPU limits. Large data delegations can OOM the parent.

**Solution:** Memory budgets per child. Streaming result handling for large subagent outputs rather than buffering everything.

---

### VM Cleanup on Hard Exit _(2026-05-07)_

**Problem:** Terminal tool VM cleanup (`cleanup_vm()`) is called in `__del__` — not guaranteed on hard exit or crash. Docker containers and VMs accumulate.

**Solution:** Register explicit cleanup hooks on agent shutdown. Signal handlers (`SIGTERM`, `SIGINT`) that call cleanup before exit.

---

### Tooling Guardrails — Real Lint / Type Gates _(2026-05-07)_

**Problem:** Quality tooling exists but is not enforcing much. `ruff` is effectively disabled in `pyproject.toml`, and type checks are permissive enough that structural problems can slip through.

**Solution:** Re-enable `ruff` incrementally, start with fork-owned code and touched files, then ratchet upward. Add CI enforcement for "no new lint debt" and gradually tighten type checking in locally owned modules first.

---

## Backlog — Architecture (Refactoring)

These are from the internal architecture review (`docs/architecture-opportunities.md`, 2026-05-07). None block current use — these are depth/maintainability improvements.

### AIAgent Monolith — `run_agent.py` (12,647 lines) _(2026-05-07)_

`AIAgent` owns the agent loop, tool dispatcher, error handler, model adapter, context manager, and I/O wrapper simultaneously. Extract `ToolDispatcher`, `ContextManager`, `ModelAdapter` as deep modules. `AIAgent` becomes an orchestrator.

---

### Provider Chain — `agent/auxiliary_client.py` (684-line if/elif) _(2026-05-07)_

Provider resolution is a 30+ branch chain with no seams. Replace with a `ProviderRegistry` where providers self-register. Resolution becomes a priority-ordered lookup. Each provider is an adapter behind a common interface.

---

### Gateway Base Adapter (60% duplication across platforms) _(2026-05-07)_

`gateway/platforms/base.py` bundles approval workflows, session lifecycle, message serialisation, and tool routing. Each platform reimplements ~60%. Split into `SessionLifecycle`, `ApprovalFlow`, `MessageDelivery` modules. Platforms compose them.

---

### Config as Env-Var Relay (3-hop flow) _(2026-05-07)_

Config read in `hermes_cli/config.py` → bridged to env vars in `gateway/run.py:113–254` → re-read from env vars in `run_agent.py:56–65`. Replace with a single typed `Config` object passed directly.

---

### Memory System Ownership (6 files, no owner) _(2026-05-07)_

Memory lifecycle split across `memory_manager.py`, `memory_provider.py`, `context_compressor.py`, `context_engine.py`, `run_agent.py:3259–3320`, `plugins/memory/`. Create a `MemorySystem` module with one interface: given history, return context block.

---

### Tool Registration via Side Effects _(2026-05-07)_

82 tool files call `register()` as an import side effect. No explicit interface for "what tools exist." Replace with an explicit `ToolRegistry` — tools declare themselves, consumers query it. Toolsets validated at registration not runtime.

---

### CLI Monolith — `hermes_cli/main.py` + `cli.py` (20K combined lines) _(2026-05-07)_

60+ command handlers inline with no router. TUI rendering and command logic interleaved. Extract a `CommandRouter` — each command is a handler behind a uniform interface.

---

### Fork Customization Layer _(2026-05-07)_

**Problem:** Personal modifications are currently spread across upstream-owned core files and a few isolated plugins/scripts. That makes future upstream merges more expensive than necessary.

**Solution:** Formalize a local customization boundary: plugins, adapters, wrappers, and helper modules that behave like "upstream Hermes plus my layer." Patch upstream hotspots only through narrow seams when possible.

---

### Manual Model Routing Extraction _(2026-05-07)_

**Problem:** Manual route switching and route-marker behavior currently span `gateway/run.py`, `gateway/platforms/base.py`, `gateway/stream_consumer.py`, and command registration. The feature is useful, but the implementation is more cross-cutting than a long-lived fork wants.

**Solution:** Extract routing state, route selection, and reply decoration into a dedicated local module with a narrow gateway integration seam. Keep upstream file edits thin and mechanical.

---

### Gateway Extension Seams / Reduced Cross-Cutting Behavior _(2026-05-07)_

**Problem:** Features such as reply decoration, transcript previews, and command normalization can require edits in several gateway layers at once. This raises merge friction and makes behavior harder to reason about.

**Solution:** Define clearer extension points in the gateway lifecycle so one feature can plug in at one seam instead of touching multiple layers.

---

### Safe Extension Points Documentation _(2026-05-07)_

**Problem:** The repo has broad documentation, but not enough short, practical guidance on where local customizations should hook in without increasing merge pain.

**Solution:** Add concise architecture notes for gateway message flow, agent execution flow, plugin/hook lifecycle, and recommended fork-safe extension points.

---

### Fork-Owned Regression Coverage _(2026-05-07)_

**Problem:** Local custom behavior is safest where it has dedicated tests, but coverage is uneven. Hotspot patches without direct regression tests are easy to break during upstream syncs.

**Solution:** Add targeted tests around fork-owned behavior, especially any customization that touches upstream hotspots such as gateway routing, memory formatting, or provider-specific glue.

---

### Upstream Sync Automation / Merge Hygiene _(2026-05-07)_

**Problem:** Long-lived fork maintenance becomes brittle when upstream sync is a remembered ritual rather than a codified workflow. Even with `reference` and `origin` set up correctly, repeated merges still rely too much on human memory.

**Solution:** Keep the sync workflow repo-local and operationalized: use `scripts/sync_reference.sh`, preserve short-lived `sync/reference-*` branches, enable `rerere`, and extend the helper over time with post-sync checks and hotspot-conflict reporting.

---

## Implemented — Archive

Items below were discussed in roadmap and have since been implemented. Full details in [hermes.md](hermes.md).

### Ollama Keep-Alive _(2026-05-07)_

**Was:** Ollama evicted model from memory after 5-minute default idle timeout. Every cold start added 10–30s to first response.

**Implemented:** `OLLAMA_KEEP_ALIVE=-1` in the owned launchd plist (`~/Library/LaunchAgents/com.mh.ollama.plist`). `ollama_keep_alive: -1` in `~/.hermes/config.yaml`. `ollama_keep_alive` config key added to hermes codebase (passes as top-level `keep_alive` field on every Ollama request). Documented in `cli-config.yaml.example`.

---

### Disable Qwen Thinking for Conversational Use _(2026-05-07)_

**Was:** `qwen3.5:35b` runs extended internal reasoning scratchpad by default. Some responses took 30s+ as the model reasoned at length before answering simple questions.

**Implemented:** `reasoning: { enabled: false }` in `~/.hermes/config.yaml`. Hermes sends `think: false` in the Ollama request body. Drops response time to 5–10s for typical conversational queries. Re-enable with `reasoning: { enabled: true }` for sessions requiring deep multi-step reasoning.
