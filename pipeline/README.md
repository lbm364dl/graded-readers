# Graded Reader Pipeline

## Korean TOPIK 1–6 pipeline

The generation scope is *Hong Gildong*, TOPIK 1–6, using the pinned searchable
Wikisource/Jikji 30-sheet original. The planning agent chooses a contiguous
source span and a coherent stopping paragraph from the remaining narrative;
the two mapped reference scenes are not chapter limits. Publication currently
contains chapter 1 at TOPIK 1, 2 and 5; other editions require their own real
generation and independent reviews before publication. The baseline is the six-level NIKL International Standard
Curriculum (2017, corrected 2020-11-17), aligned with TOPIK levels rather than an
exhaustive exam word list. The earlier A/B/C catalog preserves lexical IDs only.
Ordinary extra vocabulary retains its source grade and the existing 10% budget.
A few higher-level grammar patterns may be reviewed as optional learning, with
true grades, per-pattern rationales and a whole-chapter difficulty assessment.
Review considers frequency, variety, complexity and narrative need without a
fixed grammar quota; an unsuitable chapter returns to prose revision.
Unlisted grammar keeps a null grade and an explicit optional rationale; it is
never assigned an invented upper level. Unlisted ordinary words count toward
the extra-vocabulary budget. Vocabulary guide phrases illustrate a lexical
identity rather than excluding its other independently verified senses; grammar
source meanings still distinguish functions.

```sh
.venv/bin/python -m pipeline.korean_sources
.venv/bin/python -m pipeline.korean_agent_harness --chapter 1 --run-dir runs/korean-topik1/chapter-001
.venv/bin/python -m pipeline.korean_publication --run-dir runs/korean-topik1 --check
.venv/bin/python -m pipeline.korean_publication --run-dir runs/korean-topik1
# Generate and validate the next edition, preserving already published levels.
.venv/bin/python -m pipeline.korean_agent_harness --level 2 --chapter 1 --run-dir runs/korean-topik2/chapter-001
.venv/bin/python -m pipeline.korean_publication --run-dir runs/korean-topik2 --check
.venv/bin/python -m pipeline.korean_publication --run-dir runs/korean-topik2
```

`--level` accepts 1–6 and defaults to 1. Each edition chooses its source scope,
prose register and selective sentence help for that target. Higher-edition
instructions are in `korean_level_instructions.md`; their fingerprint and the
explicit target are checked in publication evidence. Do not relabel beginner
prose or force increasing chapter lengths. Curriculum review retrieves the actual
word candidates and prerequisites rather than transmitting all 10,635 words.
The approved dictionary registry remains shared under its historical `l1.*`
filenames. Publication preserves other editions and combines all published
source routes in the app dictionary and sentence-help assets.

Use `--stop-after prose` to prepare an edition through independent source,
lexical-plan and prose reviews while another edition finishes annotation. This
writes `preparation.json`, not a publication report. Resume without that flag;
the normal cache verifies reusable reviews, and annotation reads the then-current
shared dictionary. A prepared draft cannot pass publication.
`--stop-after curriculum` also completes annotation and curriculum review while
deferring dictionary editing and sentence help. This lets editions share newly
approved dictionary entries before final completion; resume without the flag.

`--annotation-batch-characters 240` groups adjacent complete sentences into
model jobs to reduce repeated calls. This controls job size, never chapter length:
an overlong sentence stays intact, and every character and separator is preserved.
The default zero keeps one-sentence jobs, including existing run evidence.
Assembly records the grouping budget; publication replays the exact partition.
Repairs select affected groups and retain unaffected ones through the same
digest-checked assembly mechanism.

Before annotating higher editions, a lightweight proposal lists headwords from
the actual prose. Exact lookup retrieves all matching legacy/source identities
and POS candidates, without assigning grades or approving meanings. The
annotator selects actual uses and the independent annotation/curriculum reviews
verify them. Higher editions receive these candidates rather than the entire
beginner identity list; matching approved definitions and planned names are
reused. Distinct complete-form stages are required. A grammatical role that
does not change the form stays in a complete-phrase grammar link, rather than
duplicating a stage or inventing an incomplete stem.

The harness proposes a source plan, reviewed name/story identities, target-level prose,
exact tap annotations, curriculum bindings, missing dictionary entries and selective sentence help. Each of the seven stages has an
independent review and up to seven repairs after the first attempt.
After a restart, the harness recovers saved annotation assemblies and asks the
reuse agent to approve exact unchanged occurrences, preserving unresolved issues
and distinct positions. Selected chunk digests must still match. Curriculum
search includes possible compound prerequisites as candidates; it never grades
a derived word from spelling alone.
If a process stops before writing its parent assembly, locally valid completed
worker proposals can also be recovered for the same exact chunk text. Completion,
offline tool use, lexical identities and complete-form routes are checked again.
Invalid proposals guide fresh repair jobs; rejected tool outputs are skipped.
Recovery never supplies independent approval: the assembled chapter still passes
the normal full review, and known reviewer objections prevent this shortcut.
Chapter length has no numeric quota. Source planning records the coverage and
omission decisions; prose records why its extent and stopping point suit the
level. Independent review checks both avoidable compression and padding. The
source spans are adaptation divisions, not original chapters. Publication
requires successive chapters to start immediately after the preceding span.
Annotation
proposals use sentence chunks with exact reconstruction checks.
Each chunk validates exact lexical identities and form routes locally, then
passes independent occurrence review before that worker slot takes another chunk.
Repairs resend the failed chunk while retaining valid siblings. Proposed new
grammar functions are reviewed as occurrences; their reusable lessons still need
chapter-wide identity coordination and independent dictionary review.
Four annotation jobs run concurrently by default (`--workers` changes this).
An independent review failure is mapped to affected sentence chunks before
repair; unrelated chunks retain their original model evidence. After a prose
revision, an agent selects exact unchanged occurrences whose contextual analysis
remains valid, excluding unresolved errors. Reuse preserves distinct source
positions and is checked again during publication.
Triage explicitly refers unsuitable wording to prose review; annotation and
technical errors stay in annotation repair. Spacing-unit sentence length is
reported for linguistic review and selective help, with no automatic length
rejection. Vocabulary and reviewed-identity checks remain enforced.
Reviewed linguistic references in `data/korean/linguistic-reference.json` are
supplied to linguistic review and annotation repair, separately from story
source notes. Their fingerprint is retained in publication evidence. Spelling
and pronunciation are distinct; a phonetic sound change does not by itself
justify a written conjugation rule. Restarts resume the latest matching review
findings; changed context prompts a fresh review of the latest valid proposal.
New grammar IDs from separate chunks are coordinated through explicit bindings
to existing or canonical draft identities; the mapped complete chapter receives
an independent review. Publication replays chunk assembly and those bindings and
checks the underlying completed model jobs. It uses the existing
Codex runner's fingerprinted job cache; an unchanged completed run returns
without model calls. Existing entries are immutable and only missing identities
are sent to the dictionary editor. All pipeline generation, repair and independent
review workers use the shared `gpt-6-luna` default with `low` reasoning, as required
by `AGENTS.md`. Completed-run reuse remains subject to
content/source/evidence validation. `--existing` adopts a chapter only if its
prose passes review unchanged; source errors require deliberate regeneration.

