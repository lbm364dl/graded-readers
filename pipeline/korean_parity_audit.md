# Korean pipeline coverage and remaining differences

Scope inspected: source/manifests, generation harnesses, dictionary editorial
jobs, publication validators, form and sentence-help layers, and app services/UI
for Chinese *Three Kingdoms*, Japanese *I Am a Cat*, and Korean *Hong Gildong*.
Korean currently publishes only Level 1 chapter 1. This does not claim full
feature parity. The new curriculum stage uses a six-level TOPIK-aligned
teaching baseline; it is not an exhaustive exam specification.

| Layer | Korean implementation | Remaining scope |
| --- | --- | --- |
| Source and adaptation | Complete searchable Wikisource/Jikji original pinned to revision 460078, raw/extracted checksums, Unicode paragraph spans, reviewed source notes and lexical identity plan, reviewed source plan and independently reviewed modern prose. The agent selects a contiguous source span and a coherent stopping paragraph from the remaining narrative, with reviewed coverage/omission reasoning. Total chapter length has no numeric quota. The placeholder 某 in the father's name has an edition-specific scholarly reference; it is not treated as a literal personal name. | Source-boundary decisions still require independent review; the two manually mapped scenes are reference examples. Other levels require their own learner policy. The archived 24-sheet scan is a different edition. |
| Annotation and difficulty | Independently reviewed exact taps, meanings and explicit NIKL identities. Reconstruction, lexical budget and story-term budget gate publication. Unannotatable ordinary words send unpublished prose back for simplification. Sentence length is reported for review rather than mechanically rejected. | The 2017 NIKL curriculum (corrected 2020-11-17) supplies six vocabulary and grammar bands. Explicit independently reviewed sense/POS and construction bindings determine grades; the older A/B/C list only preserves identities. Optional higher-level grammar retains true grades and contextual labels; editorial review judges overall load without a grammar quota. Generation and target-bound review support TOPIK 1–6; only TOPIK 1 is published so far. |
| Dictionary editing | Approved reusable entries remain immutable. Only missing word/grammar IDs go to a proposer and independent reviewer. Exact source positions keep repeated uses separate. | No Korean component graph or multi-sense editorial workflow yet. Meaning uncertainty must remain an editorial issue. |
| Form and construction explanations | Complete-form meanings and separate form labels, exact reviewed per-stage grammar routes; base and transformation rows carry their links without a duplicate list. Wider constructions have exact phrase rows with their full meaning. | No independently editable merged reader-unit layer or Japanese-style merged construction chains. Korean displays annotation taps directly. |
| Sentence help | Every sentence is inventoried and independently reviewed for selection; selected parts align to taps, while simple sentences remain unselected. | Selection is a reviewed judgment, not an automatic length rule. Korean sentence-help parts currently explain structure in text; grammar lessons open from word/form rows, without inline lesson buttons in the sentence-help parts. |
| Publication and reuse | Fingerprinted runner cache and completed-run cache; staged validation before promotion; cumulative dictionaries/help; retained review evidence rejects changed source/content/definitions/help in normal app builds. | Current publisher explicitly supports Hong Gildong Level 1. Other books/levels need their own source/scope configuration. |
| App | Word and grammar browsers, focus/lookup cues, contextual examples, separate dictionary/observed forms, stage links and optional sentence analyses. Stale stage meanings, labels or source text disable their routes. | Current UI does not expose a component/meaning-guide graph or merged Korean taps. |

Regression coverage includes source tampering, exact paragraph binding,
independent rejection/repair, approved-entry reuse, unchanged-run cache reuse,
changed-definition invalidation, reviewed-proposal/artifact consistency,
cumulative source occurrences and form routes, and selective sentence help.
Transformation rows require exactly one grammar destination and omit the
separately displayed dictionary base. Pronunciation readings may differ from
spelling, while the final written form must match the tap exactly.
Sentence analysis may divide punctuation from trailing whitespace but cannot
divide lexical taps. Approved-entry lists are sorted before model requests so
process hash randomization cannot invalidate otherwise identical cache inputs.
Manual pilot fixtures preserve earlier inflection/construction regressions;
they are test data, not approved source-adaptation evidence for publication.

