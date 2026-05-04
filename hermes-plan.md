# Hermes + Open Brain Plan

## Implementation Status

As of 2026-05-04, Hermes now has a working first-pass session-close capture
path into Open Brain.

Implemented:

- shared session-boundary capture context + provenance
- first-pass extraction into `session_summary`, `action_item`,
  `decision_record`, and `durable_fact`
- local capture artifacts in `$HERMES_HOME/session_captures/`
- first-pass `openbrain` memory provider
- live durable writes into hosted `open-brain-mcp`
- Telegram `/new` reset-path capture fix for interrupted in-flight turns
- manual `/ob` capture without conversation reset
- rolling capture boundary: current conversation start or since last `/ob`

Documented runtime status, verification steps, and known issues:

- [docs/openbrain-session-capture-status.md](/Users/mh/ai/agents/hermes-agent/docs/openbrain-session-capture-status.md)

Working but not finished:

- extraction quality still needs tightening
- full content-fingerprint dedup is not implemented yet
- retrieval arbitration is not implemented yet

Current command split:

- `/ob` means "capture durable memory from this conversation so far"
- `/new` and `/reset` mean "end this conversation and start fresh"

## Purpose

This document defines how `hermes-agent` should work with `open_brain` as its primary long-term memory system.

The goal is not to create two competing memory systems.
The goal is to create one seamless user experience across:

- Hermes local/session memory
- Open Brain durable/canonical memory

Hermes is the main interface.
Open Brain is the canonical durable memory layer.
Anything we adopt from `OB1` should improve that relationship, not fragment it.

## Core Principle

The user should be able to interact with Hermes naturally and trust that:

- short-term conversational context is handled quickly
- important information is preserved durably
- retrieval is fast across both memory layers
- answers are trustworthy about where the information came from
- there is no confusing split between "Hermes memory" and "Open Brain memory"

## System Roles

### Hermes

Hermes is the front door.
It is responsible for:

- conversation
- fast working memory
- active task/session continuity
- deciding when to capture durable knowledge
- retrieving and synthesizing from both memory layers
- clearly representing confidence and source provenance

### Open Brain

Open Brain is the canonical durable memory layer.
It is responsible for:

- long-term storage
- structured knowledge
- semantic retrieval across time
- cross-interface continuity
- durable facts, decisions, summaries, records, and entities

### Relationship

Hermes should not compete with Open Brain.
Hermes should orchestrate memory use across layers.

The intended flow is:

`user -> Hermes -> Hermes local/session memory -> Open Brain durable memory -> Hermes retrieval/synthesis -> user`

## Two-Layer Memory Model

### Layer 1: Hermes Memory

Hermes memory is:

- fast
- local
- session-aware
- provisional
- optimized for continuity and responsiveness

Examples:

- the current objective
- recent chat context
- in-progress decisions not yet finalized
- temporary task state
- user phrasing preferences seen in the current session

This layer is allowed to be incomplete, compressed, or ephemeral.
It should not be treated as the final system of record.

### Layer 2: Open Brain Memory

Open Brain memory is:

- durable
- canonical
- structured where useful
- optimized for long-term retrieval
- shared across interfaces and future sessions

Examples:

- explicit user facts
- stable preferences
- accepted decisions
- structured contacts/items/records
- action items worth preserving
- summaries of high-value sessions
- operating model outputs
- imported or captured life/work knowledge

This is the layer other interfaces should be able to trust.

## Source of Truth Rules

When the two layers differ, Hermes should follow these rules:

1. Current-session nuance belongs to Hermes memory.
2. Durable facts belong to Open Brain.
3. If both agree, Hermes may answer directly and confidently.
4. If they conflict, Hermes should prefer Open Brain for stable historical fact and Hermes memory for clearly newer session-local updates.
5. If conflict cannot be resolved safely, Hermes should say so explicitly.

This means Hermes needs source-aware retrieval, not blind memory merging.

## Trust Model

Hermes responses should internally distinguish among:

- `session_memory`
- `open_brain`
- `synthesized_from_both`
- `inference`

