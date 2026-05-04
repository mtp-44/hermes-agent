# Unified Retrieval Architecture

Authored: 2026-05-04

## Problem

There are currently three separate retrieval implementations that diverge from each other:

1. **Open Brain bot** — vector search on `thoughts` only, with a shadow thought bridge hack for structured tables
2. **Hermes** — nine separate MCP tools the model has to guess between
3. **Direct Supabase** — used internally by the Open Brain bot Python engine

Every time the data model evolves, all three must be updated. This is why Strava data (written directly to `life_items`) is invisible to the Open Brain bot, and why Hermes has to reason about which of nine search tools to call for a conversational memory question.

The root fix is one retrieval implementation shared by all surfaces.

---

## Principle

**Capture stays per-surface. Retrieval is unified.**

Each capture surface has real strengths worth keeping:
- The Open Brain bot has the richest capture UX — classify, confirm, correct, alternative classification
- Hermes has session-boundary capture with confidence gating and provenance
- The Strava sync has dedup via `details.strava_id` and schema alignment

None of that should change. The problem is retrieval, not capture.

---

## Architecture

```
Capture (per-surface, unchanged):
  Open Brain bot  → classify → confirm → save to correct table + provenance thought
  Hermes /ob      → session capture → capture_thought (MCP)
  Hermes mid-conv → model calls add_* MCP tools directly
  Strava sync     → life_items directly (nightly, via launchd)

Retrieval (all surfaces → one implementation):
  Open Brain bot  ─┐
  Hermes          ─┼──→  query_brain (MCP tool)  →  parallel search all tables  →  ranked results
  Any future client ┘
```

`query_brain` is a new tool added to the hosted `open-brain-mcp` Supabase edge function.
It takes a natural language query and returns ranked, merged results from every table
with per-result provenance. No client needs to know the schema.

---

## Why the hosted MCP is the right location

| Option | Why not |
|---|---|
| Python engine (local) | Hermes would need a network path to the local Mac — fragile, breaks when Hermes isn't running locally |
| Hermes orchestrates for Open Brain bot | Creates a hard dependency — Open Brain bot breaks if Hermes is down |
| Expand Open Brain bot's `_build_retrieval_context` | Two retrieval implementations again — local Python and MCP diverge over time |

The hosted MCP is always available (Supabase edge function, not a local process), already
the interface Hermes uses for everything, already the interface any future client would use,
and has direct Supabase credentials with no extra hops.

---

## Embedding situation

The embedding model is already standardised: `openai/text-embedding-3-small` via OpenRouter,
1536 dimensions, stored in pgvector on the `thoughts` table.

The Supabase edge function can call the same OpenAI embedding API, producing vectors in the
same space as stored embeddings. No migration needed.

**Gap:** `life_items`, `contacts`, `finance_records`, `home_items`, and `records` do not have
embedding columns. For these tables, `query_brain` uses structured field matching (date ranges,
categories, text search on `name`/`content` fields). This is the correct approach — a Strava
query is "rides in May 2026", not a semantic similarity search.

---

## What `query_brain` does

```
query_brain(query: string, tables?: string[]) → results[]
```

1. Generate embedding for the query (`text-embedding-3-small` via OpenAI API)
2. In parallel:
   - Vector similarity search on `thoughts` (existing `match_thoughts` RPC)
   - Full-text search on `life_items` (name + notes)
   - Full-text search on `records` (name + description)
   - Fuzzy/exact search on `contacts` (name)
   - Full-text search on `finance_records` (description)
   - Full-text search on `home_items` (name + notes)
3. Score and merge:
   - Thoughts: use similarity score directly
   - Structured tables: score by text match quality + recency
4. Return top-N results, each with: `table`, `id`, `content_summary`, `score`, `metadata`, `created_at`