Initial publication before length feedback (2026-10-01): chapter 1 was generated from the pinned text
and passed all six independent review stages. All 31 ordinary vocabulary
occurrences are NIKL A; no story-term exemption was needed. The longest sentence
has eight eojeol; three of six sentences have selected help. Publication retains
30 reusable word entries and 21 grammar entries, including unused approved
pilot entries. A completed-run rerun made zero model calls with unchanged job
metadata. Validation passed 47 Python checks, 357 Flutter tests, Flutter analysis
and a release web build. Playwright checked the rebuilt Chrome preview at
390×844, dictionary/base meanings, stage grammar destinations and selected
sentence help.

Length-feedback revision: removed the planner's two-paragraph cap and the prose
character, sentence-count and whitespace-unit quotas. Planning now chooses a
stopping paragraph from the remaining complete original, with exact contiguous
source spans; independent review assesses retained development, omissions,
padding and the stopping point. `adaptation_decisions` retains the plan's scope
reason and the prose agent's length reason. Regression cases accept both a
justified brief chapter and prose exceeding the former caps, permit expansion
beyond the reference scene, and reject missing decisions or overlapping spans.

Larger runs validate each annotation chunk's identities and form routes before
assembly. Full-review repairs use an agent-selected affected-chunk plan and
reuse other chunks. New grammar IDs are coordinated through explicit bindings,
preserving approved IDs and distinct functions. The complete mapped annotation
receives independent review, and publication replays the underlying proposals,
bindings and repair evidence. Korean pronunciation notes are optional; complete
written forms and meanings remain required.
Across prose revisions, agent-selected exact unchanged occurrences can retain
their original annotation evidence; unresolved errors must be excluded.
Technical failures do not trigger prose rewriting. Sentence spacing-unit counts
remain diagnostic, with beginner difficulty assessed by independent review.

Expanded publication (2026-10-01): all six stages independently approved chapter
1 covering original paragraphs 0–16 (Unicode span 0–1923). Its 70 sentences have
1,574 characters, or 1,187 excluding whitespace; 19 sentences have selected help
and 51 do not. Of 298 ordinary vocabulary occurrences, 297 are NIKL A; the
attested B adverb 똑같이 is ordinary extra vocabulary, not an adjective
inflection or a story exemption. 벼슬 is the one reviewed story term. The longest
sentence has 12 eojeol, reported as a diagnostic rather than a cap. Publication
retains 128 word entries and 63 grammar entries; only 98 missing words and 42
missing grammar entries required new dictionary work.

The real run reused 57 of 70 annotations after a narrow prose revision and
subsequently repaired agent-selected affected chunks. Resume preserves the
latest reviewed draft and review findings, with fresh review when context
changes. Critics receive pinned lexical identities, grades and the actual
above-level vocabulary budget, including exact attested surface candidates.
Reviewed linguistic references distinguish written morphology from
pronunciation and are fingerprinted in publication evidence. Optional readings
need not be populated on every occurrence; differing readings must be accurate.
These changes address repeated failure paths found during generation, rather
than manually expanding or editing the sample chapter.

Validation: 65 Python checks, 357 Flutter tests, Flutter analysis, release web
build, publication replay and an unchanged completed-run rerun with zero model
calls. Playwright checked the rebuilt Chrome preview at 390×844: expanded
chapter metadata/text, generic dictionary versus contextual past meanings,
linked past grammar, selected sentence help, and ordinary above-level marking.
The console reported no errors. This length-policy implementation is Korean;
the older Chinese/Japanese generators still have source-relative length bands.

## TOPIK 1 publication — 2026-10-02

The real run in `runs/korean-topik1/chapter-001` completed all seven independent
reviews. The published pilot now has 91 sentences, 1,781 characters, 392 word
taps and 22 selected sentence analyses. All 91 sentences were checked, including
the simpler sentences deliberately left without analyses. The reviewed source
scope remains paragraphs 0–16, offsets 0–1,923; its coherent nighttime ending
and difficulty-based omissions are recorded in the prose planning evidence.
No chapter-size quota was used.

Every ordinary lexical identity and linked grammar identity has a reviewed
binding to the pinned six-level curriculum or an explicit unlisted status.
Extra ordinary vocabulary is 7.37% of occurrences. Optional sourced grammar
retains grades 2 or 3; unlisted grammar retains null, not an invented grade.
Per-pattern rationales and the whole-chapter assessment are publication evidence,
not permanent caveats in the generic dictionary lessons. Higher-level Korean
generation is not configured yet; this publication is chapter 1 at TOPIK 1.

