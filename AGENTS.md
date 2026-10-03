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