When independent review finds an approved word definition does not cover an
observed legitimate sense, annotation triage records the exact IDs and stops for
shared editorial review. This is separate from normal immutable entry reuse:

```sh
.venv/bin/python -m pipeline.korean_dictionary_revision --entry-id 'LEXICAL_ID' --draft-annotation runs/korean-topik1/chapter-001/agents/annotation-N/result.json --run-dir runs/korean-topik1/dictionary-revision
```

The editor researches primary dictionary evidence, audits all published Korean
levels plus the draft, and independently reviews corrections before applying
any shared change. It preserves IDs and existing valid meanings, saves revision
evidence with the registry, and refuses concurrent registry changes. Resume the
chapter harness afterward; unaffected annotations remain cached. Editorial
requirements must not leak into learner-facing occurrence notes.
Exact construction phrases can begin on an uninflected prefix, including
negation. Their reviewed scope is available from each covered word, with
complete meanings and contextual optional-level labels.

Publication validates all accepted consecutive chapter runs in a temporary
staging directory before replacing Korean content/assets. It preserves shared
registries and cumulative source occurrences. Exported `l1.review.json` keeps
review evidence and content/dictionary/source fingerprints, so normal app
rebuilds reject stale data without needing ignored local run directories.
`python -m pipeline.publish_honggildong_smoke` rebuilds already reviewed Korean
content; it does not generate or approve new prose.

See `korean_agent_instructions.md` for reusable authoring rules and
`korean_parity_audit.md` for remaining differences from Chinese/Japanese.

## Japanese usage-dictionary pilot (N5)

The pilot now has a separate **grammar dictionary**. Particles and productive
patterns open grammar entries rather than masquerading as vocabulary. Inflected
verbs keep their lexical links and additionally link to reusable conjugation or
construction entries. Existing tap units, inline explanations and form chains are
preserved. The Japanese home screen offers both dictionary browsers.

`python -m pipeline.japanese_grammar_dictionary --update` runs an offline Luna
proposer and independent reviewer on actual particle roles, form steps and grammar
overlays. The word pipeline's `--update` invokes this stage automatically. Registry:
`content/lexicon/japanese/n5.grammar.json`; app asset:
`app/assets/grammar_dictionary_ja.json`. Every candidate is assigned exactly once
to one or more canonical grammar IDs; functions of the same written particle may
have separate entries. Candidate annotation keys are not canonical identities.
Unchanged runs make zero model calls. New contexts reuse immutable approved
lessons; only genuinely new functions receive new entries. Context explanations
and edition-checked example links are stored separately from generic explanations.
Old functional word IDs remain compatible, but word browsing hides them and links
from word components redirect to the grammar dictionary.
`--plan` reports pending grammar work without model calls. A deliberate correction
uses `--request <entry-id-or-title> --reason "..."`, followed by `--update`;
only the requested lessons are re-edited and unrelated reviewed lessons remain
unchanged. Grammar reading fields represent pronunciation (は → わ, へ → え).
Per-candidate fingerprints retain unaffected occurrence assignments when text is
added or changed. Agents receive only new/changed candidates, with their relevant
sentence rather than repeated whole chapters, plus the approved entry registry.
Conjugation dropdowns use one linked chain: lexical base → intermediate grammar
step → subsequent grammar step. Reviewed `layer: form` records bind exact stored
form/readings/labels to canonical grammar IDs; the app never guesses from suffixes.
For example, 入る opens its word entry, 入ります opens polite nonpast ます, and
入りました opens polite past ました. The chain replaces the duplicate top-level
word/grammar buttons and legacy local dictionary-form lookup. Intermediate forms
are not counted as observed text examples; when a lesson is only represented by
these stages, its source links are explicitly labeled conjugation chains.

`python -m pipeline.japanese_usage_dictionary --plan` inspects pending work without
model calls. `--update --workers 4` reviews the existing 吾輩は猫である N5 first
chapter, leaving its source text, lexical boundaries, form steps and grammar
overlays untouched. Inflected surfaces link to lemma/reading identities and shared
senses. Reviewed collocations also get whole-expression entries; productive merged
tap units can link to their canonical grammar component instead of creating a new
entry for each verb plugged into the pattern.

Japanese registries live in `content/lexicon/japanese/`; the published app asset is
`app/assets/usage_dictionary_ja.json`. They are separate from the Chinese IDs and
caches. Agents link occurrences first, then write/review reusable explanations.
The shared editor accepts explicit Japanese policies: no Chinese translation,
Mandarin-reading or Chinese etymology instructions leak into this route. Ordinary
modern explanations use offline agents; genuinely uncertain contributions escalate
to bounded browser research and independent source verification. Multi-character
lexical components receive independently reviewed entries and explanations;
single-kanji references open Japanese dictionary lookups, labeled as dictionary
senses rather than proofs of historical formation.

Adding another occurrence does not invalidate an approved word explanation unless
its sense coverage or structured component relationships change. Targeted edits:
`python -m pipeline.japanese_usage_dictionary --request 名前 --reason "Explain the
contribution of 前 more clearly"` (optionally `--research`), followed by `--update`.
Internal investigation questions persist in `japanese/investigations.json` and do
not automatically schedule research. Completed review caches survive interrupted
runs; publication waits for all explanations and component links to validate.

The app's Japanese dictionary supports written words, hiragana/katakana readings
and English meaning search. Examples retain exact source offsets and JLPT labels,
and navigate back into the Japanese reader. Other JLPT levels remain outside this
pilot; their existing contextual annotations and local dictionary lookups continue
to work.

## Dictionary investigation backlog

Successful dictionary updates refresh `content/lexicon/dictionary.investigations.json`
from reviewed guide `investigation_questions` (component + question), existing
research gaps, and reader notes in `dictionary.investigation-notes.json`.
Issues have stable IDs, entry links, evidence, a current flag and persistent triage
status. They are internal and are not included in learner-facing guide prose.
Refreshing this backlog makes no model/web calls and does not schedule editorial
jobs. A later deliberate investigation can produce a targeted editorial request;
`python -m pipeline.dictionary_corpus --edit-guides` applies those requests using
the already reviewed lexical/tap/expression layers, without rerunning their agents.
Ordinary updates do not repeatedly research an unresolved issue. Disappearing
questions are marked non-current, not silently treated as proven or deleted.

## Overview

This pipeline systematically generates and validates graded reader texts using
**character-level constraints**. Characters are unambiguous to check (no
segmentation needed), unlike word-level constraints.

### How it works

1. **Source text** → original literary text (books/chinese/, books/japanese/)
2. **Character set** → allowed characters for the target level (pipeline/charsets/)
3. **AI prompt** → instructs the AI to rewrite within the charset (pipeline/generate_prompt.py)
4. **AI generation** → simplified text (pipeline/outputs/)
5. **Validation** → character-by-character 95/5 check (pipeline/validate_text.py)
6. **Iteration** → if validation fails, retry with feedback showing above-level chars

### The 95/5 rule

At most 5% of CJK characters in the output may be outside the allowed set for
a given level. Glossary characters (proper nouns, place names) are excluded
from this count.

