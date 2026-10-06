---
id: HA-0014
date: 2026-10-06
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, dashboard, approvals]
verdict: "**Port one of the five upstream security commits dated 2026-10-05 and triage four, as `sec/p6-2026-10-06`; the digest is back at 0 undecided (77 landed, 105 triaged).** Ported by hand: `fb5bba80dc`. The dashboard keyed its password-login throttle and auth audit on a client-supplied `X-Forwarded-For`, and the fork has that helper in three copies. It applies here because `:9119` binds the tailnet address with no proxy in front. Triaged: `2b525ced84` (a `-q` approval hang the fork cannot reach, because it registers the CLI callback only in the interactive `run()`), `ef1faa4cf8` and its follow-up `762f419fe8` (classic-CLI consent prompts that fail closed today; usability, not security), and `0df1837e81` (observer hooks on those prompts, with no observer enabled). Gates pass. The live gateway and dashboard pick up the port at their next restart."
---

# Port the dashboard XFF fix; triage four approval-prompt fixes

## Context

On 2026-10-06 the digest listed five upstream security commits dated
2026-10-05, all new since `HA-0012`. Under `FORK_POLICY.md` policy 1, each
is either ported or triaged with a reason.

| Upstream | Subject | Decision |
|---|---|---|
| `fb5bba80dc` | fix(security): ignore spoofed XFF for dashboard login limits | **Ported** |
| `2b525ced84` | fix(approval): protected-instruction gate hangs in `hermes chat -q` sessions | Triaged: not applicable |
| `ef1faa4cf8` | fix(approval): classic-CLI MCP/vault consent prompts reach the approval panel | Triaged: fails closed today |
| `762f419fe8` | fix(approval): MCP/vault consent declines at once when no user can answer | Triaged: follow-up to the above |
| `0df1837e81` | fix(approval): fire observer hooks for the two hookless classic-CLI prompts | Triaged: observability, no observer enabled |

## What landed

`182b82bf27` ports `fb5bba80dc` by hand.

- **The bug.** `_client_ip` took the first `X-Forwarded-For` hop. A direct client could rotate that header to get a fresh password-login rate-limit bucket on every attempt, and write any address it liked into the auth audit.
- **The fix.** All three copies (`dashboard_auth/routes.py`, `middleware.py`, `token_auth.py`) now return the ASGI peer. Upstream consolidated these into `request_utils.client_ip`.
- **Behind the PWA's proxy.** Uvicorn's `proxy_headers` still rewrites `request.client` for trusted peers. The default trusted peer is loopback, which is how `tailscale serve` reaches the PWA on `:9219`, so tailnet clients there keep distinct addresses.
- **Tests.** Upstream's two tests are taken, the `client_ip` one parametrised over the three modules. 7 fail before the fix, 26 pass after.

### Exposure here

- **Low.** `:9119` listens on `100.93.233.4` directly, so any tailnet peer is a direct client.
- **No password provider is configured.** `dashboard.basic_auth` is empty, so today the spoof could only misattribute audit lines.
- **The throttle becomes real as soon as a password login is configured.**

## The triage, with the facts it rests on

- **`2b525ced84`.**
  - **Upstream bug:** upstream registers the CLI approval callback in `HermesCLI.__init__`. A one-shot `-q` run therefore looks interactive, and the protected-instruction gate waits on a modal nothing renders.
  - **Why the fork is unaffected:** the fork registers the callback only in `_install_tool_callbacks()`, which only the interactive `run()` calls (`cli.py:13091`). Kanban workers are spawned as `-q` runs, and kanban dispatches in the gateway here; in the fork they already fail closed at once, with the honest reason.
- **`ef1faa4cf8` and `762f419fe8`.**
  - **Fails closed today:** without the panel callback, the classic-CLI branch of `request_elicitation_consent` hits `prompt_dangerous_approval`'s prompt_toolkit guard and returns `deny`.
  - **What the fix changes:** it makes the prompt answerable, which is usability, not security.
  - **The follow-up only undoes a side effect:** `762f419fe8` fixes a `-q` wait that `ef1faa4cf8` itself introduced.
  - **Surfaces:** `mcp-trust` and `vault-payment` don't exist in the fork. Mark's approvals run through the gateway branch, which neither commit touches.
- **`0df1837e81`.** It adds observer hooks around those CLI prompts. The only in-tree observer, `observability/nemo_relay`, is not in `plugins.enabled`, and no user plugin registers `pre_approval_request` or `post_approval_response`.

Each triage line in `scripts/upstream_digest_triaged.txt` names what would make it worth re-evaluating.

## Verification

- `tests/hermes_cli/test_dashboard_auth_password_login.py`: 26 passed (7 failed before the fix).
- `tests/hermes_cli/ -k "dashboard_auth or web_server or pwa"`: 776 passed.
- Policy-1 sync gate: see the PR.
- `scripts/upstream_digest.py --no-fetch --local-branch sec/p6-2026-10-06`: undecided 0, landed 77, triaged 105.

## Consequences

- **Pick up the port:** restart the gateway and the dashboard after the merge. The dashboard is `com.mh.hermes-dashboard`; leave its plist alone and restart it the usual way.
- **Still open:** the digest gap from `HA-0013`. Fixes filed under non-security scopes are still invisible to the filter.
