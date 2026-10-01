# Korean graded-reader pipeline: Level 1 pilot

The current generation scope is *Hong Gildong*, Level 1, using mapped source
spans selected by the planning agent from the remaining original narrative. The
two mapped scenes in `books/korean/honggildong/source.json` are reference examples,
not chapter limits. The narrative reference is
the pinned Wikisource/Jikji transcription of the 30-sheet Gyeongpan edition,
revision 460078, in `books/korean/honggildong/original.txt`. Preserve its original
text and exact source offsets; do not mix editions or copy a modern adaptation.
Use reviewed source notes when interpreting archaic names or placeholders. A
placeholder is not a personal name; refer to a person by an attested title or
relationship when necessary. Keep research findings in source notes, not as
permanent disclaimers in learner-facing dictionary entries.
The separate 1920 scan is a different edition and is not the generation input.
Record retained source quotes and independently review every chapter against
the pinned source, the reviewed plan and preceding chapters before publication.

Before prose, independently review a lexical plan for named people and essential
story vocabulary. Reuse full-name IDs with explicitly reviewed aliases; keep
family members distinct. Permit only one essential story-term exemption, with
its source-specific role recorded in the plan. Ordinary difficult words and
optional literary detail require simpler wording, not extra exemptions.
Annotation proposals run in exact sentence chunks; preserve separators and
review the assembled chapter independently. Do not retry annotation endlessly
when vocabulary or sentence difficulty requires revising unpublished prose.

Write natural modern Korean for a beginner. Keep sentences short enough to
follow, but let an essential story word appear with a clear contextual gloss.
Choose total chapter length by narrative coverage and learner difficulty, never
by a character, word, sentence or source-paragraph quota. Review the entire
remaining source and retain meaningful actions, relationships and development
whenever they can be expressed naturally at the requested level. Short sentences
can form a substantial chapter. Avoid both unnecessary compression into a bare
summary and padding through repetition or invented detail. A brief chapter is
valid when its scene or actual level constraints justify it. Record the scope
decision in the source plan and the length decision with the generated prose;
an independent reviewer must assess omissions and the coherent stopping point,
including whether more source narrative could reasonably be retained. These are
editorial records, not learner-facing disclaimers. Choose the source stopping
paragraph at a coherent narrative boundary; do not consume the entire remaining
book by default. Subsequent chapters start immediately after that reviewed
paragraph. Publication rejects overlapping or skipped source spans. Original
source boundaries and adaptation length are separate editorial judgments.
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
pipeline proposes and independently reviews only missing word/grammar entries.
Approved definitions remain immutable during new-chapter generation. A changed
sense or uncertain component needs a deliberate editorial task, not a speculative
new entry or a silently rewritten definition.
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
must be natural English. Selectively explain whole constructions through exact
phrase rows when several taps contribute to one pattern.
When a learner reports one weak annotation, audit comparable occurrences and
fix the shared data or rule. Add a regression for the issue and a contrasting
case that should still pass.

Use `python -m pipeline.korean_agent_harness --chapter 1 --run-dir
runs/korean-l1/chapter-001` for generation. Each source plan, prose, annotation,
new dictionary delta and sentence-help selection requires an independent review.
Repair source errors in prose before annotating; do not preserve unsupported
claims merely because a manual pilot already used them. Distinguish the people
actually named in the source from broader groups, and state family relationships
clearly in modern Korean. Omit secondary details rather than inventing them.
Use `python -m pipeline.korean_publication --run-dir runs/korean-l1 --check`
to validate accepted runs without writes, then omit `--check` to publish.
Publication requires completed review evidence, exact source spans, reconstruction,
lexical identities, complete-form steps, dictionary links and readability budgets.
Unchanged accepted runs and approved dictionary entries must be reused.
Inspect the rebuilt app preview after asset changes.

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
