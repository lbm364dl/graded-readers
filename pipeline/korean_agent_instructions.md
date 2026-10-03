# Korean graded-reader pipeline: Level 1 pilot

## Drafts and source continuation

`previous`, `previous_chunk`, and `previous_bindings` are rejected or retained
draft proposals supplied for repair. They are never proof of a completed chapter
or permission to skip their source coverage. Only `previous_chapters` and the
current source unit establish an earlier published chapter and where this one
starts. When `previous_chapters` is empty and the unit starts at zero, chapter 1
begins at the opening of the supplied original narrative. Repair the proposed
coverage rather than planning the chapter after that proposal. Paragraph indices
refer to the supplied numbered source, not another draft's stopping point.

Source-planning review objections receive a separate independent adjudication
before triggering a rewrite. Preserve material source errors, with supporting
quotations. Reject unsupported corrections, literal wording demands for faithful
paraphrases, later-stage field requirements and demands to extend a coherent
chapter merely because more source remains. Retain both review decisions and
their provenance for publication verification; adjudication is not permission to
ignore a genuine error or to approve because retries are expensive.

Source beats anchor events to where they occur. Use surrounding source context
to resolve speakers, participant identities, pronouns and shortened titles; an
individual paragraph need not repeat a name already established nearby. Keep
this distinct from inventing an identity or moving an event to another paragraph.

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
Target Level 1 of NIKL's six-level International Standard Curriculum, corrected
2020-11-17. This is our TOPIK-aligned curriculum baseline, not an exhaustive list
of words permitted in the exam. The pinned source in
`data/korean/words/nikl_2017_curriculum_20201117.json` contains both vocabulary and
grammar grades. Higher levels are cumulative; the target band supplies the core learning goals.
A few higher-level grammar patterns may appear when natural, common wording or
faithful storytelling benefits. Keep their actual source grades and mark them
optional for the target level on their occurrences, not as required learning.
Explain each exception in optional_reason_en and assess the whole chapter in
level_reason_en: frequency, variety, complexity, level distance and how much
understanding depends on them. Repetition of one useful pattern can be easier
than many unfamiliar patterns. Use editorial judgment, not a fixed count or
percentage of grammar exceptions. Optional means outside this level's learning
goals; it does not mean the construction is unnecessary to understand the sentence.
A label or breakdown alone cannot justify a chapter dominated by advanced grammar.
If the chapter exceeds the level, set prose_revision_reason_en honestly instead
of inventing a lower source grade or a justification; the pipeline revises prose.
Rare reviewed extra vocabulary can use the existing 10% occurrence budget;
names and the one essential story term remain separate.
Review every ordinary lexical identity and every linked grammar identity against
explicit source IDs in the curriculum stage. Preserve existing dictionary IDs:
homonym numbering differs between editions. Exact spelling is only a candidate,
not proof of equivalent sense or part of speech. Missing dictionary headwords
may be productive formations or dependent units covered by a listed construction;
record all lexical/grammar prerequisites and explain the actual combined meaning.
Never infer a grade by stripping a suffix, combining English glosses or inventing
a simpler source mapping. Curriculum vocabulary grades identify lexical identities and parts of speech, not only the illustrative 길잡이말 phrase. A guide phrase is not an exhaustive sense inventory: an independently verified sense of the same lexeme can share the grade. Aggregated homonym/POS rows include each listed identity even if the single guide illustrates only one; do not merge unrelated homonyms. Grammar meanings remain function-specific: identical spelling does not license a different function. When no honest source match exists, use equivalence unlisted with empty source_ids and explain the catalog gap in analysis_en. Its grade stays null, never guessed or relabeled as Level 2. Unlisted grammar needs optional_reason_en and the same whole-chapter difficulty review as higher-level grammar. Unlisted ordinary vocabulary counts toward the extra-vocabulary budget. Catalog absence alone is not a reason to rewrite natural beginner Korean. Unsupported meanings require investigation or simpler
prose. The independent curriculum reviewer checks the complete chapter, and
publication recomputes grades from pinned source IDs and verifies review evidence.
The old 2003 A/B/C data now supplies compatibility lexical identities only.
Keep reviewed NIKL homonym number and part of speech in each occurrence's
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
reviewed six-level curriculum bindings. A name is optional story lookup; a non-beginner story
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
new dictionary delta, curriculum crosswalk and sentence-help selection requires an independent review.
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


Shared word definitions can require editorial correction when new legitimate
senses appear. Normal generation must not silently broaden approved entries or
keep retrying an unsupported sense as an annotation problem. The repair planner
records exact dictionary_revision_entry_ids and stops for the separate
`pipeline.korean_dictionary_revision` workflow. It researches primary dictionary
evidence, audits every published Korean level and the new draft, preserves IDs
and valid earlier meanings, and requires an independent review before changing
shared definitions. Keep that evidence in revision_reviews, not learner-facing
notes. Resume the cached run after approval; unrelated annotations and dictionary
research remain reusable. Do not invent a lexical entry for productive grammar.

Construction scope is independent of inflection. An exact complete phrase can
start at an uninflected prefix or particle; include meaning-bearing negation.
Record its whole form, whole meaning and inclusive ending index on the first
included word. The app exposes this reviewed construction from each covered
word while form rows retain their own exact transformation lessons. Preserve
source text and existing taps rather than moving or dropping the negative word.

Offline drafting, annotation and review roles must use only the supplied data. Do not call any tools, including MCP resource inventories or resource templates. If primary research is needed, record the need for the separate research editor; do not invoke research from an offline job.

Lexical-plan review objections receive independent adjudication before repair.
Preserve approved lexical identities, permit supported homonymous aliases as
distinct identities, and treat story-term allowances as maxima. Reject invented
named identities and missing needed names. Both original objections and the
independent decision remain bound to publication evidence.
