# Korean pipeline coverage and remaining differences

Scope inspected: source/manifests, generation harnesses, dictionary editorial
jobs, publication validators, form and sentence-help layers, and app services/UI
for Chinese *Three Kingdoms*, Japanese *I Am a Cat*, and Korean *Hong Gildong*.
Korean currently publishes only Level 1 chapter 1. This does not claim full
feature parity or TOPIK calibration.

| Layer | Korean implementation | Remaining scope |
| --- | --- | --- |
| Source and adaptation | Complete searchable Wikisource/Jikji original pinned to revision 460078, raw/extracted checksums, Unicode paragraph spans, reviewed source notes and lexical identity plan, reviewed source plan and independently reviewed modern prose. The placeholder 某 in the father's name has an edition-specific scholarly reference; it is not treated as a literal personal name. | Only two source units mapped; later units and other levels require mapping and review. The archived 24-sheet scan is a different edition. |
| Annotation and difficulty | Independently reviewed exact taps, meanings and explicit NIKL identities. Reconstruction, lexical budget, story-term budget and sentence length gate publication. Unannotatable ordinary words send unpublished prose back for simplification. | NIKL A/B/C is a vocabulary baseline, not TOPIK. Sentence length is a coarse screen alongside independent beginner review. |
| Dictionary editing | Approved reusable entries remain immutable. Only missing word/grammar IDs go to a proposer and independent reviewer. Exact source positions keep repeated uses separate. | No Korean component graph or multi-sense editorial workflow yet. Meaning uncertainty must remain an editorial issue. |
| Form and construction explanations | Complete-form meanings and separate form labels, exact reviewed per-stage grammar routes; base and transformation rows carry their links without a duplicate list. Wider constructions have exact phrase rows with their full meaning. | No independently editable merged reader-unit layer or Japanese-style merged construction chains. Korean displays annotation taps directly. |
| Sentence help | Every sentence is inventoried and independently reviewed for selection; selected parts align to taps, while simple sentences remain unselected. | Selection is a reviewed judgment, not an automatic rule based only on length. |
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

Verified publication (2026-10-01): chapter 1 was generated from the pinned text
and passed all six independent review stages. All 31 ordinary vocabulary
occurrences are NIKL A; no story-term exemption was needed. The longest sentence
has eight eojeol; three of six sentences have selected help. Publication retains
30 reusable word entries and 21 grammar entries, including unused approved
pilot entries. A completed-run rerun made zero model calls with unchanged job
metadata. Validation passed 47 Python checks, 357 Flutter tests, Flutter analysis
and a release web build. Playwright checked the rebuilt Chrome preview at
390×844, dictionary/base meanings, stage grammar destinations and selected
sentence help.
