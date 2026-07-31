# Fork Policy — Pinned Appliance (2026-07-02)

> Part of the lean pivot: canonical plan at
> `/Users/mh/ai/way_of_working_multiple_ai/MINIMAL_SCOPE.md`, work queue at
> `.../CHECKLIST.md`. This fork is an **interchangeable appliance**, not an
> investment target. The durable asset is open_brain; this gateway is one of
> several possible surfaces for it.

## The policy

1. **Pinned.** The fork is pinned at the 2026-07-02 full upstream sync
   (PR #6, ~1,160 commits — `main` fully caught up to `reference/main`).
   There is **no cadence sync**. Pull from upstream only for:
   - security fixes (the only standing reason), or
   - a specific feature Mark explicitly asks for.
2. **No load-bearing logic in core.** All Mark-specific behavior lives in
   plugins and the generic seams (`act:` feedback events, plugin hooks —
   Phase 5c). The only accepted exception is `tools/strava_tool.py`
   (documented debt, 2026-06-26). Anything new goes in
   `~/.hermes/plugins/` or `plugins/`, never in core paths.
3. **Swap, don't debug.** If a future upgrade or OS change breaks the
   gateway in a way that costs more than ~half a day, the correct response is
   to evaluate replacing the surface (any MCP-capable agent can mount
   open_brain), not to invest in the fork. The migration insurance is
   `scripts/openbrain_conformance_smoke.py` — it defines, executably, what
   any surface must do to host open_brain.
4. **Sync gate.** Any upstream pull, for any reason, must pass
   `scripts/openbrain_conformance_smoke.py` plus
   `tests/plugins/test_routing_classifier_plugin.py` before the live gateway
   moves.

## Sync history

Upstream pulls under policy 1 (security fixes only). Newest first.

### 2026-07-30 — PR #7 `sync/security-2026-07-22` merged into the live branch

13 commits (12 non-merge + the PR merge), authored 2026-07-22, sat unmerged in
production for 8 days because the live tree was mid-`~/ai`-rebuild:
credential-header stripping on cross-host redirects (`fetch_models`),
dashboard `.env`/credential-store read guards (widened across three
successive commits — case-insensitivity, suffix variants, other basenames),
a shared local-file credential-read guard for vision/image-gen inputs,
explicit `client_max_size` caps on three previously-uncapped aiohttp
servers, a chunked-body size limit on the Raft adapter, and a CI fix
routing untrusted refs through `env:` instead of `run:` interpolation.

Merged into `fix/session-capture-signal-gate`, the branch production ran at
the time, rather than `main`. **That reasoning was wrong and was corrected on
2026-07-31 — see [`HA-0003`](decisions/0003-production-returns-to-main.md).**
It cited policy 1, but policy 1 governs upstream pull cadence and says nothing
about which branch is production; this fork has no upstream remote configured
at all, so `main` was not tracking upstream either. `main` has since been
fast-forwarded onto that branch and production runs from `main`. Future
security pulls target `main`. Sync gate (`scripts/openbrain_conformance_smoke.py` +
`tests/plugins/test_routing_classifier_plugin.py`) passed; full suite
diffed pre/post-merge with zero regressions (one previously-flaky test
newly passing). All 9 new/extended security test files exercised directly
and confirmed passing, not just process-start checked. Deployed to all
three live services (`ai.hermes.gateway`, `com.mh.hermes-pwa`,
`com.mh.hermes-dashboard`) same day. Full account: ADR
[`HA-0002`](decisions/0002-security-sync-2026-07-22-merge.md).

## Permitted known divergences

Divergence from upstream is a cost, and this list is the whole of it. Keep it
short; anything not on it is a bug.

### 1. `AGENTS.md` — the estate point-up block (2026-07-29)

**What.** A 13-line block prepended to the top of `AGENTS.md`: four `>` lines
naming the estate map, the cross-repo decision ledger, `STATUS.md` and this
repo's `docs/INDEX.md` by absolute path, plus an HTML comment explaining itself
and an `END ESTATE POINT-UP BLOCK` marker.

**Why it is worth a divergence.** `AGENTS.md` is upstream-tracked and 71 KB, and
policy 1 above says pull only for security. Prepending to it guarantees a
conflict at the top of the file on every future sync. The alternative — leaving
this repo out of the estate doc spine — means the fork is *invisible* to the
spine: an agent entering `~/ai/hermes-agent` gets no route upward to the estate
map or the decision ledger, and `docs_check.py` cannot assert the link exists.
A four-line, always-"ours" conflict is a fair price for the fork not being a
blind spot. Ruled 2026-07-29 during Phase 4 of the `~/ai` rebuild.

**Standing resolution rule.** On any upstream sync that conflicts here:

> **Take "ours" for everything above the `END ESTATE POINT-UP BLOCK` marker, and
> "theirs" for everything below it.**

The marker exists precisely so this is mechanical rather than a judgement call.
If upstream ever adds its own content above the first `#` heading, merge it
*below* the marker. Never resolve by dropping the block — that silently
un-links the fork from the estate, and nothing will fail loudly.

**Verification.** `/Users/mh/ai/bootstrap/scripts/docs_check.py` asserts the
block is present and that all four absolute targets resolve. Run
`cd /Users/mh/ai/bootstrap && make check` after any sync that touches
`AGENTS.md`.

Recorded as an ADR at
[`docs/decisions/0001-agents-md-fork-divergence.md`](decisions/0001-agents-md-fork-divergence.md).

## Designated successor (evaluated 2026-07-02)

If/when rule 3 fires, the concrete swap candidate is
**[vellum-assistant](https://github.com/vellum-ai/vellum-assistant)**
(MIT, very active, v0.10.x at evaluation). Code-read findings:

- **Full MCP client** (official SDK; stdio/SSE/streamable-HTTP) with
  **per-server custom headers** — mounts open_brain with the existing
  `x-brain-key` today, and ships an MCP OAuth provider for checklist A4.
- **Covers the whole surface set, maintained upstream:** macOS/iOS/web
  clients in-repo; gateway speaks Telegram, Slack, email, voice.
- **Local mode is real:** Ollama models, local ONNX embeddings —
  **pin `memory.embeddings.provider` to local/ollama** (default
  auto-falls-back to cloud, which would add an undocumented processor).
- **Security posture strong:** actor tiers, credential isolation in a
  separate process, sandboxed tools, deny-by-default.
- **Known tension:** its native 8-type memory system is the product core
  and would accumulate a parallel profile beside open_brain. Acceptable
  only as the local working-set tier; open_brain stays canonical via MCP.

**Trial gate (must all pass before any switch; trial itself is post-Phase-A
work and needs Mark's go under the lean rules):**

1. `scripts/openbrain_conformance_smoke.py` passes against open_brain
   mounted in Vellum (same capture/retrieve contract as Hermes).
2. Vellum's native memory can be scoped down/disabled to working-set size —
   the first unknown to resolve; if "you" accumulates in the appliance
   beyond a working set, walk away.
3. Embeddings provably pinned local (no silent cloud fallback), and any new
   processor documented in `open_brain/BRAIN.md` BEFORE the trial.
4. Proactive delivery unaffected (open_brain's own `daily_digest.py` +
   Telegram bot are independent of the conversation surface and keep running
   either way).

Fallback surface if no successor qualifies at swap time: open_brain's own
Python Telegram bot (capture + proactive) + Claude clients over the hosted
MCP (retrieval/conversation) — zero gateway at all.

### Re-check 2026-07-22 (v0.10.5 → v0.10.11, code-read)

Still qualifies; policy stays standby. Gate movement since the evaluation:

- **Gate 2 (memory scoping) — the "first unknown" is ANSWERED, favorably.**
  `memory.enabled: false` is a global kill switch (gates background memory
  jobs, embedding generation, and `<memory>` injection; explicitly wins over
  both v2 and v3 per `assistant/src/config/memory-v3-gate.ts`). Finer knobs
  exist: `memory.retention`/`cleanup`, per-tier `v2.enabled`/`v3.live`, and
  an `assistant memory items` CLI (v0.10.6) to edit/delete individual
  memories. The walk-away condition is now controllable, not a mystery.
- **Gate 3 (embeddings) — still pinnable, caveat unchanged.** Provider enum
  `auto|local|openai|gemini|ollama`; default remains `auto` (cloud
  fallback), so the trial must still set `provider: ollama` (or `local`
  in-process ONNX). Vectors stay in local Qdrant.
- **Gate 1 (MCP mount) — infrastructure grown**: per-server header store +
  MCP auth orchestrator + OAuth routes all present; the A4 OAuth path is
  more built-out than at evaluation.
- **Self-hosting is first-class and accelerating** (all landed 2026-07-22):
  tailscale tunnel provider, `vellum pair --qr` device pairing, iOS
  self-hosted-server URL field + guide — matches the house pattern
  (launchd + tailnet).

New watch-items for any future trial:

1. **Managed-platform pull is strengthening**: "Vellum account" with credit
   balances, speech that auto-enables on account connect, one-connection
   hosted models, Atlas Cloud gateway. None mandatory — but a trial config
   must deliberately NOT connect a Vellum account.
2. **Memory is getting more central, not less**: v3 (concept pages, memory
   graph, procedural-memory-as-skills) is default-on for new assistants and
   v0.10.6 auto-enabled improved retrieval for everyone. The kill switch
   covers it, but the known tension grows with the product.

Health at re-check: 915 stars, releases every 3–5 days, v0.10.11
(2026-07-21), commits same-day.

## What is Mark's in this fork (the portable inventory)

- `plugins/routing-classifier/` — deterministic local/frontier route
  classifier (+ tests). Passive, propose-only; the build track around it is
  closed (see `way_of_working_multiple_ai/ROUTING_CLASSIFIER.md`).
- `~/.hermes/plugins/model-badge/` — separate user-plugin repo, not in this
  fork.
- `tools/strava_tool.py` — accepted debt.
- `scripts/openbrain_conformance_smoke.py` + seam docs
  (`docs/openbrain-hermes-seam-design.md`, touchpoint inventory).
- Config in `~/.hermes/` (managed via `hermes config` CLI, never hand-edited
  — see the config-corruption incident, fixed 2026-07-01).

Everything else is upstream's code. Keep this list short; it is the measure of
how cheap leaving would be.

## Branches

- `main` — the pinned production state.
- `pdf-extraction-phase8.1` — **keep**: preserved extractor code (pdfplumber
  text + table extraction) that CHECKLIST.md Phase B cherry-picks from. The
  `read_file` inlining on that branch must NOT be re-landed (it poisoned
  prompts with O(document) tokens; rolled back 2026-06-27).
