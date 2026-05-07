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

## Backlog — Performance & Stability

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

## Implemented — Archive

Items below were discussed in roadmap and have since been implemented. Full details in [hermes.md](hermes.md).

### Ollama Keep-Alive _(2026-05-07)_

**Was:** Ollama evicted model from memory after 5-minute default idle timeout. Every cold start added 10–30s to first response.

**Implemented:** `OLLAMA_KEEP_ALIVE=-1` in launchd plist (`~/Library/LaunchAgents/homebrew.mxcl.ollama.plist`). `ollama_keep_alive: -1` in `~/.hermes/config.yaml`. `ollama_keep_alive` config key added to hermes codebase (passes as top-level `keep_alive` field on every Ollama request). Documented in `cli-config.yaml.example`.

---

### Disable Qwen Thinking for Conversational Use _(2026-05-07)_

**Was:** `qwen3.5:35b` runs extended internal reasoning scratchpad by default. Some responses took 30s+ as the model reasoned at length before answering simple questions.

**Implemented:** `reasoning: { enabled: false }` in `~/.hermes/config.yaml`. Hermes sends `think: false` in the Ollama request body. Drops response time to 5–10s for typical conversational queries. Re-enable with `reasoning: { enabled: true }` for sessions requiring deep multi-step reasoning.
