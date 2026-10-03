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

A TOPIK 4 reviewer questioned 가까이하다's interpersonal scope. Targeted primary
research and independent review confirmed coverage by the existing krdict-28056
verb identity, without proposing a duplicate lemma. Annotation critics now
receive matching reviewed usage investigations, including outcomes that correctly
refer an attested usage back to an existing identity. Scope remains explicit:
different form/meaning requests do not inherit the investigation, unresolved
other usages stay unresolved, and these records never assign curriculum grades
or become learner-facing process notes. Empty evidence is omitted to preserve
unaffected review-cache inputs. Regression checks contrast matching versus
different usage requests.

The next shared editorial review covered future-time 앞, relational 가까이 and
habitual auxiliary 하다 across both published editions and all three supplied
TOPIK 3–5 drafts (31 distinct occurrences). Primary references and the independent
approval are stored with the shared dictionary revision; existing senses and IDs
are preserved. Republishing exposed an older-run conflict: accepted dictionary
deltas still contained the original definitions. Publication now replays validated
editorial history, recognizes those exact reviewed predecessors, and keeps their
approved successors. Historical dictionary proof digests remain valid only when
they match a verified registry version; grammar changes, arbitrary definitions,
and tampered revision approvals remain rejected. Regression coverage checks both
reuse and these contrasting failures. No chapter prose or tap boundaries changed.

The TOPIK 4 curriculum pass detected an extra-vocabulary ratio of 10.9%, above
the existing 10% gate, and initiated source-preserving prose repair. Checking
that repair path exposed a stale eight-attempt search limit for reusable
annotation assemblies. Recovery now searches every completed assembly, requires
the exact prior prose when invoked during repair, and retains its review issues
for the reuse agent. Regression coverage includes an assembly at attempt ten
and contrasts a different prose text that must not be reused. No active worker
was restarted merely to load this cache improvement.

Contextual research for TOPIK 5's harm/misfortune 화 exposed incomplete Standard
Dictionary retrieval: its public GET search returned ten results despite a
requested pageSize of twenty, and the needed homonym was on page two. Retrieval
now follows the actual pagination links, deduplicates record IDs across pages,
and retains completed primary snapshots if a later page fails. Regressions
contrast a single-page response, a later-page homonym with a duplicate earlier
record, and an interrupted later page. Research still requires exact-record
retrieval and independent lexical review; pagination supplies candidates only.

With both result pages retrieved, independent primary research approved
stdict-379255/명 for 화 (禍), calamity or misfortune. The existing 화06/명 anger
identity remains separate. The new candidate has no inferred curriculum grade;
its passage binding must still be checked against the six-level source and its
annotation reviewed. The investigation also excludes -화 suffix records and
화하다 roots from the standalone noun request.

Parallel primary research can finish after a long-running annotator captures
its candidate list. TOPIK 5 retried its unresolved calamity tap while the shared
registry already contained the reviewed identity. Annotation now reloads the
validated catalog before generating and validating each batch, supplying newly
available candidates for its requested headwords without repeating the original
list. End-to-end regressions contrast a stale snapshot that now accepts the
current reviewed identity with an invented ID that still fails, and verify the
repair worker receives the current exact IDs. This changes candidate availability,
not linguistic approval or curriculum grading.

Targeted primary research approved 넉넉히, 이상히 and the pavilion 정자 identity
(krdict-75888/명), along with separately attested homonyms. 병법책 remained
unresolved as a standalone dictionary headword. Its military-strategy 병법 and
책 components already have independently attested identities. Headword planning
and annotation instructions now distinguish a missing transparent noun compound
from a missing independent lexeme: an unapproved annotation may choose adjacent
component taps without adding spaces or rewriting source text. Attested whole
words remain preferred, idioms retain their combined meanings, and uncertain
component contributions still require investigation. A regression verifies exact
병법책을 reconstruction using its reviewed component identities and contrasts
an invented whole-compound identity that must fail. TOPIK 6 was resumed from its
cached proposals to load this guidance and the newly reviewed lexical evidence.

The revised TOPIK 3 annotation stopped on an incomplete extra ability link at
an inflected phrase ending, although the same complete construction was already
anchored earlier in the sentence. The validator now identifies the exact tap and
reports source-aligned complete rows for the same grammar identity covering that
position. It asks the annotator to retain the original phrase and remove a
redundant ending link only when they represent the same construction; distinct
constructions still need their own full fields. Nothing is deleted automatically
and validation remains strict. Contrasting regressions check a matching covering
lesson versus a different lesson that must not receive that deduplication advice.
The stopped run resumed from its cached plan, prose and completed proposals.

