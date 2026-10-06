---
id: HA-0015
date: 2026-10-06
repo: hermes-agent
status: active
tags: [security, upstream-sync, fork-policy, upstream-digest, tooling]
verdict: "**`scripts/upstream_digest.py` now classifies by subject vocabulary and body advisories, not only type and scope. The honest backlog is 132 undecided upstream security commits, not 0.** Until now a fix under an ordinary scope was invisible: `fix(agent): … (ReDoS)` sat unreported for two weeks (HA-0013). Two rules were added. (1) Behaviour-changing commits whose subject names a security idea count: ReDoS, TOCTOU, SSRF, traversal, redaction, a secret or env leak, a guard bypass, owner-only or world-readable, spoofing, exfiltration. A short exclusion list drops catalog pins and resource leaks. (2) A GHSA or CVE id anywhere in the body counts. Measured on `main..reference/main` (35,074 commits): every commit the old rule caught is still caught (182 of 182); 324 are classified now; 8 of the 9 known misses are caught; about three in four new hits are genuinely security-relevant; about 40 a month. 10 of the new hits were already ported by hand (landed 77 → 87), which confirms the rule finds real fixes. Undecided goes from 0 to 132, and the estate reviewer's upstream-security finding will show that until it is triaged. Path-based matching was measured and rejected: 257 hits at about 40% precision, and no recall the subject rule lacks on the known set."
---

# Make the upstream digest see security fixes filed under ordinary scopes

## Context

`HA-0006` (2026-09-24) widened the digest from the `security` type alone to
security scopes and advisory ids. The 2026-10-06 fork-vs-upstream inventory
(`/Users/mh/ai/notes/hermes-assessment-2026-10-06/INVENTORY.md`) showed the
remaining hole. Upstream files many security fixes as `fix(agent)`,
`fix(tools)`, `fix(mcp)` or `fix(delegation)`, and the digest reads only type and scope.
One such fix, `70f79c8cde`, was live on the fork until `HA-0013` ported it.
So "0 undecided" meant "0 undecided among commits *labelled* security".

## What was measured

The candidate rules were run over all 35,074 commits in
`main..reference/main`. Precision was judged by sampling 25–40 hits per rule,
and recall against nine upstream fixes the inventory had found by hand.

| Rule | New hits | Precision (sampled) | Known misses caught |
|---|---|---|---|
| Single subject keywords ("inject", "bypass", "sandbox", "spoof", "leak", …) | hundreds | low: catalog pins, UI escapes, resource leaks | — |
| The same keywords in the body | 100–440 per term | lower | — |
| Fix commits touching security-gate files | 257 | about 40% | 4 / 9 |
| **Curated subject vocabulary** (adopted) | 123 | **about 75%** | **8 / 9** |
| **GHSA/CVE in the body** (adopted) | 20 | high | — |

The one miss left is `1aadb02eaf`, "stop mixed platform bundles from
re-exposing blocked tools". Its subject names no security idea, and no rule
short of reading every commit would find it.

## The rule

A commit counts as security when any of these holds:

1. **Type `security`/`sec`, or a security scope** (unchanged).
2. **An advisory id in the subject** (unchanged).
3. **An advisory id in the body** (new), unless the type changes no behaviour (test, docs).
4. **A behaviour-changing type plus a security-vocabulary subject** (new).
   - The types are fix, feat, perf and harden*.
   - `perf` is included because ReDoS fixes are often filed that way.
   - The vocabulary is `SECURITY_VOCAB_RE`, minus `NOT_SECURITY_RE`.

The log format now carries the body between `\x1f` and `\x1e`, so the body's
own newlines can't be read as file names. Runtime is unchanged: about 3 s for
35k commits.

## Verification

- `tests/scripts/test_upstream_digest.py`: 65 passed.
  - Real upstream subjects are pinned both ways: caught, and look-alikes not caught.
  - Body-advisory cases are covered.
  - The JSON shape the estate reviewer consumes is unchanged.
- Old rule ⊆ new rule on the real history: 0 of 182 dropped.
- `--json` against `origin/main`: security 132, landed 87, triaged 105.

## Consequences

- **The estate reviewer will show 132 undecided.** That is the real backlog, not a regression. Triage it in batches; every triaged line needs a reason, as before.
- **Expect about 40 security-candidate commits a month** from now on, up from the handful the old filter showed. That is a running cost of the pinned fork, and it belongs in the fork-versus-upstream decision (`SUGGESTIONS.md`, `INVENTORY.md`).
- **This is a net, not a guarantee.** A fix whose subject names no security idea still gets through. The vocabulary should grow when a new miss is found, with the subject pinned in the tests, as the HA-0013 case now is.
