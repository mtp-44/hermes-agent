# 2026-07-01 — branch pruning across hermes-agent + open_brain

Routine housekeeping after the 2026-06-26 upstream sync landed (PRs #2/#3,
see `docs/cleanup/2026-06-26-upstream-sync.md`). Distinct from that file's
still-held **Cleanup Window** — this pass touched unrelated stale branches
only; the sync worktree/branch (`sync/unified-retrieval-main-2026-06-26`) and
its worktree at `/Users/mh/ai/agents/hermes-sync-2026-06-26` were **not**
touched and remain held pending a day of burn-in traffic.

## hermes-agent

Deleted (all fully merged or superseded-by-replay, verified before deletion):

- `feat/pdf-table-extraction` (local) — identical tip commit to
  `pdf-extraction-phase8.1` (`161bd917b`), pure duplicate.
- `fix/pdf-document-extraction` (local) — ancestor of
  `pdf-extraction-phase8.1`, redundant.
- `push-doc-plan` (local) — old planning branch; verified as an ancestor of
  the newly-created `archive/unified-retrieval-start-final` tag before
  force-deleting.
- `origin/sync/unified-retrieval-main-replay-2026-06-19` (remote) — merged
  into `main`; explicit deletion candidate per the 2026-06-19 sync notes.
- `unified-retrieval-start` (local + `origin`) — content was replayed (not
  merged) into `main` back on 2026-06-19 (PR #1), so `git merge-base
  --is-ancestor` reported "not merged" even though nothing was missing.
  Archived first: `git tag archive/unified-retrieval-start-final
  unified-retrieval-start`, pushed to `origin`, then branch deleted
  local+remote.
- `origin/feature/openbrain-session-capture` (remote) — stale design-proposal
  branch from ~2026-05, fully superseded by everything since (unified
  retrieval, session capture, Phase 5c/5d work all landed via other paths).

Kept, deliberately:

- `backup/pre-pull-main-2026-05-01` — explicit "keep" from the 2026-06-19 sync
  cleanup notes.
- `pdf-extraction-phase8.1` — explicit "preserved" per the 2026-06-27 PDF
  rollback notes, in case a future Phase 8.1 rebuild wants to reference it.
- `sync/unified-retrieval-main-2026-06-26` + its worktree — the still-held
  Cleanup Window item; untouched here.

## open_brain

Deleted:

- `rightsizing-eval-2026-06-28` (local) — merged, redundant with `main`.
- `claude/cool-meninsky-7cc0c3` (worktree + branch, under
  `.claude/worktrees/`) — merged, stale since 2026-05-07.
- `claude/trusting-khayyam-c1574a` (worktree + branch) — unmerged since
  2026-05-07, contained one commit: a test mock fix in `tests/test_routing.py`
  patching `core.storage.find_contact_by_name`. Attempted cherry-pick onto
  `main` conflicted; investigation showed the conflict wasn't spurious — the
  code path it patched had since been refactored to resolve contacts via
  `core.storage.load_aliases` instead, and
  `test_pre_parsed_fields_skip_parser_call` already passes on `main` without
  the old mock. The fix is moot, not lost work. Discarded (not applied).

Only `main` remains in both repos (plus hermes-agent's held sync branch).
