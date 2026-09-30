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
Publish target/lookup metadata from the reviewed lexical identity and the
versioned NIKL grade. A name is optional story lookup; a non-beginner story
term is optional story vocabulary with occurrence-specific importance; an
ordinary above-level word is extra vocabulary. Do not mark every Korean tap
as a target, or promote an ordinary beginner word to story vocabulary because
it matters in a sentence. For inflected forms, write ordered stages with the
meaning of each complete form and an explicit grammar destination. Sentence
breakdowns are optional: add one when a learner must connect several clauses
or constructions to grasp a long/difficult sentence, and leave simple ones
alone. Their parts must align with existing taps and their whole translation
must be natural English. The current whole-construction form-routing gap is
recorded in `korean_parity_audit.md`.
When a learner reports one weak annotation, audit comparable occurrences and
fix the shared data or rule. Add a regression for the issue and a contrasting
case that should still pass.

Run `python -m pipeline.publish_honggildong_smoke` to validate the single
chapter and produce its Korean app assets. The publisher checks explicit
lexical identities against the versioned NIKL list, source-alignment review,
annotation reconstruction, linked word and grammar entries, explicit form
stages, selected sentence explanations, an above-beginner budget, story-term
budget, and sentence length. Inspect the rebuilt app preview after asset changes.

On a tap with a form chain, put the base-word destination on the base row and
each transformation's grammar destination on its own row. Do not also show
those links in a separate list above the chain. If another grammar pattern
covers a larger construction containing that tap, add a reviewed row for the
complete source phrase and its complete meaning, linked to that grammar entry.
Record the exact ending segment for this phrase so publication can verify the
span. Taps without a form chain keep their direct word and grammar links.
Keep the reusable dictionary form and its definition visibly separate from
the form used in a passage and its contextual gloss. A past or polite gloss
belongs to the observed form, never to the dictionary headword. Check other
inflected and particle-attached taps when a learner reports this confusion.
