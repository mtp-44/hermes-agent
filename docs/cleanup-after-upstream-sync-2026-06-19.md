# Cleanup After Upstream Sync - 2026-06-19

Current safe point:

- `main` is at merge commit `7c074cb3b` from PR #1.
- CI passed before merge.
- The live gateway is running from `main`.
- Leave this cleanup until the Telegram smoke check has had at least one real inbound user message after the merge.

## Delete After Smoke Check

These are merge/replay leftovers and can go once `main` has run cleanly for a day:

- Local branch `sync/unified-retrieval-main-2026-06-19`
- Local branch `sync/unified-retrieval-main-replay-2026-06-19`
- Remote branch `origin/sync/unified-retrieval-main-replay-2026-06-19`

Suggested commands:

```bash
git branch -d sync/unified-retrieval-main-2026-06-19
git branch -d sync/unified-retrieval-main-replay-2026-06-19
git push origin --delete sync/unified-retrieval-main-replay-2026-06-19
```

## Delete After Claude Confirms Done

These are attached worktrees from the interrupted Claude run. Remove them only after confirming no Claude session needs to resume them:

- `.claude/worktrees/compassionate-wing-1b1f94`
- `.claude/worktrees/cool-nightingale-0ed87b`
- `.claude/worktrees/eloquent-nash-ed0621`
- Their matching local branches:
  - `claude/compassionate-wing-1b1f94`
  - `claude/cool-nightingale-0ed87b`
  - `claude/eloquent-nash-ed0621`

Suggested commands:

```bash
git worktree remove .claude/worktrees/compassionate-wing-1b1f94
git worktree remove .claude/worktrees/cool-nightingale-0ed87b
git worktree remove .claude/worktrees/eloquent-nash-ed0621
git branch -d claude/compassionate-wing-1b1f94
git branch -d claude/cool-nightingale-0ed87b
git branch -d claude/eloquent-nash-ed0621
```

## Keep For Now

- `main`
- `unified-retrieval-start`, until you are comfortable that no local scripts or notes still name it as the production branch.
- `origin/unified-retrieval-start`, for the same reason.
- `backup/pre-pull-main-2026-05-01`
- `push-doc-plan`, unless you recognize it as stale.
- `~/.hermes`, launchd plists, logs, and runtime state.
- The `reference/*` remote-tracking branches; they are the upstream mirror/reference namespace, not merge leftovers.

Before deleting `unified-retrieval-start`, make a final tag if you want an easy rollback label:

```bash
git tag archive/unified-retrieval-start-2026-06-19 unified-retrieval-start
git push origin archive/unified-retrieval-start-2026-06-19
```
