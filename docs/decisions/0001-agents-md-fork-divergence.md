---
id: HA-0001
date: 2026-07-29
repo: hermes-agent
status: active
tags: [fork-policy, doc-spine, upstream-sync]
verdict: "Accept a permanent 13-line divergence at the top of the upstream-tracked AGENTS.md so the fork is visible to the estate doc spine; resolve it 'ours' above the END ESTATE POINT-UP BLOCK marker on every sync. **Extended to 25 lines on 2026-08-19** — the marker, not the count, is the boundary: an estate-local operational note was added inside the block recording that any commit here blocks in-chat model switching until the gateway restarts. The ruling is unchanged; only the size is."
---

# Prepend the estate point-up block to the upstream-tracked `AGENTS.md`

## Context

Phase 4 of the `~/ai` rebuild introduces a doc spine in which `AGENTS.md` is the
canonical agent entry point at every level, opening with a fixed four-line
"point up" block of absolute paths:

```markdown
> Estate map: /Users/mh/ai/README.md
> Cross-repo decisions: /Users/mh/ai/DECISIONS.md
> Status + what's next: /Users/mh/ai/way_of_working_multiple_ai/STATUS.md
> This repo's decisions: /Users/mh/ai/<repo>/docs/INDEX.md
```

Absolute, so it resolves from any cwd and for any agent — Claude, Codex or
otherwise. `docs_check.py` asserts the block exists and that every target
resolves, so the upward link cannot rot silently.

Nine of the ten repos in the estate had no `AGENTS.md` at all, so for them this
is a new file and costs nothing.

`hermes-agent` is the exception, and it is awkward:

- Its `AGENTS.md` already exists, is **71 KB**, and is **upstream-tracked** —
  it comes from `NousResearch/hermes-agent`, not from this fork.
- `FORK_POLICY.md` policy 1 pins the fork and permits upstream pulls only for
  security fixes or a specifically requested feature.
- Prepending to the top of a large upstream file guarantees a conflict at that
  exact spot on every future sync.

## Options considered

**A. Leave `hermes-agent` out of the spine.** Zero divergence. But the repo
becomes a blind spot: an agent starting a session in `~/ai/hermes-agent` — the
largest repo in the estate, ~19,000 non-vendor files — gets no route upward to
the estate map or the decision ledger, and `docs_check.py` cannot assert a link
that does not exist. The spine's whole value is that it is checkable; one
opted-out repo makes "0 missing" meaningless.

**B. Put the block in a separate file** (`ESTATE.md`, or a `docs/` file). No
conflict, but it is not where any agent looks. `AGENTS.md` is the file the
convention names, and a pointer nobody reads is not a pointer.

**C. Prepend to `AGENTS.md` and accept the conflict.** Chosen.

## Decision

Prepend the block, and make the resulting conflict mechanical rather than a
judgement call by marking where the divergence ends:

```html
<!-- END ESTATE POINT-UP BLOCK — upstream content follows -->
```

**Standing resolution rule on any conflicting sync: take "ours" above the
marker, "theirs" below it.**

Total divergence: 13 lines at first, 25 as of 2026-08-19, at a fixed location and
self-documenting. **Resolve by the marker, never by line count** — the block is
expected to grow as estate-local operational notes accumulate.

## Consequences

- Every future upstream sync that touches `AGENTS.md` conflicts at the top. The
  resolution is mechanical and takes seconds.
- **The failure mode to guard against is resolving by dropping the block.** That
  un-links the fork from the estate and nothing fails loudly at the time — the
  next `make check` is what catches it. Hence the explicit rule in
  `FORK_POLICY.md` rather than only this ADR.
- If upstream ever adds content above its first `#` heading, that content merges
  *below* the marker.
- The divergence is now on `FORK_POLICY.md`'s permitted-divergence list, which is
  the measure of how cheap leaving this fork would be. It grew by 13 lines of
  documentation and no code.

## Pleasingly self-demonstrating

This ADR is itself reachable from `~/ai/DECISIONS.md` only because the block it
argues for exists. The decision demonstrates its own value.