The user-facing behavior should reflect that distinction.

Examples:

- "Earlier in this session, you said..."
- "Your Open Brain shows..."
- "Both your current session and Open Brain point to..."
- "I found partial signals, but they conflict."

Trustworthiness is more important than smoothness.
If Hermes is uncertain, it should be honest about uncertainty.

## Capture Model

Hermes must move information from local/session context into Open Brain deliberately.
Not everything should be promoted.

### Keep Only in Hermes Memory

Do not automatically promote:

- raw chatter
- low-signal brainstorming fragments
- abandoned paths
- temporary scaffolding
- speculative interpretations that were never confirmed

### Promote to Open Brain

Promote when one of the following is true:

- the user explicitly states a durable fact
- a preference appears stable and important
- a decision is made or accepted
- an action item is real and worth tracking
- a structured entity should be created or updated
- a session summary would be useful later
- the content is needed across interfaces or future sessions

### Capture Shapes

Hermes should primarily write these forms into Open Brain:

- single durable facts
- action items
- structured entity updates
- concise decision records
- concise session summaries

## Retrieval Model

Hermes should retrieve from both layers when appropriate, but not always in the same way.

### Fast Path

Use Hermes memory first when the question is clearly about:

- current session context
- active task state
- something just discussed

### Durable Path

Use Open Brain first when the question is clearly about:

- history
- known facts
- structured records
- decisions made previously
- information that should survive across sessions

### Blended Path

Use both when the question benefits from combining:

- current context
- durable memory

Examples:

- "What are we doing next on this project?"
- "What do you know about Marcus and what changed today?"
- "Summarize the state of this plan using what we discussed and what is already in my brain."

## Retrieval Arbitration Rules

When querying both layers, Hermes should:

1. retrieve session-local evidence
2. retrieve durable Open Brain evidence
3. compare overlap, novelty, and conflict
4. synthesize the answer with source awareness
5. flag contradictions instead of hiding them

Hermes should avoid:

- presenting inferred results as stored facts
- flattening conflicting evidence into a single confident answer
- over-trusting fresh but weak session notes over durable records

## Performance Goals

The user asked for capture and retrieval to be quick, efficient, and trustworthy.
That implies different optimization priorities per layer.

### Hermes Layer

Optimize for:

- low latency
- short-context continuity
- active-state lookups
- minimal overhead during conversation

### Open Brain Layer

Optimize for:

- durable writes
- semantic retrieval quality
- structured access where available
- idempotent capture
- provenance and metadata quality

### Combined Experience

The user should experience:

- fast current-context recall
- strong historical recall
- low duplication
- clear provenance
- minimal friction between capture and retrieval

## Hermes Compatibility Constraint

This is the most important product constraint:

Hermes is the interface used most often, so any change to memory architecture must preserve or improve seamless Hermes-to-Open-Brain operation.

That means:

- no parallel "main" systems
- no alternative canonical store
- no OB1 adoption that bypasses Hermes
- no feature that makes Open Brain harder for Hermes to reason about

All future work should be tested against this question:

"Does this make Hermes better at capturing to, retrieving from, and explaining Open Brain?"

If the answer is no, it is likely not worth doing.

## OB1 Reuse Filter

We only want ideas from `OB1` that strengthen the Hermes/Open Brain relationship.

### Worth Reusing Soon

- content fingerprint dedup
- auto-capture protocol
- contribution packaging pattern

### Worth Reusing Later

- Slack capture, if Hermes benefits from another capture surface
- dashboard concepts, if they help inspect or audit Open Brain

### Not a Priority Right Now

- multi-user RLS patterns
- extension curriculum unrelated to Hermes/Open Brain flow
- any product surface that creates a second primary interface

## Immediate Priorities

### 1. Auto-Capture Convention

This is the fastest win.

Define a Hermes behavior where session-close writes:

- one concise session summary
- separate durable action items
- only high-signal outputs

This should not dump raw conversation into Open Brain.

### 2. Content Fingerprint Dedup

This is the highest-leverage infrastructure improvement.

