<!-- GENERATED FILE — do not edit by hand.
     Source: /Users/mh/ai.new/hermes-agent/docs/
     Regenerate: uv run /Users/mh/ai/bootstrap/scripts/gen_docs_index.py -->

# `hermes-agent` — doc index

The chat gateway. Fork of NousResearch/hermes-agent. Serves the desktop client, the tailnet PWA, Telegram and Signal. FORK_POLICY.md governs divergence from upstream.

- Repo entry point: [`../AGENTS.md`](../AGENTS.md)
- Estate map: [`/Users/mh/ai/README.md`](/Users/mh/ai/README.md)
- Cross-repo decisions: [`/Users/mh/ai/DECISIONS.md`](/Users/mh/ai/DECISIONS.md)

## Live services

- `ai.hermes.gateway` (always on)
- `com.mh.hermes-pwa` (always on) — https://mini-mh.tailbd0650.ts.net
- `com.mh.hermes-dashboard` (always on)
- `com.mh.hermes-health-monitor` (every 300s)
- `com.mh.hermes-signal-cli` (always on)

## Decisions

| | ID | Date | Verdict |
|---|---|---|---|
| ✅ | [`HA-0001`](decisions/0001-agents-md-fork-divergence.md) | 2026-07-29 | Accept a permanent 13-line divergence at the top of the upstream-tracked AGENTS.md so the fork is visible to the estate doc spine; resolve it 'ours' above the END ESTATE POINT-UP BLOCK marker on every sync. |
| ✅ | [`HA-0002`](decisions/0002-security-sync-2026-07-22-merge.md) | 2026-07-30 | Merge PR #7 sync/security-2026-07-22 (credential-header stripping on redirects, dashboard .env/credential-store guards, aiohttp/raft body-size caps, CI ref-injection fix) into the live branch fix/session-capture-signal-gate; sync gate passed, zero test regressions across the full suite, all three claimed fixes exercised and confirmed, deployed to all three live services same day. |

## Documents

- [Fork Policy — Pinned Appliance (2026-07-02)](FORK_POLICY.md)
- [Chronos managed-cron — agent ↔ NAS wire contract](chronos-managed-cron-contract.md)
- [Cleanup After Upstream Sync - 2026-06-19](cleanup/2026-06-19-after-upstream-sync.md)
- [Upstream Sync Checkpoint — 2026-06-26](cleanup/2026-06-26-upstream-sync.md)
- [2026-06-27 — Roll back Phase 8.1 PDF extraction (perf)](cleanup/2026-06-27-pdf-rollback.md)
- [2026-06-28 — Telegram stuck-typing fix + flood-control tuning](cleanup/2026-06-28-telegram-typing-and-flood.md)
- [2026-07-01 — branch pruning across hermes-agent + open_brain](cleanup/2026-07-01-branch-pruning.md)
- [Full Non-Security Upstream Merge Checkpoint — 2026-07-02](cleanup/2026-07-02-full-merge.md)
- [/resume Hardening Checkpoint — 2026-07-02](cleanup/2026-07-02-resume-hardening.md)
- [Upstream Security Sync Checkpoint — 2026-07-02](cleanup/2026-07-02-security-sync.md)
- [2026-07-22 security-only upstream sync — notes + cleanup checklist](cleanup/2026-07-22-security-sync.md)
- [Profile Builder — Dashboard-Native, Full-Featured Profile Creation](design/profile-builder.md)
- [Handoff: Upstream Security Sync — resume here](handoff-upstream-security-sync-2026-07-02.md)
- [Hermes Update Runbook](hermes-update-runbook.md)
- [Multi-gateway deployment](kanban/multi-gateway.md)
- [Hermes Middleware](middleware/README.md)
- [model-gate: routing stall-shaped work away from the local model](model-gate-plugin.md)
- [Hermes Observer Hooks](observability/README.md)
- [Hermes Extension Seam Design (Phase 5c.2)](openbrain-hermes-seam-design.md)
- [Open Brain ↔ Hermes Touchpoint Inventory (Phase 5c.1)](openbrain-hermes-touchpoint-inventory.md)
- [Hermes ↔ Open Brain Session Capture Status](openbrain-session-capture-status.md)
- [fix: Prevent Telegram streamed replies from ending after first overflow chunk](plans/2026-06-09-003-fix-telegram-stream-overflow-continuations-plan.md)
- [RCA: Sprint-report recall false negative (2026-07-07)](rca-sprint-recall-false-negative-2026-07-07.md)
- [RCA: SSL CA cert bundle corruption after `hermes update`](rca-ssl-cacert-post-git-pull.md)
- [Relay ↔ Connector Contract (v1, EXPERIMENTAL)](relay-connector-contract.md)
- [Network Egress Isolation for Docker Deployments](security/network-egress-isolation.md)
- [Session Lifecycle](session-lifecycle.md)
- [Unified Retrieval Architecture](unified-retrieval-architecture.md)

