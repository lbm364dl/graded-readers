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
| Dictionary editing | Approved reusable entries remain immutable. Only missing word/grammar IDs go to a proposer and independent reviewer. Exact source positions keep repeated uses separate. | Shared sense corrections have a separate primary-evidence workflow auditing all published and supplied draft occurrences. No Korean component graph yet; uncertainty remains an editorial issue. |
| Form and construction explanations | Complete-form meanings and separate form labels, exact reviewed per-stage grammar routes; base and transformation rows carry their links without a duplicate list. Wider constructions have exact phrase rows with their full meaning. | No independently editable merged reader-unit layer or Japanese-style merged construction chains. Korean displays annotation taps directly. |
| Sentence help | Every sentence is inventoried and independently reviewed for selection; selected parts align to taps, while simple sentences remain unselected. | Selection is a reviewed judgment, not an automatic length rule. Korean sentence-help parts currently explain structure in text; grammar lessons open from word/form rows, without inline lesson buttons in the sentence-help parts. |
| Publication and reuse | Fingerprinted runner cache and completed-run cache; staged validation before promotion; cumulative dictionaries/help; retained review evidence rejects changed source/content/definitions/help in normal app builds. | The publisher supports Hong Gildong TOPIK 1–6 and preserves existing editions while merging dictionary/help assets. Other books need their own source/scope configuration. |
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


Actual annotation retry findings: completed TOPIK 2 worker outputs exposed wrong
legacy IDs for non-A words and repeated complete forms under separate grammar
labels. Higher-edition annotation now retrieves exact identities for proposed
headwords before annotating, preserving alternative homonyms/POS and omitting
legacy grades. Headword proposals are untrusted search requests, not definitions
or approved mappings. The existing annotation and curriculum gates still check
actual occurrences. The higher-edition prompt avoids resending the full A list
and supplies relevant approved words. Duplicate-form validation now reports the
actual error and asks for a complete-phrase grammar link instead of a repeated
stage; it explicitly forbids manufacturing a bare stem. This preserves the
validity rules for existing content. Regression coverage contrasts valid chains
with repeated forms and checks homonyms/POS survive candidate retrieval without
a claimed grade. The new curriculum checkpoint permits reviewed annotations and
bindings to wait for shared dictionary publication; it cannot itself publish.
Validation: 108 focused Python tests and complete TOPIK 1 publication replay pass.
Real TOPIK 3 and 5 runs reused their approved plans and prose and are progressing
through annotation/curriculum reviews; TOPIK 4 prose is prepared.


Interrupted assembly recovery: TOPIK 2 stopped after its 77 sentence jobs because
one chunk retained a duplicate complete-form stage through all local attempts.
The parent assembly had not been saved, so ordinary assembly reuse could not
recover the other workers. The shared producer now checks completed worker
proposals against exact chunk text, lexical identities, complete-form routes
and offline tool evidence before recovering them when no reviewer objections
exist. A rejected proposal guides a fresh numbered repair without overwriting
the old evidence. These are still unapproved proposals until full independent
chapter review. Regression coverage distinguishes a valid cached worker, an
invalid identity and a worker that called tools outside its role, and verifies
that full review remains required. The real TOPIK 2 restart recovered 76 workers
and repaired the remaining chunk. All six editions now have reviewed prose;
TOPIK 3–6 are proceeding through annotation and curriculum checkpoints before
shared dictionary work. TOPIK 2–6 are not yet published.

Generation recovery and lexical classification audit (2026-10-02): completed
annotation workers can be recovered as proposals after interrupted assembly,
including post-review repairs. The exact rejected proposal is excluded and the
assembled chapter still requires independent review. Regression cases contrast
valid recovery, unchanged rejected output, invalid identities and tool-profile
violations. Primary Basic Dictionary entry 62210 sense 32 documents the restricted
scolding use of 나다 in 혼이 나다; annotation and dictionary review receive that
reference without creating a fictitious expression headword. This evidence is
not itself approval of a generated entry.

Dictionary word/story tags no longer determine exemptions for ordinary passage
uses. Approved legacy word entries missing from the baseline can be retrieved
with an honestly unlisted grade. Occurrence kinds determine grading; stable
IDs and definitions remain unchanged. A regression contrasts ordinary 벼슬
(counted as extra, not exempt) with proper-name identity matching. These changes
were prompted by real TOPIK 2 and 5 generation failures; higher editions still
require all publication and app checks before they are called complete.

Higher-level lexical coverage: missing catalog headwords go through primary
Basic Dictionary research, with Standard Dictionary fallback, and independent
review before becoming supplemental
candidates. Their stable IDs derive from the dictionary record and POS; distinct
lexemes stay distinct. Productive expressions and uncertain headwords remain
unresolved rather than receiving fabricated entries. Verified catalog additions
have no assumed curriculum grade and no story exemption. Dictionary definitions,
actual passage use and curriculum correspondence still require their normal
reviews. Regression coverage checks primary URL/ID correspondence, full request
coverage, unresolved patterns, rejection/repair, reuse and tamper detection,
plus an ordinary unlisted occurrence contrasted with an unchecked identity.

The Standard Dictionary's dynamically loaded record text is retrieved from its
official search and content endpoints, with record numbers taken from search
results and snapshot fingerprints preserved for review. Supplemental identities
use their dictionary's own namespace. Existing lexical identities are compared
before adding a distinct homonym/POS; productive patterns stay grammatical.
A bounded research fallback also investigates repeated unresolved annotation
identities without replacing approved lexical identities.

