# Hermes Update Runbook

This runbook is the required update path for the local production Hermes agent.
It is written for Codex, Claude, and humans. The goal is to make updates
boring: verify the current production state, preserve local Open Brain deltas,
apply the update, run smoke checks, and leave a rollback label.

## Prompt For Claude Or Codex

When starting the next Hermes update cycle, open the agent in
`/Users/mh/ai/agents/hermes-agent` and give it this instruction:

```text
Read docs/hermes-update-runbook.md and follow it exactly for this Hermes update.
Run scripts/hermes_update_guard.py --pre before changing anything.
Do not proceed past any blocking failure.
After the update/restart, run scripts/hermes_update_guard.py --post.
Use scripts/hermes_update_guard.py --post --live-smoke if network access is available.
Do not print secrets.
If you hit a blocker, stop and report the failing check, suspected cause, and safest next step.
```

## Current Production Contract

- Production repo: `/Users/mh/ai/agents/hermes-agent`
- Expected production branch: `main`
- Runtime home: `/Users/mh/.hermes`
- Gateway launch label: `ai.hermes.gateway`
- Hermes is the front door; Open Brain is the durable memory/retrieval backend.
- The hosted Open Brain MCP must expose `query_brain`, `analyze_brain_query`,
  `capture_thought`, feedback, commitments, drift, graph/timeline, and related
  tools.

## Agent Rule

Before any update, run:

```bash
cd /Users/mh/ai/agents/hermes-agent
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --pre
```

If the guard fails, stop and fix the blocker before pulling, merging, rebasing,
or restarting production. Use `--allow-dirty` only when auditing an already
dirty tree, not as permission to update through unreviewed changes.

After any update and gateway restart, run:

```bash
cd /Users/mh/ai/agents/hermes-agent
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post
```

When network access is available, add `--live-smoke`:

```bash
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post --live-smoke
```

For machine-readable output:

```bash
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post --json
```

## Pre-Update Checklist

1. Run the guard with `--pre`.
2. Confirm `main` is the production branch and matches `origin/main`.
3. Confirm the working tree is clean.
4. Confirm local Hermes/Open Brain integration files are present:
   - `agent/session_capture.py`
   - `plugins/memory/openbrain/__init__.py`
   - `plugins/openbrain-query-brain-format/__init__.py`
   - `gateway/open_brain.py`
   - `gateway/open_brain_feedback.py`
   - Telegram feedback and session-boundary hooks
   - Hermes health monitor and LaunchAgents
5. Confirm `~/.hermes/config.yaml` loads and `mcp_servers.open_brain` expands
   its auth header. Never print secret values.
6. Confirm SSL/CA inputs are valid. A partial venv update or stale CA env var
   must be fixed before provider calls.
7. Tag the current production state before risky work:

```bash
git tag archive/pre-hermes-update-$(date +%Y%m%d-%H%M%S) HEAD
```

Push the tag only when you intend to keep it as shared rollback state:

```bash
git push origin <tag-name>
```

## Update Rules

- Prefer small upstream syncs. Do not let the fork drift by thousands of commits.
- Keep `main` as the production branch unless a handoff explicitly says
  otherwise.
- Do not resolve upstream conflicts with blanket "keep ours." Check whether
  upstream moved code into a new mixin/module and reapply the local delta there.
- Treat these local deltas as load-bearing:
  - Open Brain MCP routing and config
  - `/ob` manual capture
  - session-boundary capture
  - Open Brain memory provider
  - query_brain formatter plugin
  - Telegram Open Brain feedback buttons
  - health monitor and LaunchAgents
- Keep the Open Brain hosted MCP as canonical. Do not point Hermes at the local
  experimental Open Brain MCP prototype.

## Post-Update Smoke

After dependencies, merge/replay, and restart:

1. Run `scripts/hermes_update_guard.py --post`.
2. Confirm `health-monitor.jsonl` has fresh healthy rows for:
   - `ollama`
   - `gateway`
   - `telegram`
   - `openbrain`
   - `disk`
   - `memory`
3. Confirm Open Brain exposes at least 30 tools. Current expected live count is
   32 from the gateway health monitor.
4. Send a real Telegram smoke message and confirm the gateway logs an inbound
   message plus a response.
5. Ask a recall question that should call `mcp_open_brain_query_brain`.
6. Ask an analytical question that should call `mcp_open_brain_analyze_brain_query`.
7. Trigger `/ob` or a safe session-boundary capture only when you are prepared
   to write to Open Brain.
8. Check `~/.hermes/logs/gateway.log`, `agent.log`, and `health-monitor.jsonl`.
   Prefer structured health JSONL over stale stderr in `health-monitor.log`.

## Rollback

If the post-update guard fails or Telegram/Open Brain behavior regresses:

1. Stop further cleanup.
2. Preserve logs and the failing SHA.
3. Check out the pre-update tag or recorded SHA.
4. Restart the gateway.
5. Run the post-update guard again.

Example:

```bash
cd /Users/mh/ai/agents/hermes-agent
git checkout <known-good-sha-or-tag>
HERMES_HOME=/Users/mh/.hermes hermes gateway restart
/Users/mh/ai/agents/hermes-agent/.venv/bin/python scripts/hermes_update_guard.py --post
```

Do not force-push `main` unless the explicit goal is to undo the public sync.

## Cleanup Window

Only after production has passed the post-update guard and a real Telegram smoke
for at least one day:

- delete stale `sync/*` update branches
- delete interrupted `claude/*` worktrees/branches after confirming no session
  needs them
- keep rollback tags and `backup/pre-pull-main-2026-05-01`

Cleanup is not part of the update itself. It is a separate, lower-risk task.