It should protect Open Brain against:

- duplicate imports
- retry-based duplicate capture
- overlapping write surfaces
- repeated session summaries or repeated action-item capture

### 3. Contribution Packaging Pattern

This is worth adopting lightly inside our own repo organization.

Use it to make Hermes/Open Brain integration easier to understand:

- `recipes/` for repeatable workflows
- `skills/` for reusable agent behavior
- `integrations/` for Telegram, Strava, Slack, MCP connectors
- `primitives/` for reusable building blocks like dedup and routing

This should be incremental, not a disruptive replatforming effort.

## Recommended Design Direction

The clean design direction is:

1. Hermes is the main interface.
2. Open Brain is the canonical durable memory.
3. Hermes keeps a fast local/session layer for continuity.
4. Hermes promotes important information into Open Brain deliberately.
5. Hermes retrieves from both layers using source-aware arbitration.
6. Hermes explains answers in a way that reflects provenance and uncertainty.

## Tomorrow-Ready Work Breakdown

When implementation starts, sequence it like this:

1. define the auto-capture rules and trigger points in Hermes
2. add content fingerprint dedup to Open Brain write paths
3. document or implement retrieval arbitration between Hermes memory and Open Brain
4. only then consider new capture surfaces like Slack
5. gradually improve packaging/organization as we touch adjacent areas

## Implementation Checklist

This checklist turns the plan into concrete execution work.
It is ordered to match the intended rollout sequence and to keep Hermes/Open Brain alignment as the main constraint.

### Phase 1: Define Auto-Capture Behavior

- [x] define the exact session-close trigger points in Hermes
- [x] define which conversations or sessions are eligible for capture
- [x] define the promotion rules for durable facts, preferences, decisions, action items, and session summaries
- [x] define explicit exclusion rules for chatter, abandoned ideas, and speculative content
- [x] define a compact session-summary schema for Open Brain writes
- [x] define an action-item schema with status, owner, and provenance fields
- [x] define a decision-record schema with confidence and provenance
- [x] define whether capture happens automatically, explicitly on command, or both
- [x] define failure behavior when Open Brain is unavailable at session close
- [x] define logging or audit traces so capture decisions can be inspected later

### Phase 2: Implement Auto-Capture in Hermes

- [x] add a capture decision step to the session-close flow
- [x] extract high-signal candidates from session-local memory
- [x] split extracted outputs into summary, action items, decisions, and durable facts
- [ ] filter low-signal or unconfirmed content before any Open Brain write
- [x] attach provenance metadata to every capture payload
- [x] write concise session summaries to Open Brain
- [x] write action items as separate records rather than embedding them only in summaries
- [x] make capture idempotent enough to tolerate retries safely
- [x] surface capture success, partial failure, or skip reasons in Hermes logs

### Phase 3: Add Content Fingerprint Dedup

- [ ] define the canonical fingerprint inputs for each capture shape
- [ ] ensure summaries, action items, decisions, and entity updates fingerprint consistently
- [ ] check fingerprints before write on all Open Brain capture paths
- [ ] skip exact duplicates without creating noisy secondary records
- [ ] support safe retries where the same payload is submitted again
- [ ] preserve provenance even when a duplicate is detected
- [ ] document the difference between true duplicates and meaningful updates

### Phase 4: Define Retrieval Arbitration

- [ ] define the response source labels: `session_memory`, `open_brain`, `synthesized_from_both`, and `inference`
- [ ] define fast-path routing rules for current-session questions
- [ ] define durable-path routing rules for historical or structured questions
- [ ] define blended-path routing rules for questions that need both layers
- [ ] define conflict handling rules for session-vs-durable disagreements
- [ ] define how Hermes should communicate uncertainty and contradiction to the user

### Phase 5: Implement Retrieval Arbitration

- [ ] retrieve session-local evidence separately from Open Brain evidence
- [ ] compare overlap, novelty, and conflict before synthesis
- [ ] prevent inferred statements from being presented as stored facts
- [ ] prefer Open Brain for stable historical facts unless a clearly newer session update overrides it
- [ ] generate answer metadata that supports user-facing provenance language
- [ ] add lightweight tests for agreement, conflict, and partial-information cases

