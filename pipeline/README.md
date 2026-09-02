# Graded Reader Pipeline

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
