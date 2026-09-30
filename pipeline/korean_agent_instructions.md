# Korean graded-reader pipeline: Level 1 pilot

The only current publication scope is *Hong Gildong*, Level 1, chapter 1.
Develop later chapters and levels through the same gates, but do not publish
them before their prose, lexical occurrences, source alignment, and tap glosses
are reviewed. The narrative reference is the 1920 National Library of Korea
scan in `books/korean/`; do not copy a modern adaptation. Record the source
beats retained in each chapter and review them against this edition.

Write natural modern Korean for a beginner. Keep sentences short enough to
follow, but let an essential story word appear with a clear contextual gloss.
The NIKL A band is our beginner *vocabulary baseline*, not an official TOPIK
list. Keep reviewed NIKL homonym number and part of speech in each occurrence's
`lexical.id`, even when its surface is inflected. Do not infer lemma or grammar
from a Hangul suffix. Particles and endings need contextual explanation where
they affect comprehension; a productive construction is grammar, not a new
dictionary word. Proper names and story-specific terms need reviewed registry
entries and use a strict story-term budget.

First reuse existing approved identities and explanations. Store reusable
vocabulary/grammar knowledge apart from passage-specific meanings. Preserve
prose and tap boundaries during annotation repair unless the prose itself is
being deliberately revised. Every segment must reconstruct the source exactly;
every word tap needs a useful English meaning of its whole occurrence. Explain
an idiom as an idiom rather than concatenating literal component meanings.
Each lexical identity needs a standalone entry in `l1.words.json` or
`l1.grammar.json`. Grammar links on occurrence records carry their own
`context_en`; never copy a one-passage note into a reusable lesson. Link
inflections and particles to grammar lessons explicitly, without suffix
guessing. Repeated positions must remain distinct examples. New chapters
reuse matching IDs and definitions before creating entries. The current
publication scope has no automatic Korean dictionary proposer/reviewer yet;
do not call it an end-to-end agent generation pipeline.
When a learner reports one weak annotation, audit comparable occurrences and
fix the shared data or rule. Add a regression for the issue and a contrasting
case that should still pass.

Run `python -m pipeline.publish_honggildong_smoke` to validate the single
chapter and produce its two Korean app assets. The publisher checks explicit
lexical identities against the versioned NIKL list, source-alignment review,
annotation reconstruction, linked word and grammar entries, an above-beginner
budget, story-term budget, and sentence length. Inspect the rebuilt app preview
after asset changes.