An early prose grammar screen now detects clear target-level overload before
detailed annotation. It permits a few useful higher patterns and does not equate
unlisted with advanced. Exact occurrence-bound curriculum review remains the
publication gate. Contrasting regressions cover accepted optional load versus
rejected clear overload; neither changes prose directly or substitutes for final
review. TOPIK 2's real curriculum rejection prompted this optimization, and its
prose repair retained 67 unchanged sentence annotations.

Primary-record review follow-up: each proposed supplemental reference is now
retrieved before the critic checks headword, POS and sense; both reviewer and
creator receive the same research policy. Registry evidence retains the retrieved
record text and checksum, preventing URL-only claims from serving as attestation.
The curriculum critic also receives applicable reviewed lexical references so a
previously investigated idiom is not needlessly sent back to prose rewriting.
These establish lexical meaning only; curriculum grades still require their own
source bindings. The 2017 source row for 아쉽다 labels its POS as 명사 despite
the adjective-shaped guide 작별이 아쉽다; this catalog discrepancy remains an
editorial issue, not permission to silently change the pinned dataset or guess a
POS crosswalk.

Annotation throughput follow-up: a headword discovered only during annotation
now carries an explicit missing-identity error and requests primary research on
its first failure, rather than spending three identical annotation retries with
no lexical candidate. Wrong IDs for already supplied candidates remain distinct
and retain the exact candidate destinations in their repair diagnostics.
Contrasting regression cases cover both paths. The real TOPIK 5 run exposed this
gap with 불평하다; completed sibling chunks are preserved on resume. TOPIK 6 was
also resumed with three concurrent workers instead of one, without changing its
chunk partition, source coverage, or independent review gates.

Large supplemental lexical requests are split into eight-headword model jobs,
with at most three batches and three primary HTTP requests running concurrently.
This is a research job budget, not a prose or source quota. Every batch keeps the
same creator/primary-record/independent-review gates; approved batches promote
separately and are reusable if another fails. All sibling jobs finish before a
failure propagates, preventing overlapping retries. Regression coverage verifies
complete request coverage, bounded concurrency, and sibling draining on failure.

Homonym coverage follow-up: supplemental research previously treated any reviewed
candidate for a spelling as sufficient. Actual advanced runs exposed absent
merit/service 공 and calming 진정하다 beside unrelated reviewed homonyms. Failed
annotation identities now carry their observed form and contextual meaning as
unverified search requests. Research and independent review must check this
requested usage; reuse binds the exact reviewed occurrence scope rather than
spelling alone. Scope digests detect changed research context. Contrasting
regressions verify that a different usage triggers research/review while the same
reviewed request reuses its evidence. No candidate gloss becomes attestation,
curriculum grade, or passage-specific dictionary definition.

Primary-retrieval follow-up: the usage researcher reported four entries unresolved
because its browser could not open their public Standard Dictionary URLs, despite
supplied readable direct-content snapshots. Direct retrieval verified records
28591, 436250, 313043 and 489985 again. Creator and critic instructions now
distinguish direct supplied record content from a search snippet or a failed web
tool request. A proposed identity still undergoes exact primary-record retrieval
and independent review. The evidence regression covers a readable Standard
snapshot reaching both stages without relying on browser access.

The contextual lexical follow-up approved the missing merit 공, complaining
불평하다, calming 진정하다 and concubine 첩 identities, alongside separately
attested homonyms. The shared 가지다 entry also underwent primary research and
independent editorial review for its pregnancy sense. Coverage included both
published chapters and the TOPIK 4 draft: one draft occurrence, no published
occurrences. Its existing meanings remain intact. Both published edition proofs
still validate; publication refresh propagates the shared definition to assets.

The retained smoke rebuild now uses the complete Korean level map so it preserves
every enabled published edition instead of rejecting metadata after TOPIK 2 was
added. It still checks the reviewed beginner pilot. Publication regression
coverage verifies tap reconstruction for all enabled editions and contrasts a
single-edition catalog with the real multi-edition catalog; rebuild coverage
also verifies that no published chapter or annotation asset is dropped.

A real Standard Dictionary response ended mid-transfer during TOPIK 4 research.
Primary retrieval now retries one HTTP interruption before reporting an explicit
retrieval error. Previously completed record snapshots remain available, but a
truncated response is never used as evidence. Contrasting regression cases cover
recovery on the second request and bounded failure after two interrupted reads.

TOPIK 3's complete curriculum proposal exceeded the ten-minute model limit on
both attempts. Larger higher-edition binding requests now use sixteen-identity
offline jobs under the existing worker concurrency, followed by a whole-chapter
assessment and the same independent curriculum review. Local computation still
requires every ordinary vocabulary and grammar identity, including standalone
grammar taps. A model repair plan selects affected identities so unchanged
binding batches remain reusable. This budgets model jobs, not chapter length or
optional grammar. Publication replays every batch and assessment from completed
offline worker evidence and rejects altered outputs or forbidden tool use.
Regression coverage includes complete binding assembly and publication replay,
with contrasting tampered-content and tool-use failures.

TOPIK 5 exposed another repeated repair: an audit declared an inflected tap but
provided no stages, while the validator reported only an incomplete review.
Diagnostics now identify exact indices/surfaces both for declared inflections
without stages and for stages missing from the audit. They require actual form
review instead of omitting inflections or inventing intermediate forms. Paired
regressions cover each mismatch direction; data and tap boundaries are unchanged.