The revised TOPIK 4 run stopped after requesting a separate 대우받다 dictionary
identity. Its earlier independently approved annotation already used lexical
대우 with complete productive grammar stages. Missing-identity diagnostics and
annotation instructions now explicitly permit an attested noun base for such
grammatical formations, retaining complete meanings and separate transformation
destinations rather than inventing whole-formation lemmas. A regression exercises
that existing analysis through canonicalization and asset validation, contrasting
an invented 대우받다 identity that remains rejected. The stopped run resumed
from cache; no approved dictionary definitions or source prose were rewritten.

Unresolved lexical research can cite an expression under another dictionary
headword without proposing a new lemma. Those cited direct primary records now
reach the offline independent reviewer too; previously only proposed entries'
URLs were retrieved. Retrieval accepts exact official record URLs, not arbitrary
external claims or search pages. Regression coverage checks this evidence handoff
and the contrasting URLs that must not be retrieved.

Curriculum generation and independent review now receive matching, independently
reviewed occurrence investigations, as annotation review already did. Evidence
is scoped to requested vocabulary identities and exact observed forms/meanings;
names, unrelated batches and different senses do not inherit approval. It supplies
no curriculum grade. This repairs repeated requests to reinvestigate the already
reviewed interpersonal use of 가까이하다; unrelated mappings remain cached.

Repair triage now receives the exact approved word-ID set. Only those definitions
may be sent to shared dictionary revision. Catalog candidates and requested new
entries are distinct: uncertain occurrence senses need primary lexical research,
and new reusable definitions remain the later dictionary editor's responsibility.
Regressions exercise repair recovery with both approved IDs and candidates outside
that set. The TOPIK 5 run resumed after incorrectly selecting unapproved 놓다 and
태우다 entries for dictionary revision.

Targeted review of the current TOPIK 4 and 5 drafts verified 걸리다's mind-bothering
sense, 놓다's worry/tension-easing sense and 태우다's anxiety-causing sense under
their existing identities. The distinct adverb use of 본래 received primary
identity stdict-429486/부; its noun identity remains separate. Actual direct record
text and independent approval are stored in the supplemental registry. These are
lexical findings, not approved chapter annotations or curriculum grades. Final
review and publication of the advanced drafts remain required.

The advanced drafts need substantially larger new dictionary deltas than the
published beginner editions (the checked TOPIK 5 proposal requested 229 new word
definitions and 70 grammar lessons). New deltas above 32 entries now use bounded
24-entry drafting jobs under the existing worker limit. This bounds agent work,
not chapter length or learner content. Completed jobs are cached, repairs select
only affected identities and preserve unrelated definitions even within a selected
batch. The assembled full delta still goes through the existing independent
dictionary review. Matching reviewed lexical usage evidence reaches both drafting
and final review without becoming a grade or passage-specific definition.

Publication replays every dictionary child job, verifies its digest and offline
tool profile, and compares the assembled delta to the reviewed proposal. Tests
cover full requested identity coverage, local identity failures without redrafting
successful siblings, focused review repairs, altered worker definitions, tool use,
changed repair plans and the actual publication verifier. Legacy completed
dictionary reviews and small deltas retain their original paths.

TOPIK 6 revealed a source-attested place name, 운봉산, omitted by the pre-prose
lexical plan. Annotators correctly refused to assign it another person's ID or
invent a name exemption. The mismatch diagnostic now identifies the exact tap,
headword, ID and kind. A missing planned name ends that local repair immediately;
after sibling jobs drain, the pipeline can perform one bounded cached recovery.
Only completed offline proposals matching current prose chunks supply unverified
name requests. A source-grounded lexical-plan completion and independent review
decide whether to add named people or places, preserving all existing entries,
aliases and roles exactly. Ordinary vocabulary and story terms are not converted
into new exemptions. Prose and unrelated completed annotation jobs stay cached.

The actual TOPIK 6 coverage completion passed independent review. Regression
coverage contrasts current prose with stale chunks, completed with failed workers,
offline with tool-using proposals, missing names with already planned aliases,
and name-recovery errors with unrelated dictionary errors. Existing roles cannot
be overwritten and reviewer rejection cannot become lexical approval. No claim
of final TOPIK 6 annotation or publication approval follows from this plan repair.