## Quick start

### HSK1 usage dictionary pilot

`python3 -m pipeline.usage_dictionary --update` matches reviewed usages to shared
entries/senses, runs an independent critic and bounded repair loop, and publishes
`app/assets/usage_dictionary.json` only after all checks pass. Requires Python 3.11+
and the same authenticated Codex CLI as the annotation pipeline. Proposal,
review and repair calls use `gpt-6-luna` at `low`, with up to four concurrent calls.
A Luna `medium` adjudicator filters failed reviews before repairs, so low-effort
critics do not force speculative distinctions or oscillate on acceptable variants.
This is an explicit post-annotation step. The original HSK1 registries remain
reproducible and separate from the incremental HSK2 trial described below.

### Incremental dictionary across HSK levels

`python -m pipeline.dictionary_corpus --prepare --scope full` matches the
already-reviewed HSK2 annotation to shared identities, reviews new usage links,
and prints the dictionary-editor work plan without starting research jobs.
`python -m pipeline.dictionary_corpus --update --scope full --workers 8` updates
and publishes the full HSK2 chapter alongside HSK1. Previously reviewed matching,
reading-unit and dictionary decisions are reused. Optional `--scope sample`
limits indexing to the oath-and-drinking paragraph beginning `第二天，三人在桃园`;
that limited coverage is explicitly recorded and labeled in the UI. Source text,
lexical boundaries, IDs and offsets are never rewritten by dictionary editing.
`--workers 8` allows up to eight
independent editorial jobs; the default is four. Whole jobs are bounded so source
verification and final editing do not wait behind every remaining research call.
Research, verification, writing and review remain separate roles with the same
tool restrictions. Failed jobs do not publish a partial dictionary.
Use `--levels hsk1 hsk2 hsk3 hsk4` to extend the corpus through chapter 1 of
HSK3 and HSK4. Add `hsk5 hsk6` to that list to extend both complete first
chapters through HSK6; already published levels remain part of the same corpus.
Verify the generated higher-level assets with
`python -m scripts.audit_hsk56_dictionary_publication` after asset generation.
Subsequent rebuilds default to the published level list. Each
level reuses the shared dictionary identities, including identities approved for
earlier levels in the same run; this prevents duplicate entries across new texts.
Every level has its own reviewed sense and reading-unit registry. Existing
annotations supply lexical evidence; dictionary editing does not regenerate them.
New tap-unit runs split large chapters at sentence-ending punctuation, keeping
original global segment indices. Each batch gets an offline draft and independent
high-effort review; the reassembled chapter is validated again for overlaps and
exact source spans. Previously approved chapter decisions remain reusable.
Research validation permits at most two corrective attempts per stage, plus one
malformed-JSON retry; final guide validation permits one offline high-effort
repair. Every repaired output passes the same checks before it can be accepted.

HSK1 is rebuilt deterministically, not sent back to agents. Later-level occurrences reuse
approved entry/sense IDs and definitions. New entries/senses get deterministic
ASCII IDs; model spellings cannot rename existing identities. Shared metadata
cannot be rewritten by an occurrence-linking job. Conflicting reviewed meanings
fail rather than being silently merged. Canonical expression entries can also
acquire direct lexical occurrences in another text without becoming duplicates.
Lexical homonyms with different readings get separate entries; occurrence linking
must not change an approved entry's reading (e.g. nàn versus nán for 难).
Pronunciation variation of the same lexical item or person does not create a
second identity; observed source readings supplement the canonical reading.
Canonical bindings can reuse a word across different source boundaries, such as
救下 versus 救|下, or select an independently reviewed carrier inside an inflected
token, such as 开 inside 开得. These create additional span usages, never rewrite
the original lexical sense assignments. Exact whole-token bindings still require
the already reviewed sense. Copied canonical offsets can be restored only when
the chosen headword occurs once in the supplied unit; repeated substrings remain
ambiguous. Canonical names and lexical compounds retain their classification;
they are not automatically grammatical constructions.
Each matching/repair batch is checked for missing, duplicate, unknown and
wrong-headword occurrence links before it joins the corpus. Copied ID typos may
be restored deterministically only for a unique matching headword whose existing
sense definitions match exactly; ambiguous identities still require agent repair.
Occurrence-reference repair is similarly limited to a unique same-headword,
same-source, same-position reference with a one-character fingerprint typo.
Missing, duplicate and misassigned uses still require bounded agent correction.
Existing canonical-expression components and definitions are restored from their
immutable approved registry before validating new bindings. Incremental updates
seed identities from the published snapshot, allowing a reviewed next-scope
registry to coexist with the previous publication while explanations are pending.

`hsk2.senses.json` and `hsk2.reading-units.json` hold source-local decisions.
`corpus.expressions.json` retains unchanged binding decisions by per-candidate
fingerprints, and `corpus.meaning-guides.json` starts with approved HSK1 guides.
New expression bindings are reviewed in bounded batches of thirty. Each batch
retains the prior canonical registry and commits an independently reviewed subset;
a failed later batch resumes from that subset. Publication still requires the
complete requested corpus, not a partial set of bindings.
Only new entries, changed sense coverage or explicit requests trigger dictionary
research/editing. `corpus.last-update.json` records reuse and scheduled work.
Lexical reviews retain per-headword approval fingerprints and content-addressed
approved batch checkpoints. Reviews retain validated successful sibling batches
even when another batch fails; failed headwords remain unapproved and cannot be
published. When a lexical review agent times out, the corpus updater retries only
pending headwords in batches of five rather than the default twenty. Successful
approvals remain cached; smaller batches do not bypass independent review or
validation. Other failures are not silently treated as timeouts.
A targeted editorial review uses high effort and can
correct one entry without rerunning the corpus. ID-addressed review findings scope
repairs to affected headwords, retaining all other entries and links untouched;
unaddressed findings conservatively retain full-batch repair. The assembled batch
still passes independent review and complete occurrence validation afterward.
Initial proposal repairs likewise scope exact identity/link failures by headword,
including both sides of a misplaced occurrence. Unexpected headwords retain the
full-batch fallback; a scoped repair cannot return or alter withheld headwords.
Copied occurrence fingerprints may be corrected for a single substituted
character or a short duplicated block only when the source path, segment number,
and supplied headword identify exactly one current occurrence. This cannot repair
a changed source, position or headword, or choose between senses.
Naming predicates and their particular name arguments stay separate taps and
dictionary identities. Their contextual grammar notes explain the surname,
given-name or courtesy-name role without creating a word for the filled phrase.
Existing shared definitions stay
read-only during occurrence linking; a deterministic metadata guard also checks
cached and newly approved review results before they can be accepted.
When review reuses an approved expression newly attested as a whole lexical token,
ID allocation protects that shared identity even if it was absent from the local
provisional registry; examples must join the existing entry, not a duplicate.
A newly discovered construction must not donate its whole meaning to one component
(e.g. 没有 inside 有没有, or 下来
inside 活下来). Context-specific gloss corrections are recorded separately in
`contextual-gloss-corrections.json`; these do not change segmentation or sense IDs.
Once `corpus.json` is published, the normal usage-dictionary, reading-unit and
asset-generation commands preserve the enabled corpus rather than reverting to
HSK1. No-flag rebuilds and `dictionary_corpus --plan` make no model calls.

