# Hardened AI Stack Phase -1

This runbook turns the Phase `-1` validation items from `/Users/mh/ai/hardened-ai-stack-implementation-plan.md` into repeatable checks.

## Current Status

As of `2026-05-18`, the validation layer is in place and the first gateway capture controls have moved beyond probe-only status:

- Hermes surface probe exists and runs offline.
- Session hook probe exists and records real payload shape.
- Hosted Openbrain MCP smoke test exists with authenticated, unauthenticated, invalid-input, and optional live-write coverage.
- Hermes gateway now implements `/nosave`, `/private`, `/capture-status`, `/note`, `/m`, `/claude`, and `/opus`.
- Session-end summary capture is wired for reset, idle expiry, and shutdown, and respects `/nosave` and `/private`.

## Hermes Surface Probe

Offline probe for currently implemented Hermes extension points:

```bash
uv run python /Users/mh/ai/agents/hermes-agent/scripts/hardened_phase_minus1_probe.py --pretty
```

What it reports:

- documented `~/.hermes/skills/` location
- implemented slash-route commands such as `/local`, `/fast`, `/5.5`, `/usage`, `/save`
- implemented capture-control commands such as `/nosave`, `/private`, `/capture-status`, `/note`, `/m`
- implemented Claude route commands such as `/claude` and `/opus`
- supported hook events including `on_session_end` and `on_session_finalize`
- scheduler module locations
- gateway route defaults currently wired in code

## Claude Code / Stop-Hook Probe

Diagnostic recorder for stdin, cwd, argv, env vars, and likely payload files:

```bash
python /Users/mh/ai/agents/hermes-agent/scripts/session_hook_probe.py --label stop-hook
```

To use it as a real hook, point the hook command to this script and inspect the generated JSON file path from stdout.

Default output directory:

- `~/.hermes/hook-probes/`

Safety notes:

- secret-like env var values are redacted
- stdin is recorded only as byte count plus a capped UTF-8 preview

## Hosted Openbrain MCP Probe

Safer live contract check for the hosted MCP endpoint:

```bash
cd /Users/mh/ai/open_brain
uv run python scripts/hosted_mcp_smoke.py
```

Default checks:

- unauthenticated `tools/list` fails with `401 Unauthorized`
- authenticated `tools/list` succeeds
- required tools are exposed
- `query_brain` returns the expected response fields
- invalid tool input fails in a controlled way

Optional live write check:

```bash
cd /Users/mh/ai/open_brain
uv run python scripts/hosted_mcp_smoke.py --write-test
```

This performs a real `capture_thought` call and verifies the saved record can be found again with `query_brain`.

## Verification Snapshot

Commands used during this implementation slice:

```bash
uv run python /Users/mh/ai/agents/hermes-agent/scripts/hardened_phase_minus1_probe.py --pretty
uv run pytest /Users/mh/ai/agents/hermes-agent/tests/gateway/test_capture_commands.py /Users/mh/ai/agents/hermes-agent/tests/gateway/test_session_boundary_hooks.py

cd /Users/mh/ai/open_brain
uv run pytest /Users/mh/ai/open_brain/tests/test_hosted_mcp_smoke.py
uv run python /Users/mh/ai/open_brain/scripts/hosted_mcp_smoke.py
```

Observed results:

- gateway capture-control and session-boundary tests passed
- Openbrain hosted MCP smoke tests passed
- hosted MCP live probe confirmed authenticated success and predictable `401 Unauthorized` behavior
- the offline probe now reports no planned slash-command gaps