The assembled TOPIK 6 annotation exceeded the model's 1,048,576-character input
limit during grammar identity coordination. Large annotation inputs now use a
lossless row format with explicit column names and original segment indices.
Every lexical field, complete form stage, reading, grammar context, full phrase,
ending index and inflection audit position is retained. The stored artifacts
remain unchanged. Both coordination and independent whole-annotation review can
use this format; smaller inputs keep their existing representation and caches.

The actual approved-prose draft contains 3,477 segments and 174 new grammar
identities. Its coordination payload decreased from 1,150,133 to 537,697
characters without sampling or shortening. All 32 completed annotation batches
were recovered on resume. A regression reconstructs every field and occurrence
from the packed view, proves no mutation, and contrasts the unchanged small-input
path. The relevant 120-test suite passed. Final semantic review is still required.

## TOPIK 5 publication and structured expression links

TOPIK 5 chapter 1 passed all seven independent gates and publication replay:
2,732 characters, source paragraphs 0–23, 1,347 segments and 14 selected sentence
breakdowns. The exceptional vocabulary ratio is 0.07249. Published levels are
currently 1, 2 and 5, sharing 441 word entries and 167 grammar entries. The
reviewed self-directed-judgment sense of 알다 was audited across 13 published and
draft occurrences before promotion; approved older dictionary evidence remains
verifiable through the revision ledger.

TOPIK 4 review exposed a missing shared representation for idiomatic lexical
expressions. Fresh annotations now include expression_links with exact component
spans, a component's attested lexical identity, complete idiomatic meaning and
passage context. Word identities are rejected in grammar_links. App expression
cards open the lexical entry while preserving the original component taps.
Legacy annotations remain readable. Existing published annotations have not been
retroactively given expression spans; the new layer is available to the active
chapter-generation and repair runs. Regression coverage contrasts valid spans
with incorrect text, unrelated word identities and invalid ranges.

Dictionary examples now group annotation layers by source and sentence position,
keeping their useful notes together. Identical sentences at distinct positions
remain separate. Closing quotes use the same source sentence inventory as
sentence help rather than leaking into the next example. Quoted, unquoted and
repeated-position cases are covered without changing source text or tap bounds.

The complete-form UI merges identical single-tap form/meaning rows into a single
displayed stage while retaining both grammar destinations. Different meanings
and wider constructions remain separate. All nine Korean Flutter tests passed;
the actual mobile Chrome preview showed 살다 → 살았다 (“lived”) and a separate
English Plain statement lesson button, which opened its correct lesson.

TOPIK 6 reuse planning also exceeded the model input limit. Its candidate
annotations now use the same lossless row format as whole reviews; no fields or
occurrences are dropped. The real resumed run recovered 10 of 32 batches after
independent selection. Seven targeted reuse/packing tests passed. The remaining
batches require fresh work because of review findings; final approval is pending.

Annotation repairs now receive independently reviewed usage investigations for
their previous exact forms, retaining the original requested meanings and an
explicit related-form warning requiring independent contextual assessment. Whole
annotation reviewers receive the same research context and must independently
assess its applicability. Curriculum stages still require exact form-and-meaning
matches.
This avoids asking a repair worker to resolve an investigation whose approved
outcome was omitted from its inputs. Fresh chunks without occurrence evidence and
unmatched forms receive no such evidence. No spelling match approves a new sense.
Four repair regression cases passed,
contrasting known evidence, absent evidence and already recovered proposals.

## Shared worker configuration

All pipeline execution now uses gpt-6-luna with low reasoning, including
independent reviewers, lexical research and dictionary revisions. The shared
CodexRunner enforces the repository policy even for older callers requesting a
different model or effort. Korean defaults and stage requests also state this
policy explicitly. Runtime command inspection confirmed Luna/low for the resumed
TOPIK 3, 4 and 6 workers. Exact-context cache-only evidence may retain its original
model provenance; changed prompts still miss the cache. A model change alone is
not evidence that reviewed content needs regeneration.