All Chinese annotation, review, reading-unit and dictionary roles share
`chinese_translation_policy.py`: English must not add unsupported specificity.
For example, 酒 is not automatically wine (or a particular kind of wine), and
historical plausibility is not evidence. A narrower contextual gloss needs actual
contextual support. Updating this guidance does not blanket-regenerate approved
annotations or dictionary explanations.

The durable registry is `content/lexicon/hsk1.senses.json`. Existing IDs are sent
back to agents on updates; retired entries/senses retain their IDs with empty
evidence lists and are omitted from the app. Each current lexical segment must
link to exactly one sense. Contextual glosses remain separate from shared definitions.
Source fingerprints and a digest of reviewed decisions prevent stale publication.
The app also rejects links if its chapter text differs from the indexed source.

Dictionary guides explain reusable lexical meanings; reader annotations retain
the contextual layer. A dictionary link is not a replacement for a construction
note, a non-obvious grammatical role, or an explanation of a word's contribution
to a larger expression. Short translations are sufficient for straightforward
uses, not for uses whose interpretation depends on an unexplained construction.
Chinese grammar overlays are displayed on overlapping taps, including grouped
reading units and their original lexical components.

Source-boundary corrections must precede final dictionary editing. Independent
review must distinguish complete compounds/names from fused name+verb tokens and
ordinary subject+predicate phrases; relabeling a malformed token as a construction
is not a substitute for correcting its boundaries. `lexical_boundary_migration`
provides pure span-checked helpers: unchanged evidence can retain valid per-word
approvals, changed/new links remain unapproved, and original prose cannot change.
Callers must lock the corpus, verify the reviewed source digest, retain backups,
validate complete staged sources/registries, and only then write changes. Existing
story explanations are retained when their exact term survives a merge.

Use `--propose` for an initial unreviewed registry, `--review` to independently
review/repair an existing proposal and publish it, or no flags for a deterministic,
model-free rebuild. Agent logs are in `runs/usage-dictionary-hsk1/agents/`.
Failed review leaves the previous published asset untouched. Updates re-evaluate
the HSK1 corpus against the existing registry; unchanged proposal inputs need no
new matching calls. Cross-book aliases and nested subsegments are not yet indexed.

In the Chinese library, open **Reading dictionary**, or tap **Dictionary** in an
HSK1 word's explanation. A shared sense shows actual source examples; tapping an
example opens its chapter and highlights the occurrence.

Reading tap boundaries are independent of these dictionary segments. The HSK1
`--update` command also runs `pipeline.chinese_reading_units`: Luna low proposes
contextual predicate-sized units and compact reusable constructions; an independent Luna high pass reviews and
corrects them. `content/lexicon/hsk1.reading-units.json` stores reviewed decisions;
`app/assets/chinese_reading_units.json` is the generated display layer. The reader
opens whole forms such as 看到了 and 来了 and exposes original lexical segments
under Components, preserving their contextual dictionary links. It does not infer
groupings from spelling or merge every aspect particle mechanically.
Separate glosses can hide a construction's logic: 国家 | 有难 keeps the subject
separate while explaining 有 + noun 难 (nàn, trouble), not adjective 难 (nán).
Agents explain the semantic/grammatical bridge and relevant reading or word-class
contrast. Ordinary verb + referential object phrases and whole clauses remain
separate; this is not a license for arbitrary phrase grouping.

Run `python -m pipeline.chinese_reading_units --update` after HSK1 source changes.
Both the asset publisher and reader reject stale display-unit data instead of
silently reverting to fragmented taps. Other levels retain their existing display.

The layered dictionary pilot also maps those reviewed reading units to canonical
entries using `pipeline.expression_dictionary` (invoked by dictionary `--update`).
For example, 看到了 opens as one unit but links to 看到; 看到 links to its base
看 and result complement 到. The 看 page lists attested expressions built on it.
Simple aspect forms such as 来了 link to the existing 来 sense without creating
duplicate headwords/examples. Base lexical occurrence IDs and tap boundaries are
unchanged. `hsk1.expressions.json` stores reviewed expression IDs, ordered component
relations and precise surface-to-canonical span bindings. Luna low proposes and
Luna high reviews; deterministic validation checks spans, references, sense reuse,
complete reading-unit coverage and unchanged-input caching before publication.
Only attested expressions from the HSK1 reading-unit layer are added in this first
trial; it does not fabricate unseen complements or reanalyse every existing compound.
Compact constructions such as 有难 likewise receive a reusable entry linked to
their original components; the original 有 and 难 occurrences remain intact.
Invalid tap proposals are returned to the independent reviewer, and invalid
expression links receive at most two validation-guided repair attempts. Duplicate
identity and component-reconstruction errors identify the offending headword and
the exact existing identities or reconstructed text, rather than asking the editor
to rediscover the problem across the entire registry. Invalid final
results fail closed rather than changing an existing lexical sense assignment.

Every pilot entry also has a **How this word makes sense** explanation. Context
annotation, occurrence-to-sense linking and dictionary editing are separate jobs.
New occurrences reuse an existing sense/definition whenever it fits. Only a new
entry, changed meaning coverage or an explicit editorial request invokes the
dictionary editor. Adding examples does NOT regenerate approved explanations.
Learner-facing explanations emphasize supported present-day composition, not a
research report or proof of historical origin. Irrelevant historical disclaimers,
source-access details and inline internal citation IDs stay out of the prose;
the structured research dossier retains evidence and limitations. Single-character
entries explain their lexical role and leave graph origins to the hanzi dictionary.

Dictionary editing defaults to two independent Luna calls: a low-effort offline
draft and a high-effort offline review. Both return a structured research decision
and concrete questions. Either can escalate uncertain composition or a necessary
historical claim; the reviewer cannot erase the writer's research concern. A
deterministic backstop also routes explicit uncertainty and historical claims
to research. A lexicalized label alone is not uncertainty: a confidently explained
conventional meaning need not establish its historical origin. Reviewers must not
use that label to hide a real gap in understanding a useful component contribution.
Accepted offline explanations carry `evidence_status=reviewed_unresearched`, no
research dossier and no source claim IDs. They are reviewed, not source-verified.

Escalation runs external research, independent high-effort source verification,
then writing and editorial review using the verified dossier. Research/verification
have live web search but no shell; writing and review have web and shell disabled.
Already completed, valid research is checked by a cache-only lookup and reused
before considering the offline route; a cache miss cannot launch a research call.
`editorial_route` records offline review, research, or reused cached research.
Use `dictionary_meaning_guides --request WORD --reason REASON --research` to
explicitly require source verification even for an otherwise simple entry.
Role settings are part of the agent cache key, and tool-event audits reject
unexpected tool use or a researcher that did not actually browse. These are AI
checks, not specialist certification. Historical lexical explanations are allowed
when supported; unsupported historical narratives and forced splits are not.
The reviewer checks both invented explanations AND giving up too early. A research
gap is not proof that a word cannot be explained. Dossiers retain source URLs,
titles, access dates, paraphrased evidence, claim-to-source links, searches and gaps.
Researched explanations and parts cite claim IDs; invalid provenance fails closed,
with at most two research repair attempts. The app exposes sources and research notes
only for entries that actually have a research dossier.
Guides must stand alone as reusable dictionary explanations: source sentences
constrain sense coverage but are not narrated in the explanation, parts, or
caveats. Context-specific commentary belongs to occurrence annotations instead.
Generic illustrations use the smallest canonical form needed for the point, not
unrelated particles or sentence scaffolding (e.g. 看到 rather than 看到了 when
explaining 到 rather than its interaction with 了).

