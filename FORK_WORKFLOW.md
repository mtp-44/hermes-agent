# Fork Workflow

This repo is an upstream-powered fork:

- `reference` = upstream NousResearch repo
- `origin` = my fork
- `main` = my stable branch

The goal is simple:

1. Keep getting upstream Hermes updates from `reference/main`
2. Keep my custom layer stable
3. Reduce memory work and ad hoc git decisions
4. Prefer routines and automation over “remembering the process”

## Working Rules

- Do not develop directly on `main`
- Do not rebase long-lived fork history onto upstream
- Do not mix upstream sync work with unrelated feature work
- Prefer merge-based upstream syncs
- Keep custom features in isolated files/modules/plugins/scripts when possible
- Treat `gateway/run.py`, `run_agent.py`, and other large upstream core files as high-conflict files

## Branch Model

- `main`
  - Stable branch on my fork
  - Always represents the version I can trust and use
- `sync/reference-YYYY-MM-DD`
  - Temporary branch used only for pulling in `reference/main`
  - Used to resolve conflicts, run tests, and review upstream changes safely
- `feature/...`
  - Small focused branches for my own work

## Golden Path For Upstream Updates

When I want upstream changes from NousResearch:

1. Fetch both remotes
2. Create a short-lived sync branch from `main`
3. Merge `reference/main` into the sync branch
4. Resolve conflicts there
5. Run tests and a quick smoke test
6. Merge the sync branch back into `main`
7. Push `main` to `origin`

This keeps upstream adoption explicit and keeps `main` stable.

## What Belongs In My Fork Layer

Good fork customizations:

- new tools
- new scripts
- new plugins
- formatters and adapters
- config-driven behavior
- narrow hook-based patches

Avoid when possible:

- large edits inside `gateway/run.py`
- large edits inside `run_agent.py`
- spreading one feature across many upstream-owned core files

## Current Risk Map

Lower-risk local ownership:

- `tools/strava_tool.py`
- `scripts/strava_auth.py`
- `plugins/openbrain-query-brain-format/`
- tests for local custom behavior

Higher-risk merge hotspots:

- `gateway/run.py`
- `run_agent.py`
- `gateway/platforms/base.py`
- `gateway/stream_consumer.py`
- `hermes_cli/commands.py`

If a feature touches a hotspot, prefer extracting logic into a local module and leaving only a thin call site in the upstream file.

## Automatic Habits To Enable Once

These reduce repeated manual conflict work:

```bash
git config rerere.enabled true
git config fetch.prune true
```

`rerere` is especially important for a long-lived fork because repeated conflicts often resolve the same way.

## Pulling Policy

Do not use plain `git pull` as the main maintenance workflow for upstream updates.

Why:

- `git pull` only updates the current branch from its tracked remote
- it does not safely stage upstream sync work from `reference/main`
- it encourages mixing sync and feature work

Instead, upstream updates should happen through a single repeatable sync routine.

## Desired Automation

The long-term goal is:

- one command for “sync upstream into my fork safely”
- automatic branch naming
- automatic fetch from `origin` and `reference`
- automatic merge of `reference/main` into a sync branch
- automatic test run
- clear pause only when human conflict resolution is required

Documentation alone does not provide that automation. This file defines the process the automation must follow.

## Definition Of Done For Future Automation

The automated sync flow should:

1. fetch `origin` and `reference`
2. verify the worktree is clean or stop safely
3. create `sync/reference-YYYY-MM-DD`
4. merge `reference/main`
5. stop on conflicts with clear instructions
6. run the standard test command if merge succeeds
7. report next steps to merge back into `main`

## Day-To-Day Usage

- Start feature work from `main`
- Create `feature/...`
- Keep custom behavior isolated where possible
- Merge finished feature branches back into `main`
- Use sync branches only for upstream adoption

## Maintenance Rule

Any time a local customization grows inside an upstream hotspot, prefer:

1. extract to a local module
2. keep a thin integration seam in the upstream file
3. add tests for the local behavior

This is the main rule that keeps future upstream merges cheap.

## Repo Command

Use the repo-local helper:

```bash
scripts/sync_reference.sh
```

Useful variants:

```bash
scripts/sync_reference.sh --yes
scripts/sync_reference.sh --no-tests
scripts/sync_reference.sh --branch sync/reference-manual-check
```

This command is the default upstream sync path for this fork.