Luna planning retries exposed a draft/continuation ambiguity: a rejected prior
plan was treated as a completed chapter, skipping its source prefix. Instructions
now explicitly distinguish repair proposals from published previous_chapters.
Planning context includes chapter_number and the authoritative source_start.
Two regression cases verify the opening chapter against a real published
continuation, including the exact remaining source text. Chapter length still
depends on reviewed source coverage and learner difficulty, with no quotas.

Source planning previously transmitted both the entire raw original and its
numbered paragraphs. It now sends the numbered paragraphs once, plus the source
hash and authoritative offsets. Review-task deduplication removes the same
paragraph array from duplicate task inputs. The first-chapter and continuation
tests reconstruct the exact original from every retained paragraph, verify its
hash, and prove the review receives one copy. Later stages still receive their
selected source normally; no narrative is sampled or truncated.

Repeated source-plan reviews supplied contradictory participant identities,
rejected faithful paraphrases as nonliteral, requested lexical-plan fields in the
source-plan contract and demanded extension merely because more source remained.
Plan objections now receive a separate independent adjudication against the same
complete source and stage task before another proposal is commissioned. Genuine
errors remain rejection grounds; unsupported objections do not force rewrites.
Both primary and adjudicated decisions are retained with digests and offline
worker provenance. Publication validates their lineage and rejects changed
primary objections. Regression cases distinguish unsupported objections from
material corrections and intact publication evidence from tampered evidence.

Instruction wording changes exposed a separate provenance problem: already
reviewed published chapters were rejected solely because the current generation
instructions had changed. The original approved policy is retained as an exact
SHA-256-named snapshot recovered from version control, matching all three
published editions' evidence. Publication can verify that historical policy;
unknown versions and altered snapshots fail. Source, chapter, dictionary,
breakdown, curriculum and independent-review evidence checks remain mandatory.
New promotions retain their exact current policy snapshot. Review prompts also
name the actual TOPIK target explicitly rather than relying on replacing the
old beginner-only wording. These changes do not rerun unrelated dictionary
research or change historical content.

### Lexical planning objection adjudication (2026-10-02)

TOPIK 3 exposed a reviewer request to rename the approved hong-gildong identity, which local validation correctly rejected. Lexical planning now independently adjudicates objections before repair, preserving registry identities and supported homonymous aliases while rejecting invented names and omissions. Publication binds both reviews and detects altered original objections. The shared stage and publication tests cover supported versus unsupported objections for both source and lexical planning; 133 pipeline/publication tests passed. Levels 3, 4 and 6 remain in progress; this does not claim completed pilot coverage.

Prose objections now receive the same independent adjudication: the beginner vocabulary core is not a TOPIK 4 ceiling, faithful adaptation is not verbatim transcription, and actual source errors, invented actions, lost causality or excessive difficulty still require repair. Both decisions remain publication evidence.

### Reviewed source continuity guidance (2026-10-02)

Repeated reviews confused the woman killed in paragraphs 65–67 with Chonan. The exact original paragraphs 66, 68, 86 and 88 establish that they are distinct. GPT-6 Luna low independently approved a source-bound finding. The shared source-context loader verifies its source hash, paragraph quotations, exact reviewed input fingerprint, completed offline review and approval before exposing it to all chapter stages. This is internal evidence, not learner-facing dictionary commentary. Six regressions cover valid guidance and changed findings, rejected/incomplete reviews, altered source and quotations. The broad suite passed 140 cases; three existing prose-review mocks needed the new adjudication call and were repaired, retaining rejection of real errors. No unfinished chapter is claimed published.

New reports and publication evidence bind the exact source-context file hash, captured when the run reads it. Altered guidance invalidates those approvals; legacy published chapters that did not use this guidance retain their original evidence. Valid versus changed guidance publication tests passed. The complete source-context/pipeline/publication suite now passes 145 tests. TOPIK 6 source planning passed with the reviewed guidance; the chapter remains unfinished.

A second independently approved source-context record links the established 좌랑 인형 to later 좌랑, confirms the siblings' shared father and distinct mothers, and preserves Chonan's fear as Gildong's inference in paragraph 22. Earlier guidance remains an exact hash-addressed snapshot for unchanged approvals. Historical snapshot verification and tampered-snapshot rejection are covered; seven source-context tests and two publication guidance tests pass. TOPIK 4 resumed with both records after its terminal prose rejection.

Shared stage repair prompts now explicitly retain unaffected source events, identities, aliases, wording and narrative boundaries. Missing development is restored within the reviewed span instead of replacing correct passages. The prose rejection regression contrasts initial generation with a scoped repair; genuine review objections still require repair and independent approval.