Reviewed guides live in `content/lexicon/hsk1.meaning-guides.json`. Their input
fingerprints cover lexical identity, sense definitions and word-part relations,
NOT examples or the current prompt version. Earlier reviewed guides are migrated
without rewriting and explicitly labeled legacy/unresearched in the registry.
Prompt changes apply to subsequent editorial jobs; they do not trigger corpus-wide
rewrites. Explicit requests opt existing entries into re-editing. Requests for the
same entry coalesce into one job; fulfilled request hashes prevent repeated work.
`meaning-guide-jobs/` records pending/reviewed/failed jobs; `meaning-guide-history/`
retains previous registries. A per-registry process lock prevents overlapping
editorial runs. Failures preserve the previously published dictionary; completed
agent steps remain cached for retry. Parts must reconstruct the headword and
references must resolve before publication. Tap units and lexical occurrences are
never changed by dictionary editorial work.

```bash
# Queue a targeted improvement (no model call yet); prints pending jobs.
python -m pipeline.dictionary_meaning_guides --request 国家 \
  --reason 'Investigate the contribution of 家 using external sources'
# Inspect pending jobs without changing them.
python -m pipeline.dictionary_meaning_guides
# Process queued/missing/changed entries and publish.
python -m pipeline.usage_dictionary --update
# Same request mechanism for word entries discovered within other words.
python -m pipeline.dictionary_meaning_guides --components --request 黄巾 \
  --reason 'Clarify the connection between the cloth and the group name'
```

Publication also runs `dictionary_component_links`: every genuine guide part has
code-point `start`/`end` offsets within its headword, `entry_id`, nullable `sense_id`,
and `link_status`. A unique existing headword is an entry-level navigation link,
not an inferred sense match. Missing multi-character parts are recursively promoted
by `component_words` into real lexical entries: low/high identity creation followed
by the same researched editorial workflow. `hsk1.component-words.json` stores their
identities and `hsk1.component-meaning-guides.json` their reusable explanations.
For example, 黄巾军 links to 黄巾, which explains 黄 + 巾. Strictly shorter written
parts guarantee termination. Existing component entries/guides are reused, including
when discovered in another parent. `origin=component_word` records that evidence is
within a compound, not a fabricated standalone usage; reverse `component_uses`
links lead back to parent words and their real examples.

Single characters with an existing lexical entry link there. Otherwise they carry
a typed `character_ref` (`dictionary=hanzi-etymology`, exact character and Unicode
ID), opening the app's existing character/etymology view rather than manufacturing
thin lexical entries. Single-character lexical entries also carry a `character_ref`
and expose a **Character & etymology** link, keeping the word and character views
connected without conflating their meanings. This uses the currently imported etymology data; it is not
yet an automatic sync with the separate etymology repo's new editorial exports.
Whole-word parts are marked `whole_entry` with no self-link. Ambiguous word-level
targets fail closed rather than silently choosing a sense or homograph.

```bash
# Extract character sets from word/character CSVs
python3 -m pipeline.extract_characters

# Validate a single reader
python3 -m pipeline.validate_text output/xiyouji/hsk3_xiyouji.md -l hsk3

# Validate with glossary
python3 -m pipeline.validate_text output/xiyouji/hsk3_xiyouji.md -l hsk3 \
  --extra-chars "$(grep -v '^#' output/xiyouji/glossary.txt | tr -d '\n')"

# Validate all readers
python3 -m pipeline.validate_all

# After chapter generation, audit every adjacent handoff against the exact
# source chapters and autonomously repair only implicated chapters:
python3 -m pipeline.book_continuity \
  --book-run-dir runs/graded-readers/sanguoyanyi-full \
  --level hsk4 \
  $(printf -- '--source %q ' books/chinese/sanguoyanyi/chapter_*.txt) \
  --concurrency 16

# Generate a prompt for AI
python3 -m pipeline.generate_prompt books/chinese/xiyouji_ch1.txt \
  -l hsk3 --language chinese --title "西游记"
```

## Directory structure

```
pipeline/
├── charsets/           # Generated character sets per level
│   ├── hsk/            # hsk1_chars.txt .. hsk7to9_chars.txt
│   └── jlpt/           # n5_chars.txt .. n1_chars.txt
├── glossaries/         # Per-reader glossary files (proper nouns)
├── outputs/            # AI-generated texts (validated intermediate outputs)
├── prompts/            # Generated prompts (gitignored)
├── reports/            # Validation reports (gitignored)
├── extract_characters.py
├── generate_prompt.py
├── validate_text.py
├── validate_all.py
└── run_pipeline.py     # Interactive orchestrator
```

## Current state and next steps

## Agent harness

`pipeline.agent_harness` runs one isolated, resumable chapter workflow. A
chapter owns the continuity context and final artifacts; scenes are independent
agent jobs within that chapter.

```bash
python3 -m pipeline.agent_harness run \
  --source books/chinese/sanguoyanyi_ch1.txt \
  --level hsk4 \
  --run-id sanguoyanyi-ch01-hsk4

python3 -m pipeline.agent_harness status \
  runs/graded-readers/sanguoyanyi-ch01-hsk4
```

The default stages are:

1. Read the complete original chapter and produce exact source scene boundaries.
2. Adapt scenes independently with Luna xhigh and bounded concurrency.
3. Review every adaptation against its verbatim source scene with Luna medium.
4. Repair scenes that fail source review and independently review each repair.
   Repeat with configurable attempts; if scoped repair still fails, perform a
   fresh low-effort rewrite from the original with a new cache key and audit it again.
5. Assemble accepted scenes in source order.
6. Split the chapter into small annotation chunks and annotate with Luna low.
7. Require exact character-for-character reconstruction before writing
   `reader.json`.

Every agent job retains `result.json`, JSONL events, stderr, and metadata under
the run's `agents/` tree. The metadata fingerprints the prompt, schema, model,
and reasoning effort; rerunning resumes matching successful jobs. Use
`--refresh` to force new calls, `--skip-annotations` for adaptation-only smoke
runs, and `--concurrency` to bound simultaneous agents.

Annotation generation remains backward compatible by default. The explicit
`--annotation-mode constrained` opt-in uses deterministic candidate boundaries,
model enrichment with immutable surfaces, independent semantic review, and
strict lossless correction patches. Neither tokenizer proposal is authoritative;
the correction agent receives both as neutral evidence. Every correction is an
indexed adjacent replacement whose characters must exactly match the replaced
source span. Review issues include exact occurrence-specific character offsets,
so repeated surfaces can be repaired independently; legacy findings without
offsets are accepted only when their surface is unique. Failed review or an
invalid patch fails closed, and this mode is
recorded in both the run manifest and `annotation_audit`. Keep the default
`generative` mode until the constrained smoke-quality gate has passed.

