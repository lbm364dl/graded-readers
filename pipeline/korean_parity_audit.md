# Korean pipeline coverage and remaining differences

Scope inspected: source/manifests, generation harnesses, dictionary editorial
jobs, publication validators, form and sentence-help layers, and app services/UI
for Chinese *Three Kingdoms*, Japanese *I Am a Cat*, and Korean *Hong Gildong*.
Korean currently publishes only Level 1 chapter 1. This does not claim full
feature parity or TOPIK calibration.

| Layer | Korean implementation | Remaining scope |
| --- | --- | --- |
| Source and adaptation | Complete searchable Wikisource/Jikji original pinned to revision 460078, raw/extracted checksums, Unicode paragraph spans, reviewed source notes and lexical identity plan, reviewed source plan and independently reviewed modern prose. The agent selects a contiguous source span and a coherent stopping paragraph from the remaining narrative, with reviewed coverage/omission reasoning. Total chapter length has no numeric quota. The placeholder 某 in the father's name has an edition-specific scholarly reference; it is not treated as a literal personal name. | Source-boundary decisions still require independent review; the two manually mapped scenes are reference examples. Other levels require their own learner policy. The archived 24-sheet scan is a different edition. |
| Annotation and difficulty | Independently reviewed exact taps, meanings and explicit NIKL identities. Reconstruction, lexical budget and story-term budget gate publication. Unannotatable ordinary words send unpublished prose back for simplification. Sentence length is reported for review rather than mechanically rejected. | NIKL A/B/C is a vocabulary baseline, not TOPIK. Linguistic review judges sentence difficulty and selective help. |
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