### Phase 6: Packaging and Repo Clarity

- [ ] identify current code paths that map naturally to `primitives/`, `integrations/`, `skills/`, and `recipes/`
- [ ] move only touched or newly added Hermes/Open Brain components into clearer structure
- [ ] avoid broad replatforming that delays capture and retrieval improvements
- [x] document the resulting architecture in a short repo-facing developer note

### Acceptance Checks

- [ ] Hermes can end a session and write one concise summary plus separate durable action items
- [ ] Open Brain does not accumulate obvious duplicate captures from retries or repeated triggers
- [ ] Hermes can answer with clear provenance when information comes from session memory, Open Brain, or both
- [ ] Hermes flags contradictions instead of flattening them into false certainty
- [ ] the overall flow still feels like one system, not two competing memory products

## Phase 1 Spec: Auto-Capture

This section resolves Phase 1 into concrete product and implementation decisions.
It is intentionally aligned to the Hermes code paths that already exist today.

### Existing Hermes Trigger Surfaces

Hermes already has real session-boundary mechanisms we should build on:

- CLI session rotation via `/new`
- CLI process exit / shutdown
- gateway `/new` and `/reset`
- gateway session expiry finalization
- TUI session finalization on close/shutdown
- compression-driven `session_id` rotation during long conversations

That means Phase 1 should be implemented as a capture policy layered onto existing finalize/rotation behavior, not as a new parallel session system.

### Capture Trigger Policy

Auto-capture should run on these boundaries:

1. manual session reset or new-session actions
2. session expiry finalization
3. clean shutdown of an active session
4. compression-driven session rotation

The trigger should be best-effort and non-blocking for the user-facing reset or shutdown path.
If capture fails, Hermes should log the failure and continue closing or rotating the session.

### Capture Eligibility Rules

Run session-close capture only when at least one of these is true:

- the session contains a real user-assistant exchange, not just a startup shell
- at least one high-signal candidate was identified
- the session produced explicit action items, durable facts, preferences, or accepted decisions
- the session had enough meaningful activity that a concise summary would help later retrieval

Skip capture when any of these are true:

- the session has no meaningful conversation history
- the session is only raw chatter or speculative brainstorming with no confirmed outcome
- the session was interrupted before a usable conversational state exists
- the extracted output is entirely duplicate of what Hermes already plans to write for that boundary

### Capture Output Contract

Each eligible boundary may emit up to four capture shapes:

1. one `session_summary`
2. zero or more `action_item`
3. zero or more `decision_record`
4. zero or more `durable_fact`

The key constraint is that action items and decisions are first-class records, not details buried only inside the summary.

### Session Summary Rules

Each finalized session should write at most one concise summary.

The summary should contain:

- the main objective or topic
- the outcome or current state
- important changes or resolved questions
- references to linked action items or decisions when they exist

The summary should not contain:

- raw transcript dumps
- speculative reasoning that was never confirmed
- long tool traces
- low-level scratchpad content

Recommended fields:

- `type`: `session_summary`
- `session_id`
- `parent_session_id` when applicable
- `boundary_reason`: `new_session`, `session_reset`, `compression`, `session_expiry`, `cli_close`, `tui_close`, or equivalent
- `platform`
- `user_id` or stable person identifier when available
- `summary_text`
- `topics`
- `source_count`
- `captured_at`
- `provenance`

### Action Item Rules

Create separate action-item records only for real, durable follow-ups.

An item qualifies when:

- it has an explicit or strongly implied next step
- it matters beyond the immediate turn
- it would be useful across sessions or interfaces

Do not create an action item for:

- vague ideas
- abandoned options
- rhetorical suggestions
- tool-internal TODOs the user did not actually adopt

Recommended fields:

- `type`: `action_item`
- `session_id`
- `title`
- `details`
- `status`: default `open`
- `owner`: `user`, `hermes`, or named entity when known
- `due_hint` when stated
- `related_entities`
- `captured_at`
- `provenance`

