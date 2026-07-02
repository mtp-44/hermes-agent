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
