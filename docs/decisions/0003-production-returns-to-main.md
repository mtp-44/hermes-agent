---
id: HA-0003
date: 2026-07-31
repo: hermes-agent
status: active
tags: [fork-policy, branching, release]
verdict: "Fast-forward origin/main onto fix/session-capture-signal-gate and run production from main; the branch was 45 ahead / 0 behind so the move was a pure ref advance onto the already-deployed commits, changing no file and redeploying nothing. FORK_POLICY's stated reason for keeping production off main — 'this fork does not track main directly' — did not hold: policy 1 governs upstream pull cadence, and this fork has no upstream remote configured at all."
---

# Production returns to `main`

## Context

Since the pwa9 work, `hermes-agent` production has run from
`fix/session-capture-signal-gate` rather than `main`. By 2026-07-31 that
branch carried 45 commits `main` did not have, and the branch name had long
since stopped describing its contents — it named a session-capture Signal
gate but held the entire pwa9 series, the `~/ai` rebuild fallout and the
estate doc-spine cutover.

Two things made the arrangement worth ending rather than ratifying.

**The stated justification did not hold.** `FORK_POLICY.md` and HA-0002 both
explain production-off-`main` as *"this fork does not track `main`
directly — see policy 1"*. Policy 1 is about **upstream pull cadence**: it
pins the fork at the 2026-07-02 sync and permits pulls only for security
fixes. It says nothing about which local branch is production. The `main` in
question is not upstream's — it is this fork's own branch on
`mtp-44/hermes-agent`, and `git config --get-regexp 'remote\..*\.url'`
returns exactly one remote, `origin`, pointing at the fork. There is no
upstream remote configured. So `main` was not tracking upstream either, and
nothing about being pinned required production to sit on a feature branch.

**It was actively generating false documentation.** The estate's own
`estate.yaml` `branch_note` still described the branch as *13 behind
origin/main, PR #7 an OPEN item* the day after HA-0002 merged PR #7 — and
that stale prose had propagated into the generated root `README.md`. The
2026-07-31 standup caught it only because the generator reads ahead/behind
live. A long-lived production branch whose relationship to `main` has to be
narrated by hand is a standing source of exactly that class of bug.

## What was done

`origin/main` was fast-forwarded onto the branch tip and the working tree
switched to `main`. The branch was 45 ahead / **0 behind**, so this advanced
`main` onto the precise commits already running in production.

- `git merge-base --is-ancestor origin/main HEAD` confirmed the
  fast-forward, re-verified after a `git fetch` rather than taken on trust
  from a stale ref.
- `git push origin fix/session-capture-signal-gate:main` —
  `7d8d5cbee..ba0aba5eb`, a fast-forward, no force. The pre-push hook's
  doc-spine check passed (374 checks).
- Working tree moved to `main` at the same commit. HEAD tree hash before and
  after was identical (`194398766c320ed6650cb9a9607d6dd2fe4109fa`), which is
  the operative safety property: **no file changed, so nothing redeployed.**
  This repo is the running system, and a branch move here is only safe
  because it moved a ref and not a byte.
- All five services confirmed still up on their pre-existing PIDs
  (`ai.hermes.gateway` 32355, `com.mh.hermes-pwa` 32539,
  `com.mh.hermes-dashboard` 32590, `com.mh.hermes-signal-cli` 32513;
  `com.mh.hermes-health-monitor` scheduled, `-` between runs). No
  `launchctl` command was issued.

The deployed release `20260727T105823Z-70be5a5d7989` is unaffected — its
commit is now an ancestor of `main` rather than reachable only from a
feature branch, which was the entire point.

## Consequences

- `origin/HEAD -> origin/main` is true again; a clone gets production.
- The `⚠️ Repos not on main` section of the generated root `README.md` no
  longer lists `hermes-agent`. The estate's `branch_note` for it is retired
  rather than corrected — there is no divergence left to narrate.
- `FORK_POLICY.md`'s sync-history entry for PR #7 keeps its factual account
  but its reasoning is corrected: future security pulls target `main`.
- Policy 1 is untouched. The fork stays pinned, still pulls only for
  security fixes, and is still an interchangeable appliance. This decision
  is about branch topology, not about the fork's relationship to upstream.

## Loose ends

**Merged branches, safe to delete.** `git branch -r --merged origin/main`
confirms `origin/fix/session-capture-signal-gate`, `origin/sync/security-2026-07-22`
and six of the `pwa9/*` branches (`wp0-baseline`, `wp1-green-release-gate`,
`wp2-pwa-contract-tests`, `wp3-bundle-mobile-performance`,
`wp4-atomic-release-reliability`, `wp5-mobile-ux-accessibility`,
`wpc-review-cleanup`) now carry no unique commits. Deleting them is
irreversible on the remote and was deliberately left for Mark rather than
folded into this change.

**Four branches still hold unique commits** and must not be deleted without
being read first — `git branch -r --no-merged origin/main`:

- `origin/pwa9/wp6-durable-inbox`
- `origin/sync/unified-retrieval-main-2026-06-26`
- `origin/claude/adoring-dhawan-bf1fc0`
- `origin/claude/epic-vaughan-8e4cf9`

`wp6-durable-inbox` is the notable one: the pwa9 series is recorded as
closed and live, so a `wp6` branch holding unmerged work is either
superseded-but-unpruned or a genuine gap. It was not investigated here.
This is the estate rule about checking every branch with `--contains` /
`--no-merged` rather than `@{u}..HEAD` earning its keep again.