`--annotation-mode constrained-delta` is a second explicit experimental opt-in.
It builds exact chapter-wide deterministic segments and dictionary/pinyin
metadata, makes one compact Luna metadata-delta call for the whole chapter, then
partitions the exact result at annotation-chunk boundaries. Each chunk receives
an independent semantic review and, when needed, only lossless indexed boundary
corrections. A segment or grammar overlay crossing a chunk boundary, a missing
delta target, an invalid patch, or an unresolved review fails closed. The reader
audit records `mode=constrained-delta`, the real review status, and exactly one
chapter metadata call. This mode is disabled by default and must not be enabled
for live book orchestration until its boundary-quality smoke gate passes.

All agent stages default to Luna `low`, including final annotation self-heal.
Quality improvements come from fresh, targeted reruns with concrete review
findings while deterministic gates continue to fail closed.

The harness is unattended by design. It publishes and annotates only when every
scene passes its source-grounded audit. Remaining failures after the automatic
repair ladder produce a `blocked` report with `unresolved_scenes`; the assembled
draft is retained as `chapter-candidate.txt`, never as the accepted chapter.
Tune recovery with `--max-repairs`, `--repair-effort`, `--final-effort`, or
disable the final from-scratch pass with `--no-fresh-rewrite`.

The HSK coverage tools remain diagnostic. The harness prioritizes natural
Chinese and source fidelity; above-level literary vocabulary is expected to be
made accessible through exhaustive annotations.

The Japanese publisher follows the same pragmatic policy. Publication requires
the complete 11 chapter × N5–N1 matrix, with strictly increasing length for
every chapter and the whole book. Passing reviews may not retain unresolved
material additions, distortions, or language defects. JLPT vocabulary coverage
is recorded as a diagnostic only; particles, auxiliaries, names, and a small
story-term allowlist are excluded, and no blanket percentage rejects otherwise
natural literary prose. Japanese annotation remains lexical: compositional
verb/auxiliary grammar is split into tappable units and explained with an
overlay, while genuine names and lexicalized idioms may stay together. `吾輩`
is treated as a pronoun, never as the cat's personal name.

### Audited 三国演义 publication

For the unattended end-to-end finish, run `pipeline/finalize_sanguoyanyi.sh`.
Before any omission or continuity model calls it requires the exact 120×6
matrix of complete `chapter.txt`, `manifest.json`, and `report.json` artifacts;
missing or blocked keys are recovered through narrowly targeted, resumable
harness runs. After omission/continuity promotions it enforces strict
per-chapter HSK1 < ... < HSK6 length, heals only deficient upper chapters, and
invalidates any reader/annotation cache whose prose changed. Publication is
wrapped fail-closed: a nonzero publisher result prevents the success marker and
is returned by the finalizer.

Once all chapter runs and their annotations are complete, publish the six
reader levels deterministically (this command makes no model calls):

```bash
python3 -m pipeline.publish_sanguoyanyi \
  --run-dir runs/graded-readers/sanguoyanyi-full-hsk123 \
  --run-dir runs/graded-readers/sanguoyanyi-full-hsk456 \
  --build-app-content --build-dictionary
```

The publisher refuses to overwrite the standard readers unless it can prove
all of the following for every level: exactly 120 ordered, nonempty chapters;
the original extracted chapter path and SHA-256 match; the generation run and
all source-grounded scene reviews passed; reviewed word/particle/name/idiom
annotations reconstruct the accepted chapter exactly; OpenCC mechanically
normalizes titles, prose, and matching annotation surfaces to Simplified
Chinese and proves the result idempotent; and both every chapter and the whole book become
strictly longer from HSK1 through HSK6. It writes:

- `output/chinese/sanguoyanyi/hskN_sanguoyanyi.md`, with a standard preamble
  and exactly 120 `##` chapter headings;
- `hskN_sanguoyanyi_annotations.json`, retaining text, segments, review audit,
  and source provenance per chapter;
- `publication-audit.json`, containing hashes, lengths, and passed invariants.

Use `--audit-only` to check readiness without writing. During an early
adaptation-only pass, `--audit-only --allow-missing-annotations` checks prose
and provenance while intentionally deferring the annotation gate. Final
publication output remains separate from the app catalog. Snapshot accepted
Sanguoyanyi drafts and reviewed annotations with
`scripts/sync_sanguoyanyi_content.py`, then rebuild both app language assets
with `scripts/generate_app_content_json.py`. The app lazy-loads those exact
agent segments and explanations; missing sidecars are non-interactive rather
than dictionary-tokenized. Legacy
`output/` and `readers/` trees are never scanned for app-visible content. The
dictionary can be rebuilt with `scripts/build_dictionary.py`.

HSK1 has an additional enforced learner-readability gate. Natural word tokens
must be at least 85% HSK1 after explicit story-term exemptions,
synopsis-style long sentences and clauses are rejected, and any explicit
naturalness or readability finding forces another Luna-low repair. The clean
sync and post-generation promotion paths rerun the same gate, so a later repair
cannot restore a rejected legacy HSK1 draft. For a partial v2 preview, override
only that level with `--run-root hsk1=PATH --level-chapters hsk1=1`.

### Annotation-only Chinese batch

After continuity repair has finalized accepted `chapter.txt` files, annotate
them without invoking any adaptation or prose-rewrite stage:

```bash
python3 -m pipeline.annotate_chinese \
  --run-dir runs/graded-readers/sanguoyanyi-full-hsk123 \
  --run-dir runs/graded-readers/sanguoyanyi-full-hsk456 \
  --concurrency 8 --chapter-concurrency 8
```

The command discovers only chapter runs whose generation manifest and report
are complete. It fingerprints every annotation and annotation-review call with
the exact full `chapter.txt` SHA-256, checks that the prose did not change while
agents were running, and atomically replaces `reader.json` only after reviewed
segments reconstruct the chapter exactly. It never calls outline, adaptation,
repair, or rewrite stages and never mutates generation provenance. Per-chapter
status is written to `annotation-report.json`; aggregate progress is kept in
`annotation-batch-report.json`. A pre-existing reader is reused with zero model
calls only when its text and level match exactly, its segments reconstruct the
current chapter, and `annotation_audit.all_reviewed` is true. Any malformed or
stale reader fails closed into re-annotation; `--refresh` always forces calls.

Chinese reader annotations keep lexical and grammatical explanations separate.
The exact adjacent `segments` stream contains only dictionary words, particles,
names, genuine lexicalized idioms, and punctuation; it reconstructs the chapter
character for character. `grammar_overlays` use exact character offsets to
explain multi-segment constructions without turning ordinary clauses into fake
idioms or sentence-sized vocabulary entries. Each overlay carries a provisional
`grammar_candidate_key` for later clustering, not a canonical lesson identifier;
future unification must consider its span, pattern, explanation, and context rather
than relying on exact key equality. Publication derives `grammar_candidate_keys`
on every overlapping segment so consumers can directly discover which segments
have grammar help. Both layers are independently reviewed and mechanically
validated before publication.