Integration checks exposed missing direct guidance inputs: prose and annotation producers, plus the annotation critic, now receive reviewed source-context findings. Prose treats this as interpretation/identity reference rather than additional chapter events. Preparation and curriculum checkpoint regressions verify that exact reviewed findings reach these stages; both pass. Detached processes 3, 4 and 6 were confirmed running after session-attached processes stopped together.

Targeted lexical research now receives the chapter prose as unverified usage context and explicitly avoids exhaustive homonym enrichment. Reviewers retain distinct identities among proposed entries and require complete requested-spelling coverage, but unrelated dictionary homonyms are not mandatory. Conjugated requests are unresolved rather than answered with unrelated noun homonyms. Direct primary records still establish every accepted identity and reusable definition. Source-context digests detect edited research inputs; 21 lexical-research tests pass, including valid versus altered contextual evidence. Existing approved lexical records remain reusable.

Complete supplied direct-record snapshots now allow an offline lexical editor rather than a redundant mandatory web search. Missing/unreadable record coverage still selects research capability; every proposed identity still gets direct proposal-record retrieval and an independent offline critic. Both capability branches and the remaining research regressions pass (23 tests). Levels 3 and 4 resumed after terminal tool-profile failures, with targeted passage lookup and reviewed source guidance.

Initial lexical planning now includes genuinely named people, places and works in the reviewed source span, matching the existing later coverage-completion contract. Generic locations and unnamed roles remain ordinary vocabulary. This addresses place names such as 운봉산 being sent to lexical research before the first annotation pass. The preparation checkpoint verifies both named-entity coverage and the contrasting generic-role restriction. Existing cached missing-name completion continues to preserve reviewed IDs and aliases.

Prose instructions now distinguish ordinary extra vocabulary from historical story exemptions. Sparing higher-level/unlisted words retain honest identities and grades and count toward the separate curriculum budget; catalog absence alone is not rejection. The preparation regression checks this distinction alongside interpretation-only source guidance. Lexical critics also receive the already-validated POS/ID contract and distinguish material sense errors from equivalent concise English glosses. Five focused preparation/research tests passed.

Unverified lexical requests receive a focused offline triage before dictionary retrieval: normalize inflected lexical bases, exclude names and productive phrase frames, and preserve potentially attested compounds and idioms. These remain search requests, not approved entries or grades. The curriculum checkpoint verifies both the correction and the contrasting compound-preservation instruction. Prose criticism now carries the actual approved source-plan stage evidence and uses that independently reviewed interpretation and boundary as its baseline; concrete source contradictions can still be flagged with the affected planned event. It does not reopen every source-planning omission or impose shorter-chapter quotas. Approval-present versus absent and preparation/curriculum regressions pass (four tests).

### Exact research evidence and segment-attached annotations (2026-10-02)

Research batches now resume from their latest rejected proposal and adjudicated repair requirements, rather than replaying the same bounded failures. Missing required web-search use gets one explicit retry without weakening the tool-profile gate. Complete supplied direct records still use an offline editor. Independent objection adjudication preserves real record/sense errors and dismisses demands for unrelated homonyms or fabricated standalone phrases. Its original review is retained with a verified digest.

Standard Dictionary retrieval exposes the exact record number, displayed headword and written headword separately from examples. Internal compound hyphens are structural markers; actual spaces and leading/trailing affix markers remain distinct. Exact headword mismatches are rejected before model review, while unknown HTML layouts retain the original primary text for independent review. Phrase requests also receive supplied catalog candidates for exact component spellings; this does not infer stems, attest an idiom or create a phrase lemma. The lexical-research suite covers these distinctions and passes 37 tests.

Canonical IDs accidentally placed in headword fields now produce a field-repair diagnostic using explicit catalog identities instead of triggering dictionary research. Invalid grammar occurrence diagnostics show nearby indexed source segments, including punctuation, without changing text or taps. Approved source-interpretation findings are explicit review authority for their resolved passages; they are not reopened as competing interpretations at each prose attempt.

