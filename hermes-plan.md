# Hermes + Open Brain Plan

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
