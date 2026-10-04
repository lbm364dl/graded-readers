# Working on this repository

## Pipeline worker model

All pipeline workers, including research, repairs and independent reviewers,
must use `gpt-6-luna` with `low` reasoning. Keep this policy in the shared runner;
do not introduce separate model defaults for a language or a review stage.
Reuse previously reviewed work rather than regenerating it merely to change
the worker model.

## Turn feedback into reusable improvements

Treat examples in user feedback as evidence of a broader issue, not as an
exhaustive list of items to patch. Inspect comparable cases and identify whether
the cause belongs in agent instructions, structured data, pipeline validation,
publication, or app presentation. Fix it at the appropriate shared layer.

When a lesson can improve future output, update the relevant pipeline agent
instructions and add regression coverage for the general behavior and at least
one contrasting case. Existing comparable content should be checked and repaired
where necessary. Do not claim a general fix if only one example was changed;
explain remaining coverage or limitations.

Avoid ad hoc word-specific UI rules, kana-suffix guesses, and concatenation of
English glosses to manufacture linguistic explanations. Prefer reviewed,
structured data with explicit identities and links. Preserve source text and
tap boundaries unless the task actually calls for changing them.

## Separate reusable knowledge from contextual analysis

Word and grammar dictionary entries must make sense independently of any
particular passage. Reuse approved entries and senses rather than regenerating
their explanations for each occurrence. Keep passage-specific translations,
grammar roles, and notes on occurrence records.

Keep each grammar entry focused on its own pattern and the formation needed to
understand it. Do not append catalogs of polite, past, negative, imperative, or
other combinations merely because they occurred in a source example. Those
transformations have their own entries and are linked in occurrence chains.
Include a prerequisite or contrast only when it helps explain this pattern;
avoid repeating another lesson. Examples belong in the dedicated example section.
Multiple annotation layers for one sentence occurrence should yield one example
card, preserving useful notes. Distinct source positions must remain distinct,
even when their sentence text matches.
Do not turn reviewer objections or one passage's ambiguities into permanent
learner-facing disclaimers. Every explanatory sentence should answer a natural
question about the entry itself. Equivalent terminology should identify the
same concept; distinguish a stem from a complete inflected form explicitly.
When feedback reveals repeated stale material, audit the complete published
pilot coverage and record what was checked, rather than fixing another small
hand-picked set and calling the whole level consistent.

Breakdowns must be consistent across simple inflections, merged constructions,
and lexical expressions. Each displayed stage needs a meaning for its COMPLETE
form, not merely an ending or a category such as "Construction". For example,
住むことにする and 住むことにします mean "decide to live"; 住むことにしました
means "decided to live". Express politeness and other form information separately
from the complete meaning. Link lexical stages to word entries and grammatical
transformations to grammar entries; do not invent lexical entries for productive
grammar patterns or duplicate legacy lookup blocks.

Respect idioms: explaining an expression's components does not license a literal
translation of its combined meaning. Missing or uncertain contributions should
be recorded for investigation, not filled with unsupported claims.
When a component-derived word later appears in the text, route matching lexical
identities to the attested entry without duplicate search results. Preserve old
IDs as compatibility destinations; do not merge different readings or kinds.

## Verify and minimize unnecessary work

Choose chapter length through reviewed source coverage, narrative coherence and
the requested learner level. Unless the user explicitly requests a fixed size,
do not impose chapter-wide character, word, sentence or source-paragraph quotas.
Short individual sentences do not require a short chapter. Review avoidable
compression and repetitive padding alike; a brief chapter is valid when the
source scene or actual learner difficulty justifies it. Keep this decision in
pipeline planning and review, never as a manual expansion of one sample.

Preserve unrelated worktree changes. Reuse reviewed data and cached agent work;
send agents only new or changed material, with review proportional to the risk.
Changing UI wording is not a reason to rerun unrelated dictionary research.
Verify both pipeline data and app behavior when a change crosses that boundary.
If updating the preview, rebuild it and verify the actual user-facing window;
do not assume that a refresh applied.

Pipeline workers should have tools and organized, inspectable inputs. Let them
read references, research, write drafts and run validation before submission;
do not disable tools merely to enforce a JSON response. Consume the exact
validated artifact instead of making a worker reproduce it in its final message.
Keep independent review and publication checks. Apply improvements to shared
worker infrastructure and inspect Chinese, Japanese and Korean equivalents.
Optimize for reliable completed work, not minimum prompt tokens in isolation.
When valid annotations need semantic corrections, use the shared issue-planned
edit protocol with exact targets and base digests. Preserve source text and tap
ranges, retain replayable evidence and independently review the derived result.
Route a required boundary change explicitly; a stale or out-of-scope patch is
not permission to regenerate unaffected annotations. Preserve reviewed sibling
chunks when a batch fails, and collect all explicit prose objections before
targeted prose revision.

