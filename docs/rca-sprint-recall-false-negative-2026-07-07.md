# RCA: Sprint-report recall false negative (2026-07-07)

Authored: 2026-07-07

## Incident

On 2026-06-29 Mark pasted exhaustive Amnia AMP end-of-sprint reports (Sprints
97/98, later merged with 99) into the Hermes Telegram DM (session
`20260629_185119_956dd0b3`). Both dumps were captured to Open Brain via live
`capture_thought` calls (one transient input-validation error self-corrected on
retry; thought `363e2de9-470c-468c-af28-ba81ac8ad749`, created
2026-06-29T17:10:02Z).

On 2026-07-07 Mark asked Hermes in the DM: *"give me a quick summary of sprints
98 and 99"*. Hermes replied that it found no meaningful records. A raw-transcript
search tool (Claude Cowork) found the content immediately, undermining trust in
the memory layer.

## Root cause

Capture was **not** at fault — the record was intact in `thoughts`, correctly
merged. The failure was retrieval-side, in three stacked layers:

1. **Ranking (server-side, open_brain repo).** For the natural phrasing, the
   sprint-report thought did not appear in the top-10 at all. ~400 Strava rides
   bulk-imported on 2026-07-04 scored 0.65–0.74 via
   `structured:text_tokens,recency_boost`: the literal token "sprint" appears in
   Zwift ride titles ("+sneaky sprint"), and the recency boost keys on the row's
   `created_at` (import date) rather than the actual ride date from 2021–2022.
   With Jira-flavored phrasing ("sprint 98 sprint 99 end of sprint report") the
   real record surfaced at 0.74 — still below the 0.75 `answer_threshold`, tied
   with bike rides.
2. **System prompt (Hermes-side).** The live `agent.system_prompt` in
   `~/.hermes/config.yaml` instructed: *"If query_brain returns no results, stop
   and say so plainly"* — with no guidance for the `uncertain` verdict and an
   explicit prohibition on follow-up searching. The model collapsed
   "uncertain, low-scoring hits" into a confident "no meaningful records".
3. **Result-format plugin (Hermes-side).** `openbrain-query-brain-format`'s
   below-threshold-with-warnings branch replaced the entire tool result with
   "I found a possible lead, but I wouldn't present it confidently…" without
   naming the lead — discarding the candidate summaries the model needed.

The silent false negative ("no records" instead of "no *confident* record") is
the trust-destroying part: an occasional miss is tolerable, a confident wrong
negative is not.

## Fixes applied (2026-07-07)

- **Plugin** (`plugins/openbrain-query-brain-format/__init__.py`): the
  low-confidence-with-warnings branch now includes the top-3 candidate
  summaries and instructs the model to present them tentatively rather than as
  "not found". Tests added in
  `tests/plugins/test_openbrain_query_brain_format_plugin.py`.
- **System prompt** (`~/.hermes/config.yaml`, live config — not in repo): the
  final retrieval paragraph now says: on an `uncertain` verdict never claim
  nothing was found — present plausible matches as tentative candidates; if
  none are plausibly relevant, retry `query_brain` exactly once with more
  distinctive keywords; only then report a miss, noting the memory may exist
  but ranked below the retrieval threshold.
- Gateway restarted 2026-07-07 ~14:18 local; both changes live (the gateway
  venv is an editable install of this repo).

## Open work (open_brain repo)

- **Scoring bug:** `recency_boost` on bulk-imported `life_items` uses import
  `created_at`, not the activity date — 2021 rides imported last week outrank
  real work content. Needs a fix plus a regression/gold-set query for this case
  (spawned as a separate task 2026-07-07).
- **S8 reranker** (`open_brain` `docs/specs/S8_reranker.md`): built and shipped
  disabled pending the L5/F5 gate (false-confident rate and abstention moved
  the wrong way on the gold set at every alpha). This incident is a concrete
  real-world instance of the query class the reranker targets — relevant
  evidence for the next gate review (Fable's call).

## Lesson

Hermes must never present a below-threshold retrieval outcome as an
authoritative absence. "Not found" and "not confidently found" are different
answers, and the surface must say which one it means.
