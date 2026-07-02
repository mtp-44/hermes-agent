# Handoff: Upstream Security Sync — resume here

> **Living handoff, not disposable.** Unlike `docs/cleanup/*.md`, keep this
> file until the "Next up" work below is actually done, then delete it (or
> fold anything still relevant into `docs/hermes-update-runbook.md`).

## TL;DR for a fresh session

The fork was ~1,113 commits behind `reference/main` (NousResearch/hermes-agent,
last full sync 2026-06-26). Reviewing/merging all of it in one sitting isn't
tractable at upstream's pace (~140 commits/day). On 2026-07-02 we did a
**security-only** cherry-pick sync instead of a full merge, landed it, and
restarted the gateway. Full non-security merge is still outstanding.

Read `docs/hermes-update-runbook.md` first — it's the standing process this
handoff builds on. This file is just "what's already done, what's left, and
what tripped us up."

## What's done (2026-07-02)

- [PR #4](https://github.com/mtp-44/hermes-agent/pull/4) merged into `main`
  (commit `39ffabee0`, plus a follow-up docs commit `e2465f57c`). CI green.
- 12 of 15 `security(...)`-flagged upstream commits landed, cherry-picked
  with their prerequisite/adaptation commits. Full list and rationale in
  `docs/cleanup/2026-07-02-security-sync.md`. Headlines:
  - `/resume` + `/sessions` IDOR fix (cross-user/cross-platform session
    hijack via title/id) — the core vulnerability is closed.
  - Browser SSRF/private-network hardening (cloud-metadata floor, CDP
    treated as non-local, private-page-action guard re-checked after
    `browser_back`/eval nav).
  - `aiohttp` 3.14.0 / `anthropic` 0.87.0 / `cryptography` floor, enforced
    on every install path (eager + lazy).
  - Cron scheduler fail-closed + blocks `base_url` credential-exfil
    overrides (relevant to our own 2026-06-26 stale-port incident).
  - Slack `xapp-` token redaction.
- Gateway restarted; `hermes_update_guard.py --post --live-smoke`,
  `openbrain_conformance_smoke.py`, and the full `run_tests_parallel.py`
  suite (vs. a `main` baseline) all green. Health monitor reports all 7
  services (incl. the new `config` check) healthy post-restart.
- New tool: `scripts/upstream_digest.py` — run this at the *start* of the
  next sync session instead of manually grepping `git log`. It fetches
  `reference` and reports `security(...)` commits + commits touching
  `LOCAL_DELTA_PATHS` (our load-bearing files); everything else is a bulk
  count by conventional-commit type.

## Next up (not started)

**1. The 3 deferred `/resume` hardening commits**, in order:
   `5248877c6` → `599a6391d` → `f1e58d8c1` → `5b3f06425`
   (chat/thread-origin proof, DM scoping tightening, shared-group fallback,
   `user_id_alt` keying). These depend on a `sessions` table schema change
   (`chat_id`/`thread_id` columns + `ON CONFLICT` upsert enrichment in
   `hermes_state.py`'s session-insert helper) that lives in earlier,
   non-security upstream commits we haven't merged. To take these:
   - Find the schema-introducing commit(s) upstream (walk
     `git log reference/main -- hermes_state.py` around the same date range
     as `5248877c6`).
   - Cherry-pick the schema commit(s) first, verify `hermes_state.py`
     migrations/tests pass, *then* the 4 `/resume` commits should apply
     cleanly (they didn't need the async/i18n workarounds this sync needed
     for the base IDOR fix, since by then upstream's own test file is
     already consistent).
   - This is a real schema change touching the live production SQLite DB —
     treat it with the same care as a migration (backup, verify rollback).

**2. The full non-security merge** (~1,098 remaining commits as of
   2026-07-02, growing). Strategy:
   - Run `scripts/upstream_digest.py` fresh to see current drift and any
     new security commits since this sync.
   - Do NOT try to review 1,000+ commits by hand. Merge in batches (e.g. by
     date range or by subsystem) on a dated `sync/` branch, resolve
     conflicts per the Update Rules in the runbook (never blanket
     "keep-ours" — reapply local deltas where upstream moved code), and
     lean on CI + `run_tests_parallel.py` vs. a `main` baseline to catch
     regressions rather than trying to reason about every commit.
   - Expect the same failure modes we hit this sync: upstream commits that
     assume a later refactor (async facades, schema changes) not yet
     merged. When a cherry-pick/merge references a symbol/table/async
     interface that doesn't exist in our tree, look for the actual
     defining/introducing commit and pull it in first rather than
     papering over it.

**3. Consider scheduling `upstream_digest.py`** (e.g. via Hermes's own
   scheduled-tasks) to run every 1–2 days and report via Telegram, so drift
   is visible continuously instead of rediscovered cold at each sync.

## Gotchas hit this sync (don't repeat)

- **`uv sync` in a scratch worktree can hijack the global `hermes` CLI
  symlink.** It repointed `/Users/mh/.local/bin/hermes` at the scratch
  worktree's venv instead of production. Caught by
  `hermes_update_guard.py --pre`'s `launchagents-targets` check. Always
  `readlink /Users/mh/.local/bin/hermes` and confirm it points at
  `/Users/mh/ai/agents/hermes-agent/.venv/bin/hermes` before restarting the
  gateway, if you did any `uv sync` in a disposable worktree along the way.
- **Production `uv sync` needs `--extra messaging`** alongside
  `--extra all --extra dev`, or it prunes `python-telegram-bot[webhooks]`
  (lazy-installed, deliberately excluded from `[all]`). Same class of
  incident as 2026-06-26 — this time avoided proactively by checking
  `pyproject.toml` extras before running the sync command in production.
- **Never do dependency-install diffs in the live production repo dir** —
  always a disposable worktree (`git worktree add --detach /tmp/... HEAD`),
  per the lesson already in `docs/hermes-update-runbook.md`.
- **When a cherry-picked commit's own tests fail with an unawaited
  coroutine / missing symbol / TypeError**, don't assume it's a bug in
  upstream — check whether a *later* commit in the same upstream lineage
  fixes it (often bundled with an unrelated schema/refactor change you
  haven't merged yet). Search `git log reference/main -S '<symbol>' --
  <file>` to find the real defining commit before patching around it.
- **This repo enforces strict i18n key/placeholder parity across all 16
  locale catalogs** (`tests/agent/test_i18n.py`). Any new
  `t("some.new.key", ...)` call needs the key added to every
  `locales/*.yaml`, not just `en.yaml` — upstream itself sometimes only
  updates `en.yaml` in the security-fix commit itself, so check this
  whenever cherry-picking a commit that touches `locales/`.
- **Local plugins can silently break unrelated tests the first time they
  hit real CI.** The `routing-classifier` plugin (`e555765a4`, unrelated to
  this sync) had never run through CI before this session — it broke 4
  Discord-skill-command tests that didn't isolate the plugin-command tier.
  If a PR's CI failure looks unrelated to your diff, check whether it's
  actually exposing a latent bug in something that was merged directly to
  `main` without ever going through CI.

## Reference

- Standing process: `docs/hermes-update-runbook.md`
- This sync's detailed notes: `docs/cleanup/2026-07-02-security-sync.md`
- Prior sync notes: `docs/cleanup/2026-06-26-upstream-sync.md`,
  `docs/cleanup/2026-06-19-after-upstream-sync.md`
- Triage tool: `scripts/upstream_digest.py`
- Rollback tag from this sync: `archive/pre-hermes-update-20260702-074406`
