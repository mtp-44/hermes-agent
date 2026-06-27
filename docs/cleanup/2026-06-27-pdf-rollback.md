# 2026-06-27 — Roll back Phase 8.1 PDF extraction (perf)

## What & why

Reverted today's two PDF commits because they made every turn slow:

- `1df853da5` fix: read_file extracts PDF text instead of returning gibberish
- `161bd917b` feat: layout-aware PDF extraction via pdfplumber (Phase 8.1)

`read_file` was inlining the **entire** extracted document (text + every table) into the
live conversation prompt. On the local model (`qwen3.5:35b-a3b` via ollama, prefill
~200 tok/s) a single spending PDF pushed prompts to 144–205K tokens → 10–20 min latency
and request-timeout/500 retry storms. The prompt is re-read every turn, so one PDF
poisoned the whole session.

**Decision (mark):** always-fast responses beat once-or-twice-a-week PDF analysis.

## Changes

- Revert commits on `main`: `5d6735fb6`, `2784adb09` (revert of `161bd917b`, `1df853da5`).
  Clean reverts, no conflicts. `.pdf` is back in `tools/binary_extensions.py` exclude set;
  `pdfplumber` no longer referenced in `tools/`.
- Original work preserved on branch **`pdf-extraction-phase8.1`** (tip `161bd917b`).
- `pdfplumber`/`pypdf` left installed in `~/.hermes/venv` (harmless when unused; no prompt cost).
- Out-of-repo: `~/.hermes/config.yaml` `context_file_max_chars: null → 4000` (stops the 69K
  dev `AGENTS.md` loading in full every turn). Gateway restarted via launchd
  `ai.hermes.gateway` (new pid 174).

These commits are local on `main`, **not pushed**.

## Re-enable later (only after capture→retrieve)

```bash
git cherry-pick 1df853da5 161bd917b   # or: git revert 5d6735fb6 2784adb09
```

The proper Phase 8.1 must route extraction through **capture → retrieve** (chunk + store,
answered via `query_brain`) so a document adds ~O(1) tokens to live context, not O(document).
Full analysis: `open_brain/docs/perf_hermes_prompt_budget_2026-06-27.md`.

## Follow-up same day: background skill-review disabled (GPU contention)

The `background_review.py` self-improvement loop forks a second agent that replays the whole
conversation (100K+ ctx) on the same single local GPU after sessions, starving interactive
chat. ROI was poor (67 skills tracked, 80% never used). **Disabled via config** (no code change):

- `~/.hermes/config.yaml` `skills.creation_nudge_interval: 0` (gate: `agent/turn_finalizer.py:377`,
  source: `agent/agent_init.py:1213`; re-enable = `10`).
- `~/.hermes/config.yaml` `agent.gateway_auto_continue_freshness: 0` (stop heavy interrupted
  sessions auto-resurrecting on restart).

The lighter `memory.nudge_interval` review (feeds open_brain memory) was left on. Rule going
forward: any autonomous/background model pass must be idle-gated or off by default on this
single-GPU host. See `open_brain/docs/perf_hermes_prompt_budget_2026-06-27.md` §"Follow-up".