HSK focus normally follows exact word-list membership. The deliberate exception
is a complete segment that has both an independently reviewed subsegment
decomposition and a grammar overlay on that exact span. Such a productive
construction is classified from its lexical parts, with its hardest required
part controlling reading focus; absence of the combined surface from the HSK
list is not itself evidence that the construction is above level.

### Japanese linked form-chain meanings

Reusable grammar entries are focused lessons, not catalogs of inflected variants
found in a passage. A construction entry explains its own meaning and formation;
polite/past/negative/imperative transformations belong to their separate entries
and occurrence chains. Both new-entry agents and scoped editorial agents use this
rule. Minimal examples, prerequisites and useful contrasts remain appropriate
when they clarify the entry's own concept.

Agent annotations supply exact source forms, readings and ordered transformation
steps. `pipeline.japanese_grammar_dictionary` links each transformation to its
reviewed grammar entry. For compact merged units, it also supplies the agent with
the whole overlay and component spans, preserving the original tail-step identity.
Agents must return `display_meaning_en` for the **complete displayed form** and
`display_base_meaning_en` for its complete base. These occurrence-specific fields
are independently reviewed and required by validation, not constructed by joining
English fragments. Thus 住むことにします means “decide to live,” and
住むことにしました means “decided to live”; lexical idioms keep their combined
meaning. Tense, politeness and other formation labels are shown separately.

Only candidates whose evidence changed are sent to agents. Existing dictionary
entries and unaffected assignments are reused; full approved explanation prose
stays local. The app checks the exact source edition, tail step and complete
display form before using its reviewed meaning. Older assets may show the final
contextual translation, but must not label a full intermediate form with a
tail-only translation. Repository-wide feedback handling is documented in
`AGENTS.md`: improve the appropriate shared layer, check comparable cases and
update future agent instructions instead of accumulating ad hoc exceptions.

### Audited 吾輩は猫である publication

The Japanese full-book pipeline has an equivalent deterministic publication
gate for the original eleven chapters and JLPT N5 through N1:

```bash
python3 -m pipeline.publish_wagahai \
  --run-dir runs/graded-readers/wagahai-full-jlpt \
  --build-app-content
```

Run directories are named `chapter_01-n5` through `chapter_11-n1`. The gate
verifies the source manifest and every chapter SHA-256, accepted generation,
every scene review and the final whole-chapter review, exact canonical
`surface` segment reconstruction, offset-based grammar-overlay spans, annotation
review status, and strictly increasing Japanese-script length for every chapter
and whole level from N5 through N1. It retains optional grammar overlays and
writes `nN_wagahai.md`, `nN_wagahai_annotations.json`, and
`publication-audit.json` under `output/japanese/wagahai`. Use `--audit-only` to
check readiness without writing, or add `--allow-missing-annotations` to that
read-only check during the adaptation phase.

### Unattended book runs

The `book` command expands chapter sources across levels and runs them in
parallel. `--concurrency` is a single global ceiling for all Codex subprocesses;
`--chapter-concurrency` separately limits how many chapter pipelines may be
active. This avoids multiplying the process/RAM budget when many chapters run.

```bash
python3 -m pipeline.agent_harness book \
  --source books/chinese/sanguoyanyi_ch001.txt \
  --source books/chinese/sanguoyanyi_ch002.txt \
  --book-run-id sanguoyanyi \
  --levels hsk4 hsk5 hsk6 \
  --concurrency 9 --chapter-concurrency 3
```

Each chapter-level combination keeps the same fingerprinted job cache as a
single run. A process restart or chapter retry therefore reuses valid work and
executes only missing or failed jobs. `book-report.json` is rewritten after
each chapter completes, records isolated failures, and refuses a `complete`
status unless every chapter passes and each source's CJK length increases
strictly across the requested levels. A violation automatically re-runs only
the deficient upper-level chapter with a raised target (bounded by
`--length-repair-rounds`). Default targets are 220, 300, 500, 750,
1050, and 1450 CJK characters for HSK1 through HSK6; override one-off runs with
`--target-chars`. Scene review, scoped repair, fresh-rewrite escalation, and
exact annotation reconstruction remain mandatory inside every chapter. Use
`--chapter-retries` to control automatic recovery from transient failures.

### What's been done

**Pipeline infrastructure** is complete and tested:
- Character extraction from both official char CSVs and word-list CSVs
- Prompt generation with charset constraints
- Validation with glossary support
- Batch validation

**Validated chapter-1 outputs** exist in `pipeline/outputs/` for:
- **xiyouji** HSK1-6 (99.6-100% in-level)
- **liaozhai** HSK3-6 (96.8-100% in-level, 画皮 + 聂小倩)
- **sanguoyanyi** HSK3-6 (95.7-99.2% in-level, 桃园结义)
- **JLPT readers**: n3_chuumon, n2_merosu, hsk3_xiyouji, hsk6_guxiang, hsk5_songci

**Individual readers** in `readers/` and `jlpt/readers/` that pass validation:
- hsk3_05_xiyouji.md, hsk4_02_kong_yiji.md, hsk5_02_songci.md
- hsk5_03_niexiaoqian.md, hsk6_02_modern_poetry.md, hsk6_03_guxiang.md
- n5_05_tanjoubi.md, n5_07_kaimono.md, n2_01_wabi_sabi.md
- n2_04_merosu.md, n3_06_chuumon.md

### What still needs to be done

**The main remaining task**: the `output/` directory has 90 readers (15 series
× 6 HSK levels) that were AI-generated from memory without character
constraints. Most of them (79/90) FAIL the 95/5 validation.

The validated texts in `pipeline/outputs/` only cover the **opening chapter**
of each novel. The original `output/` readers are **abridged versions of the
full novels** — for example, sanguoyanyi HSK4 covers 25+ chapters from 桃园结义
through 三国归晋.

**The right approach for fixing these is:**
1. Keep the full-novel story scope from the existing output/ readers
2. Do targeted character-level fixes: find above-level characters and replace
   them with in-level synonyms/paraphrases
3. Use the validated chapter-1 versions in `pipeline/outputs/` as reference for
   the writing style at each level
4. Work level by level — lower levels need more aggressive simplification

**Series with source texts** (prioritize these):
- xiyouji (6 readers) ← books/chinese/xiyouji_ch1.txt, xiyouji_ch4to7.txt
- sanguoyanyi (6 readers) ← books/chinese/sanguoyanyi.epub (ch1 extracted to sanguoyanyi_ch1.txt)
- liaozhai (6 readers) ← books/chinese/liaozhai_zhiyi.txt, niexiaoqian_original.txt
- songci (6 readers) ← books/chinese/songci_collection.txt + individual poems

**Series without source texts** (66 readers, lower priority):
chengyugushi, chuci, guwenguanzhi, hongloumeng, lunyu, minjiangushi,
shijing, shishuoxinyu, shuihuzhuan, sunzibingfa, tangshi

**Songci special case**: These include original classical Chinese poems that
can't be simplified. Need a different validation approach — perhaps exclude
the poem text from the character count, or add poem characters to the glossary.

## Japanese agent harness

### Independent dictionary editorial review