The complete pilot annotation review repaired contextual tense in final form
glosses, optional pronunciation errors, and the scope of qualified negative
phrases. Construction links now cover every included tap, including an
uninflected opening word, while preserving source text and tap boundaries.
Three shared dictionary corrections were separately researched and reviewed
against all published pilot and supplied draft occurrences (14 distinct uses).
Existing approved entries and unchanged annotation chunks were reused.

Validation: 94 focused Python checks, 360 Flutter tests, Flutter analysis,
publication replay, and a completed-run rerun without model calls. Release web
and Android debug builds succeeded. Playwright checked the rebuilt separate
Chrome preview at 390×844, including a physical word tap, neutral dictionary
versus past passage forms, a true Level 2 optional grammar link, an unlisted
grammar link, full qualified negation available from its opening name, and
selective sentence help. Resume captions use current reader metadata rather
than an old saved level label. The earlier phone update succeeded with data
restored; the new chapter's Android build awaits a USB reconnection.

Remaining follow-up: quoted-sentence extraction can attach a closing quotation
mark to the next example (observed in the `살다` examples) or end an example
before its closing mark. Repair the shared dictionary/sentence-inventory
boundaries together and refresh their source-bound help evidence, preserving
text and taps. This is a punctuation-boundary issue, not a dictionary meaning
or curriculum-grade correction. The 10,635-word catalog is a grading reference;
we do not claim an exhaustive reviewed dictionary for every entry or an exact
list of words permitted on TOPIK exams.

Optional-grammar follow-up: the existing producer and reviewer instructions
permit useful above-level grammar with its actual grade, an occurrence-specific
optional rationale, and an honest whole-chapter difficulty assessment. No grammar
quota was added. Frequency evaluation now uses the reviewer's shared inventory,
including standalone grammar taps while deduplicating overlapping layers at one
source position. A regression checks this case and the contrasting higher target
where the same pattern becomes in-level and no longer optional. All published
Korean coverage (one chapter, ten optional grammar identities) was recomputed;
its stored evaluations remain correct and need no content repair. Validation:
81 curriculum/pipeline/publication Python tests and eight Korean reader Flutter
tests passed, including contextual optional labels and neutral generic lessons.


TOPIK edition support: the harness now accepts explicit targets 1–6. The planning
and prose prompts, independent reviewers, curriculum evaluation and selective
sentence help use that target. Higher-edition instructions describe register and
level-appropriate narrative complexity without imposing length or grammar quotas.
Source asset IDs, dictionary example labels and publication evidence preserve
each edition's target. Approved dictionary entries remain shared. Publication
stages existing editions alongside the incoming one and combines their exact
dictionary and sentence-help routes; it rejects target mismatches. Regression
coverage verifies reviewed target/source identities for all levels 2–6 and a
TOPIK 2 publication retaining TOPIK 1, including unique dictionary entries and
per-edition app tap labels. All 102 focused Python checks pass, and the existing
real TOPIK 1 run passes publication replay. The real TOPIK 2 run has approved
source and lexical plans; its chapter draft is under independent prose review.
This support does not establish that TOPIK 2–6 chapters are already published.


Generation scheduling follow-up: `--stop-after prose` provides a reviewed
preparation checkpoint distinct from a publishable run. Preparations for TOPIK
3–6 can proceed while TOPIK 2 annotations finish; final runs resume with the
current approved dictionary registry. Opt-in complete-sentence batching reduces
repeated annotation calls without changing chapter scope, source text, sentence
help decisions or tap reconstruction. Its recorded partition is independently
replayed by publication, and the zero default preserves existing run evidence.
Regression coverage includes a nonpublishable preparation, preserved separators,
an intact sentence exceeding the job budget, and rejection of a changed assembly
partition. Validation: 105 focused Python tests passed; TOPIK 1 publication replay
still passes. Actual TOPIK 3 and 5 plans independently chose source paragraphs
0–23 (20 beats), beyond TOPIK 2's paragraphs 0–16 (14 beats), through narrative
judgment rather than forced increasing lengths. Their prose reviews are underway.