New chunk workers use `segment-anchored-annotation-v1`: grammar and lexical-expression links are attached directly to their first included word segment. The pipeline computes numeric anchors and resolves complete-form endpoints only at exact existing segment boundaries. The published annotation/app format remains unchanged. Legacy indexed chunks remain readable; new chunks retain both raw-worker and decoded-output digests. Publication replays decoding and rejects altered raw output. Regression coverage includes complete annotation round trips, spans across words and spaces, mid-tap/nonmatching forms, punctuation anchors, resumed-worker repairs and publication tampering.

Compatibility coverage checked: the real published TOPIK 1, 2 and 5 runs pass publication replay with the new decoder. TOPIK 3, 4 and 6 remain unfinished and are not claimed published. No app text or unrelated Japanese work was modified for these pipeline changes.

The first live TOPIK 3 attached-format pass exposed links placed on a grammatical ending despite displaying a phrase beginning earlier. Worker instructions now explicitly distinguish the phrase's start from its grammar-bearing final word, with whole-phrase and single-word examples. Three contrasting regressions preserve taps and accept only the appropriate anchor; all ten decoder tests pass. Existing invalid proposals are rejected for agent repair, never silently retargeted. Already running processes retain their loaded instructions until a normal resume.

The comparable TOPIK 4 pass still produced misplaced links. Rejection diagnostics now list every exact existing start segment for the supplied complete form. Repeated forms remain separate candidates for the worker to judge; no candidate is selected automatically. Missing and mid-tap forms yield no candidates. Eleven decoder regressions pass, including duplicate source positions, unsupported forms and unchanged source segments. This is repair evidence, not linguistic approval or automatic normalization.

### Contained-span worker protocol (2026-10-03)

Live results showed that first-word attachment itself was a recurring worker-format failure. New workers use `segment-contained-annotation-v2`: a construction or expression link may attach to any included word in its explicit complete source form. Decoding requires exactly one matching span at existing tap boundaries containing that attachment, then emits the existing published first/last indices. A match elsewhere is not sufficient; overlapping matches are rejected. Meanings, identities, source characters and taps are never inferred or rewritten. Step-owned links retain their own segment and empty display fields.

V1 remains strict and retains its separate schema file. No cached V1 proposal is relabeled or silently repaired. Both protocols retain raw and decoded digests and publication replay. Twenty decoder regressions cover first/final attachment, missing and mid-tap forms, distinct repeated positions, ambiguous overlaps, legacy rejection and tampering for both protocols. Real published TOPIK 1, 2 and 5 still pass publication replay. TOPIK 3, 4 and 6 remain unfinished; active older workers keep their current protocol until normal resume.

Resume now retains schema-valid rejected span/reconstruction proposals as explicit repair context after verifying successful offline worker provenance. They are never returned as reusable occurrences: a new worker output must pass reconstruction, identities, dictionary validation and the whole-chapter review. Malformed or tool-profile-rejected proposals remain excluded. Eight scoped repair/reuse regressions and all twenty decoder tests pass, including an invalid attached form forwarded with its exact rejection while a valid sibling is reused. TOPIK 3 resumed after its terminal punctuation-loss failure with these improvements.

Measured annotation worker metadata showed 78,281–90,663 input tokens for roughly 240-character chunks, with sampled jobs taking 14–25 minutes. Approved word and grammar references now use explicit shared-column rows, retaining every field and complete value. The full published word-reference JSON falls from 59,208 to 40,348 characters (31.9%); grammar references fall from 62,840 to 55,098 (12.3%). Review input deduplication recognizes the same lossless references under existing context aliases, while retaining changed meanings and distinct empty roles. Uniform-field checks reject omissions rather than silently discard data. Twelve focused reference/review and repair/reuse tests pass. This measures reference size reduction, not a proven end-to-end runtime improvement; existing workers keep their loaded prompt until resume.


### Exact paragraph-whitespace repair guidance (2026-10-03)

The TOPIK 3 chunk repair repeatedly replaced a source space with paragraph breaks. The shared reconstruction gate now identifies the exact differing whitespace runs and tells the worker which source run to copy. Chunk instructions explicitly distinguish source paragraph boundaries from a rejected proposal. No text is automatically changed and the strict reconstruction gate remains intact. Three reconstruction tests cover missing tails, changed whitespace, changed words, extra tails and exact reconstruction. Published TOPIK 1, 2 and 5 were previously checked by publication replay; this change supplies diagnostics only and does not repair published content. TOPIK 3, 4 and 6 remain unpublished pending annotation and independent review; effectiveness of the new guidance is not yet verified on those runs.