### Decision Record Rules

Create a decision record when the user accepts a direction, chooses among options, or confirms a lasting conclusion.

Recommended fields:

- `type`: `decision_record`
- `session_id`
- `decision`
- `rationale_summary`
- `status`: `accepted` or `confirmed`
- `confidence`
- `captured_at`
- `provenance`

### Durable Fact Rules

Create durable facts only for stable information that should survive the session.

Examples:

- stable user preference
- biography or relationship fact
- accepted project constraint
- persistent environment or workflow preference

Do not capture:

- one-off phrasing choices
- transient local state
- unverified inference

Recommended fields:

- `type`: `durable_fact`
- `subject`
- `predicate`
- `object` or `value`
- `confidence`
- `valid_from` when known
- `captured_at`
- `provenance`

### Provenance Minimum

Every capture record should include enough provenance for Hermes to explain where it came from later.

Minimum provenance fields:

- `source_layer`: `session_memory`
- `session_id`
- `boundary_reason`
- `platform`
- `message_refs` or equivalent turn references
- `captured_by`: `hermes_auto_capture`
- `captured_at`

### Failure and Retry Policy

If Open Brain is unavailable at capture time:

- Hermes should not block the user-facing boundary action
- Hermes should log a structured capture failure
- Hermes should preserve enough payload metadata to support a safe retry path
- retries must be idempotent once fingerprint dedup lands in Phase 3

Before dedup is implemented, Hermes should still avoid naive repeated writes from the same boundary event when possible.

### Phase 1 Implementation Anchors

The first implementation pass should hook into the existing session-boundary surfaces already present in Hermes:

- CLI session rotation and finalize flow
- gateway reset/finalize flow
- gateway expiry watcher
- TUI finalization
- compression-driven `commit_memory_session()` path

The design target is to route all of these through one shared capture-policy layer so Hermes has one consistent rule set for promotion into Open Brain.

## Capture Architecture: Two-Tier Confidence Gating

This section defines the refined capture approach that supersedes simple broad capture.
It is the recommended implementation target for Phase 1 and Phase 2.

### Core Idea

At session close, Hermes classifies capture candidates and scores each one by confidence.
High-confidence items write directly to Open Brain as canonical records.
Low-confidence items go into a `pending` staging bucket — captured and queryable, but not treated as authoritative.

This protects retrieval quality from day one while still capturing broadly enough to avoid missing signal.

### Confidence Gating

At session close, Hermes runs a classification pass over extracted candidates.
Each candidate receives:

- a proposed capture shape (`session_summary`, `action_item`, `decision_record`, `durable_fact`)
- a confidence score
- a routing decision: `canonical` or `pending`

The confidence threshold should start loose so more items go to `pending` during early operation.
Tighten the threshold as audit patterns stabilize across multiple audit cycles.

Items routed to `canonical` write directly into Open Brain and are treated as authoritative immediately.
Items routed to `pending` write into the staging bucket with a decay timestamp attached.

### The Pending Bucket

The `pending` bucket is a first-class Open Brain staging layer, not a secondary log.

Properties:

- queryable by Hermes but deprioritized in confident answers
- each item carries a `decay_at` timestamp set to 14 days from capture
- items that reach `decay_at` without being audited are automatically removed
- items that are promoted during audit move into the canonical layer with full provenance intact
- items that are reclassified during audit are corrected and promoted as the right shape
- items that are discarded during audit are removed without trace

Hermes should be able to answer questions using `pending` content but must signal lower confidence when it does:

- "I have an unaudited note from last week that suggests..."
- rather than treating it with the same weight as a canonical record

### Weekly Audit Loop

The audit surfaces only the `pending` bucket, not the full canonical store.
This keeps the audit surface small and focused on exactly the items Hermes was uncertain about.

Each audit cycle:

1. Hermes surfaces all `pending` items grouped by session or by proposed shape
2. user reviews each item: promote as-is, reclassify to a different shape, or discard
3. promoted items move to canonical with corrected shape and updated provenance
4. discarded items are removed
5. items not touched remain in `pending` until `decay_at` is reached