The LLM (Hermes or the Open Brain bot's retrieval model) synthesizes the results into a
natural language answer. `query_brain` handles the hard part — knowing where to look.
The LLM handles synthesis.

The optional `tables` parameter lets callers narrow the search when context makes the
relevant table obvious (e.g. a Strava-specific query could pass `tables: ["life_items"]`).
Default behaviour searches all tables.

---

## Impact per surface

### Hermes

Retrieval becomes dramatically simpler. Instead of reasoning about which of nine search
tools to call, the model calls `query_brain("how far did I ride last month")` and gets
back the right Strava records.

The individual `search_*` tools remain available for explicit structured operations
(filtered lists, date-range exports). `query_brain` handles all conversational retrieval.

Hermes also becomes **stable with respect to schema changes**: when a new table is added
to Open Brain, `query_brain` is updated once and Hermes retrieves from it automatically
with no changes to Hermes itself.

### Open Brain bot

`_build_retrieval_context` is replaced by a single MCP call to `query_brain`. The shadow
thought bridge (the `ref_table` + `ref_id` reference thought written on every structured
capture) can be retired once unified retrieval is validated — it was always a workaround
for the missing unified layer.

Strava data becomes immediately retrievable from the bot.

The capture flow is untouched.

### Any future surface

A new client gets full retrieval from day one by calling one tool. No knowledge of the
schema required.

---

## Shadow thoughts

Currently, when the Open Brain bot saves to a non-thoughts table, it also writes a
reference thought so that retrieval works:

```python
# Save a reference thought so retrieval still works until unified search is built
metadata["ref_table"] = table
metadata["ref_id"] = str(saved.get("id", ""))
await storage.save_thought(content=text, embedding=embedding, metadata=metadata)
```

The comment explicitly acknowledges this is a temporary bridge. Once `query_brain`
searches all tables directly, these shadow thoughts are no longer needed for retrieval.

They may still be useful as lightweight provenance records ("this life_item was captured
on [date] from [source]"), but should be made explicit rather than remaining an
undocumented workaround. Post-Phase 3, the shadow thought write can be removed or
replaced with a slimmer provenance record.

---

## Hermes-specific retrieval guidance

Once `query_brain` exists, Hermes needs a short system prompt addition to tell it when
to use the new tool vs the individual tools:

> For any conversational memory or retrieval question, use `query_brain` — it searches
> across all memory tables and returns ranked results. Use individual `search_*` tools
> only for explicit structured filtering (e.g., listing all contacts, filtering rides by
> date range). Use `add_*` and `update_*` tools for writes.

This collapses nine search decisions into one, making Hermes easier to reason about and
less likely to miss data that lives in a table it didn't think to search.

---

## Implementation phases

### Phase 1 — Add `query_brain` to the hosted MCP (~2 days)

Add a new tool to the `open-brain-mcp` Supabase edge function (Deno/TypeScript).
The pattern is established by the existing `work-operating-model-mcp` function.

Steps:
- Add OpenAI embedding call (same model as Python engine)
- Add parallel Supabase queries across all tables
- Implement score normalisation and result merging
- Add `tables` filter parameter
- Return unified result schema with provenance fields

This is the only phase that requires significant new code.

### Phase 2 — Update Hermes (~2 hours)

- Add retrieval guidance to Hermes system prompt (see above)
- Verify `query_brain` resolves Strava, contacts, thoughts, and mixed queries correctly
- The existing MCP config in `~/.hermes/config.yaml` already points at the right endpoint

### Phase 3 — Update Open Brain bot (~half day)

- Replace `_build_retrieval_context` in `core/engine.py` with a `query_brain` MCP call
- The bot already has the MCP access key; add a lightweight MCP client or use `httpx`
  to call the tool directly
- Validate that the bot can now answer Strava questions

### Phase 4 — Retire shadow thoughts (~half day, after Phase 3 is stable)

- Remove the `ref_table`/`ref_id` shadow thought write from `_save_classified`
- Optionally replace with a slimmer provenance record (no embedding, no retrieval weight)
- Validate that retrieval quality is unchanged (it should improve — no more shadow
  thought noise in results)

---

## Why this is future-proof

| Change | What happens |
|---|---|
| New table added to Open Brain | Update `query_brain` once — all surfaces retrieve from it |
| Ranking algorithm improved | Update `query_brain` once — every surface gets better retrieval |
| New capture surface added | It calls `query_brain` — full retrieval from day one |
| Embedding model changed | Migrate stored embeddings once, update `query_brain` — clients unchanged |
| New retrieval surface (voice, web) | Calls `query_brain` — no schema knowledge required |

---

## Non-goals

- Replacing per-surface capture logic — each surface's capture UX is a strength
- Building a query language or filter DSL — `query_brain` is natural language in, results out
- Real-time indexing of structured tables — text search on existing fields is sufficient
- Multi-user support — this is a single-user system; RLS patterns are out of scope

---

## Related documents

- [openbrain-session-capture-status.md](openbrain-session-capture-status.md) — current Hermes capture implementation status
- [hermes-plan.md](../hermes-plan.md) — full Hermes/Open Brain memory architecture plan
- Open Brain engine: `/Users/mh/ai/open_brain/core/engine.py`
- Hosted MCP example: `/Users/mh/ai/open_brain/wom/supabase/functions/work-operating-model-mcp/index.ts`
