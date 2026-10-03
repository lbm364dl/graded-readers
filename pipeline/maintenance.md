# Shared pipeline maintenance

The maintenance lane watches selected Chinese, Japanese and Korean runs without
restarting their content workers. It collects evidence, commissions bounded
diagnoses, and optionally updates GitHub issues. A diagnosis is a proposal;
implementation, independent review and fresh production verification remain
separate steps. Workers use the shared GPT-6 Luna low runner with tools and
inspectable input files.

```sh
.venv/bin/python -m pipeline.maintenance_loop \
  --run-dir runs/korean-topik3/chapter-001 \
  --run-dir runs/korean-topik4/chapter-001 \
  --run-dir runs/korean-topik6/chapter-001 \
  --output runs/maintenance/current \
  --minimum-jobs 3 --limit 1 --interval 120 \
  --github-repo lbm364dl/graded-readers --github-writes 2
```

Add Chinese and Japanese `--run-dir` arguments to the same command. `--since`
accepts a Unix timestamp for an observation window; `--once` performs one cycle.
The output directory must not overlap a monitored run. A lock prevents duplicate
loops using the same output. The scanner rejects symlinked and out-of-run
artifacts, tolerates partial live JSON, and never writes production reports.

`incidents.json` records exact artifact paths, distinct jobs, repeated symptoms
and unresolved/resolved observations. Transient validation failures remain cost
evidence after a later valid result. A zero process exit does not resolve a
failed validator; an approval with outstanding issues does not resolve a review.
Resolution follows the relevant attempt and, where available, exact occurrence
context. Historical failures do not imply a currently running worker has failed.

`triage/triage-state.json` preserves content-addressed diagnoses. Only repeated
patterns trigger a worker; a known pattern is reconsidered when its distinct-job
count doubles. `--limit` bounds diagnoses per cycle. Proposals must cite an actual
incident and include a reproducer, contrasting case and verification plan. An
`already_addressed` hypothesis additionally needs current code and test evidence;
it does not close an issue or approve content. The worker's no-mutation rule is
an instruction, not an operating-system read-only sandbox. Tools remain enabled;
this lane does not automatically apply or promote its proposed code changes.

An invalid diagnosis receives one correction attempt with the exact local
validation error. Rejected proposals and their artifact digests remain separate
from accepted findings. If correction still fails, the cluster waits for doubled
evidence while other clusters proceed; repeated polling must not replay one bad
proposal forever. A failed refresh preserves the previous accepted finding and
its original job/digests. A citation's line suffix is permitted only when its
underlying file is the exact recorded incident artifact.

GitHub synchronization requires an explicit repository. It publishes conservative
counts and repository-relative artifact references, never raw transcripts,
prompts or unverified model diagnoses. A stable cluster marker deduplicates open
and closed issues. A managed block preserves human notes; failed writes remain
eligible for retry, and actual write attempts are bounded separately from model
jobs. `github-state.json` supplies issue numbers to the dashboard. The loop never
closes an issue or merges a PR. Link a tested correction from a PR and close its
issue only after independent review and affected-output verification.

## Local progress dashboard

```sh
.venv/bin/python -m pipeline.dashboard \
  --maintenance-dir runs/maintenance/20261003 \
  --run-dir runs/graded-readers/sanguoyanyi-ch1-full-hsk456-v8/chapter_001-hsk4 \
  --run-dir runs/graded-readers/wagahai-ch1-agent-smoke-n5-construction-v8
```

Open <http://localhost:8771/>. All six Korean chapter-1 runs are included by
default; repeat `--run-dir` to add other runs. The loopback-only page refreshes
every five seconds and cannot change runs or approve content. It shows ready
chapters, actual live workers, and stopped or unpublished work in plain language.
Pipeline follow-up is work for the maintainer, not a request for user intervention.
Click a chapter card to see its live agents, their tasks and elapsed time, the
latest failed check, and saved submission/review history. Pipeline coordinators
are separate from AI workers; submitted output is separate from reviewer
approval. Stopped checks do not require your approval. The selected chapter,
expanded findings and dialog scroll position survive refreshes. Run paths and
maintenance diagnosis proposals remain under the collapsed technical details.

Korean readiness observes the public file, enabled metadata and matching chapter
review digest; complete private reports are listed separately. The dashboard
does not replay linguistic review or replace publication checks. Chinese and
Japanese currently show selected run history and live activity; their publication
state is explicitly unchecked. Maintenance proposals, tested corrections and
fixes verified on new output are separate counts.

## First verified mechanisms

The Korean missing-lesson cluster exposed a contract ambiguity. Phrase coverage
does not supply a grammar tap's direct lesson: each grammar-kind segment requires
the exact `(segment_index, lexical_id)` occurrence. The existing local validator
already calls the final asset builder, so a second duplicate preflight was
unnecessary. Its diagnostic now identifies every missing tap, surface and lesson;
worker instructions explain the direct link separately from a phrase link.

Audit found missing pairs in saved proposals at TOPIK 3, 4 and 6, including
`annotation-0-chunk-023-40`, `annotation-0-chunk-013-35` and
`annotation-0-chunk-013-45`. This verifies a recurring mechanism, not the cause of
every historical incident. Regression coverage contrasts a covering phrase with
and without the direct lesson, preserves distinct positions, and rejects same
identity/position collisions while retaining different lessons sharing a tap.

The decoder publishes a phrase at its source range's first tap. Moving its worker
attachment inside an unchanged range does **not** change that position. A proposed
diagnosis suggested moving that attachment to avoid duplicate keys; tracing the
decoder disproved the suggestion. No deduplication, invented identity, altered
source range or weakened publication invariant was introduced.

Local reviewers also need to distinguish a proposed new grammar function from
an existing approved lesson. Draft functions receive linguistic occurrence
review and remain subject to later identity coordination and independent
dictionary review. Absence of a later-stage definition alone is not an annotation
defect; unsupported analysis or an existing lesson with the wrong function is.

Chinese and Japanese were inspected for comparable occurrence checks. Their
representations differ from the Korean attached-link schema, so these diagnostics
belong in the Korean contract. The collector, runner and lifecycle scheduler
remain shared. Published TOPIK 1, 2 and 5 are retained controls; pending fresh
TOPIK 3, 4 and 6 reviews/publication must not be counted as completed chapters.