The audit should be completable inside Hermes — no external tooling required.
Promote, reclassify, and discard should be single-turn actions.

### Learning Loop

Each completed audit cycle is a labeled correction signal.

Hermes should use audit outcomes to improve future classification:

- track which session characteristics or content patterns produced misclassified candidates
- update classification prompt rules or few-shot examples after each audit cycle
- gradually tighten the confidence threshold as precision improves
- after several cycles, the `pending` queue should shrink as Hermes learns what qualifies

The goal is not a fixed rule set defined in advance.
The goal is a classification model that calibrates itself through real session data and real audit corrections.

### Rationale

This approach was chosen over alternatives for the following reasons:

- **vs. pure broad capture**: canonical layer stays clean from day one; retrieval quality is not degraded during the learning phase
- **vs. strict upfront classification rules**: high recall early; rules improve from real data rather than speculation
- **vs. full post-session review**: user only reviews uncertain items, not everything; audit surface stays manageable
- **vs. inline confirmation during conversation**: no friction during active sessions; classification is async and non-blocking

The 14-day decay creates natural pressure to maintain the audit cadence without making it feel like cleanup work.
Anything that matters gets promoted. Anything that doesn't disappears.

### Integration with Phase 3 Dedup

Before Phase 3 fingerprint dedup is implemented, Hermes should avoid naive repeated writes from the same boundary event.
Once dedup lands, canonical and pending writes are both protected against retry duplication.
Decay-and-repromote paths also become safe to retry once fingerprints are in place.

## Recommended Next Steps

The direction is still correct:

- Hermes should remain the only main interface
- Open Brain should remain the only canonical durable memory
- Hermes should orchestrate capture and retrieval across layers

What should change is rollout strategy.
The first implementation should stay simple, stable, and easy to evaluate in the real world before more elaborate memory workflows are added.

### What Not To Do First

Do not start by building:

- a second bot or second primary interface
- a full pending-memory product surface
- a broad audit workflow with too many moving parts
- a self-training or self-tuning memory loop before baseline behavior is proven

Those ideas may become useful later, but they are not the right first implementation.

### MVP Rollout

The first production version should do only this:

1. Hermes remains the only interface
2. Open Brain remains the only durable store
3. session close writes one concise session summary
4. real follow-up items are written as separate action items
5. every write includes provenance metadata
6. dedup is added early to protect against retries and repeated boundaries
7. retrieval is updated to explain whether an answer came from session memory, Open Brain, or both

This is enough to prove the core value without overbuilding the system.

### Real-World Testing First

After the MVP exists, the next step is to run it in normal Hermes usage and observe:

- whether summaries are actually useful later
- whether action-item capture is accurate enough
- whether canonical memory quality stays clean
- where false positives or missed captures happen
- whether provenance explanations feel trustworthy to the user

The purpose of this phase is to learn from real usage, not to optimize from theory.

### Build On Top Only If Needed

Only after real-world testing should Hermes add more advanced layers such as:

- pending vs canonical confidence gating
- staged audit workflows
- decay windows for uncertain captures
- learning loops from audit outcomes

Those should be added only if real usage shows that canonical capture quality needs more protection.

### Decision Standard

The practical standard should be:

- implement the smallest version that creates real value
- test it in real Hermes sessions
- inspect the failure modes
- only then decide if more structure is necessary

The goal is not to build the most sophisticated memory architecture first.
The goal is to build the simplest memory architecture that works well in actual use, then extend it only when the evidence says to.

## Non-Goals

These are explicitly not the goal right now:

- replacing Open Brain with Hermes memory
- replacing Hermes with an OB1-style interface
- building a second canonical memory store
- building a broad multi-user platform before single-user Hermes/Open-Brain flow is excellent
- importing OB1 wholesale

## Final Position

The right model is not "Hermes or Open Brain."
The right model is:

- Hermes for interaction and short-term intelligence
- Open Brain for durable memory and retrieval
- one seamless experience across both

That is the standard future work should protect.