Chinese, Japanese and Korean annotation pipelines must share the bounded chunk
lifecycle scheduler in `pipeline/chunk_scheduler.py`. Each generation, local
validation, independent review and targeted repair lifecycle should progress
without waiting for every other chunk to be generated. Keep language-specific
linguistic contracts and final chapter/publication checks. When changing a shared
workflow, inspect all three callers and test their integration; do not assume a
feature exists in another language. Cached approvals must bind exact output and
relevant context, including distinct source positions.

Keep recurring pipeline problems in the shared maintenance lane. Count distinct
jobs and retain exact evidence; a successful process exit or an approval with
outstanding issues does not resolve a failed review. Verify proposed causes and
reviewer objections against actual artifacts and comparable language paths.
Use one GitHub issue per recurring pattern and link tested fixes from a PR;
preserve human investigation notes and never publish raw worker transcripts.
Diagnosis is a proposal. An issue is resolved only after a reproducer, contrasting
regression, independent review and affected-output verification support that claim.
Do not restart live chapters just to collect evidence or promote a proposal.
Keep dashboard counts explicit about attempts, drafts, reviews and publication.

When repeated linguistic reviews contradict an earlier supported correction,
investigate the exact finding before editing again. Use the shared bounded
independent adjudication route with the immutable candidate, source position,
review and repair history, and relevant linguistic references. A previous approval
or a majority of reviewers is not linguistic evidence. Preserve every current
finding; unsupported objections need concrete cited evidence, genuine defects
remain repairable, and uncertainty must not become approval. Never override source,
schema or other deterministic checks. Keep adjudication evidence replayable through
cache reuse and publication, and use the same GPT-6 Luna low policy as other workers.

Before resuming production after a convergence or adjudication change, run an
isolated small-chunk smoke that completes the whole path: a targeted semantic
repair, a fresh independent review of the derived candidate, effective
acceptance, and cache replay of the accepted result. Verify the same evidence
through publication checks. An adjudicator-only success or passing unit tests
does not establish that the full repair-and-acceptance lifecycle works.

When a worker verdict changes during host validation, inspect the raw result and
the host's binding checks before attributing the outcome to linguistic uncertainty
or model quality. Supporting source spans are not automatically additional defect
targets. Validate a smoke's required request payload and immutable gate context
before launching paid workers; driver setup errors are not pipeline-quality evidence.

Unresolved linguistic evidence should enter the shared bounded research and
independent-review path before stopping a chunk. Keep the candidate and raw review
unchanged during research. A lesson independently approved for the current run is
distinct from an unreviewed proposal and from promotion into the published registry;
retain and replay its exact research/review provenance. Do not repurpose an existing
component or construction identity merely to make the evidence fit.
Use the same complete-form and meaning-scope criteria in research, research
review, annotation review, repair and adjudication. A missing dictionary headword
for an exact inflected substring does not by itself prove that its analysis is
unsupported; investigate applicable formation rules and attested usage. If a
researcher identifies a needed authoritative page, request and capture it rather
than treating an uncaptured web observation as evidence or stopping without the
available investigation.

Research review evaluates the research claims and their citations, not whether
the immutable annotation has already been repaired. A supported fact that
establishes a defect is useful repair evidence; it is not a candidate approval.
Keep source-request instructions specific to the current stage: the initial
research pass may request direct primary pages, while the bounded captured-source
continuation may not open another capture cycle. When changing host target
binding or evidence policies, preserve exact replay of historical receipts and
use an explicit new policy version for new decisions.

New annotation reviews must identify defective candidate fields with structured
`candidate_paths` and list contextual fields separately in `supporting_paths`.
Validate these pointers against the exact immutable annotation before accepting
the review. Mentions of other segments, forms or spans in prose are not additional
defect targets. Preserve every explicitly targeted defect and exact source
position, including repeated text. A diagnostic target does not authorize changing
source text, tap boundaries or an entire annotation. Historical untyped reviews
retain their original replay contracts.
Record raw-to-host adjudication downgrades in maintenance with both authenticated
artifacts. A downgrade is evidence to investigate, not proof of a validator bug
or a model failure; a successful exit does not resolve it.

Present each unresolved research task with its exact finding, candidate fields
and evidence gap. Other findings remain context, not substitute research tasks.
Research review must check that every proposed fact and citation addresses its
assigned unresolved finding; an accurate fact about another construction does
not resolve that finding. Preserve historical research packets and receipts.