Extend cumulative coverage without rewriting source annotations:

```bash
python -m pipeline.japanese_usage_dictionary --update --levels n5 n4 n3 n2 n1 --workers 4
```

`content/lexicon/japanese/coverage.json` records the selected levels. Existing
N5-named registries are retained as shared stores for compatibility, not separate
per-level dictionaries. Shared lexical identities and grammar lessons are reused
across levels. Lexical routing uses 16-group reviewed batches; grammar routing
uses 96-candidate reviewed batches with durable checkpoints.
Grammar batch boundaries keep a tap unit's contiguous conjugation stages together,
so separate reviewers do not independently paraphrase the same base meaning.
Independent lexical batches run concurrently under the configured tool-call ceiling. Only changed groups
and candidates go to agents. Assets publish after complete validation; interrupted
runs resume from approved batches rather than repeating completed work.
Lexical repairs retain short occurrence aliases and report every missing or extra
sense assignment explicitly; durable registries retain canonical occurrence IDs.
Cached worker responses that violate their assigned tool capabilities are rejected
as cache misses: a retry reruns only that job, and cache-only probes launch nothing.
Grammar repair errors use the same short candidate aliases as their input records,
while durable records retain canonical IDs. Overlapping ID prefixes are replaced
longest-first so an error cannot accidentally identify the wrong candidate.
Exact-coverage errors enumerate all missing and extra assignments, including
repeated duplicates, so repairs can address the whole batch rather than guessing.
Missing complete-form meanings likewise report every affected candidate and its
missing fields together, rather than spending one repair on each omission.
Inconsistent base meanings report every conflicting chain and its stage IDs/glosses,
so reviewers can resolve the actual disagreement without scanning unrelated rows.
Grammar repairs receive only new lesson prose and complete assignments; approved
existing lesson explanations stay local and are not regenerated.
Repairs are complete replacements, not patches: every new lesson referenced by
the assignments must be returned again, even if its definition needed no change.
Plain-nonpast lexical verbs still route their observed verb form to the reusable
plain-nonpast grammar lesson alongside their separate word link. Empty grammar
destinations are invalid at the schema boundary.
Grammar display identities follow the reader's compact-unit rules, including
noun-absence connective phrases and benefactive chains whose overlay starts at
the linking particle. Expanded identities retain the original tail-step IDs and
source boundaries; changed identities trigger a scoped re-review of their complete
meanings, rather than displaying a tail-only gloss on the larger expression.
New grammar routing batches require reviewed meanings for every complete form
step, including ordinary standalone inflections; provisional source glosses are
retained only as identity guards. After the migration, run
`python -m pipeline.japanese_dictionary_publication_audit` to verify all five
levels against the actual app assets and write the publication evidence report.
Component words that later gain an attested entry with the same headword,
reading, and kind retain their old records with `superseded_by` compatibility
links. Search and component navigation prefer the canonical attested destination;
homographs with different readings or grammatical kinds remain distinct.
Dictionary parts have separately reviewed destinations in `component-links.json`.
The resolver receives existing functional identities in a separate catalog:
particles and constructions excluded from lexical destinations must be routed to
the appropriate grammar lesson, not recreated as new words. Duplicate-identity
validation names the conflicting headwords and readings for targeted repair.
Agents use short per-batch component IDs; the host restores stable IDs before
validation and storage. Unknown aliases are rejected, and repair errors list
missing/unknown IDs and undefined/unreferenced lexical entries explicitly.
An inflected written part can link to its base word and grammar lessons without
becoming a fake dictionary headword. Grammar routing precedes this resolver so
the canonical lesson catalog is available. Genuine missing component words are
created and explained recursively; unchanged part bindings are reused.
Reverse component usages are rebuilt from the final reviewed destinations, so
replacing a generic particle link with a precise grammar link cannot leave a
stale "part of this word" relationship on the old lexical record.
Expressions that are single annotation units in another level are promoted into
the shared word store. Sense IDs, definitions, and approved meaning guides are
reused; the secondary store retains compatibility records with empty usages.

`pipeline.japanese_dictionary_audit` audits the complete published pilot:
word explanations, grammar lessons, source segments, overlays, and reviewed
occurrence chains. Low-effort proposals receive high-effort review. A complete
coverage ledger is validated; compact agent-facing IDs are restored to canonical
IDs before publication. Unchanged reviewed audits are cached.

The N5 baseline audit and root triage are recorded in
`content/lexicon/japanese/n5.editorial-audit.json` and
`n5.editorial-triage.json`. Triage distinguishes real edits from publication or
presentation defects and retains useful component explanations and honest gaps.
Scoped entry requests reuse approved research; `--occurrence-request` edits
contextual notes/full-form meanings without changing dictionary routing.

Grammar formations can link to prerequisite lessons using `grammar_entry_id`.
References must exist and be acyclic; publication includes their transitive
closure. 連用形 and ます-stem name the same stem, whereas ます is the complete
polite nonpast operation. Related suffix lessons link to the shared stem rather
than duplicating its formation rules.

The app renders reviewed occurrence chains and canonical grammar context, not
legacy overlay lookup cards. Reviewed display meanings override provisional
source glosses only after exact source/form identity checks. Particle readings
and functions come from the matched lesson. Source text and tap boundaries remain
unchanged.

`pipeline.japanese_agent_harness` applies the resumable, fail-closed
adapt/review/repair/fresh-rewrite workflow to Japanese sources without changing
the Chinese harness. Its Japanese schemas segment every accepted chapter into
dictionary words, auxiliaries, particles, names, genuine idioms, and
punctuation. Each segment has a dictionary-form lemma, kana reading, and concise
contextual English meaning. Independent review checks exact reconstruction,
segmentation, lemmas, readings, and meanings; rejected chunks self-repair and
then make a fresh low-effort attempt with a distinct cache key.

Run all 11 cleaned chapters of `吾輩は猫である` at every JLPT level:

```bash
python3 -m pipeline.japanese_agent_harness book \
  --source-dir books/japanese/wagahai_wa_neko_de_aru \
  --book-run-id wagahai-full \
  --levels n5 n4 n3 n2 n1 \
  --concurrency 16 --chapter-concurrency 8
```

Chapter-proportional targets total about 18k, 30k, 48k, 72k, and 105k
Japanese characters from N5 through N1. Every source chapter must become
strictly longer at each higher level; only a deficient upper-level chapter is
regenerated. Artifacts follow the Chinese harness contract (`manifest.json`,
`outline.json`, `report.json`, `chapter.txt`, and `reader.json`) in run folders
such as `chapter_01-n5`.

### Glossary files

Each series in `output/` has a `glossary.txt` with proper nouns and essential
story vocabulary. These characters are excluded from the 95/5 count. When
regenerating readers, check the glossary and add missing proper nouns as needed.

Updated glossaries:
- output/xiyouji/glossary.txt — added 齐天大圣, 菩提老祖
- output/liaozhai/glossary.txt — added 燕赤霞, 陈氏, 兰若寺, 拂尘, 乞丐, 葬
- output/sanguoyanyi/glossary.txt — added 涿郡, 涿县, 讨伐, 黄巾, 朝廷, 誓, 祭, 皇帝, 皇室
