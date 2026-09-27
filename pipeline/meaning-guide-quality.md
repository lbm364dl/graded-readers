# Meaning-guide pilot checks

## Legacy dropdown audit (2026-09-27)

Reviewed the 109 nested-dropdown occurrences (56 distinct decompositions) across
the six published first chapters. Four 招军榜 occurrences now preserve 招军 as
a word component; 三路夹攻 preserves 三路 and no longer glosses its three-way
attack as merely an attack from both sides. Corrected 公论 from official to fair
public judgment and removed question force wrongly assigned to 没有 inside
有没有. Outer tap boundaries and chapter text are unchanged.

The general lesson is to distinguish component meaning from whole-construction
meaning, preserve useful word components, and constrain glosses by the actual
context. Character-level dictionary explanations and word-level dropdowns need
not have identical granularity. Existing ordinal and grammatical groupings were
therefore retained. Regression coverage checks these fixes and every nested
component's source offsets across all six levels. This is a targeted editorial
audit, not a claim that every explanation has been linguistically certified.

## HSK2 learner-feedback follow-ups (2026-09-27)

Targeted editorial requests revised 聚集, 当时, 流行, 相信 and 许多 without
changing dictionary identities or sense coverage. 聚集 distinguishes supported
standalone usage ranges from the shared gathering meaning inside the compound;
it does not invent distinct steps. 当时 explains 当's temporal contribution in
plainer language. The remaining useful contribution questions, notably 行, 相
and 许, are recorded as internal investigations rather than declared unknowable.

The stale 黄巾起事 dropdown now uses 黄巾|起事 instead of assigning the
compound's uprising meaning to 事 alone. 变得 remains a searchable construction,
with an explicit learner-facing grammar-construction label and explanation that
it is not an indivisible word. A live browser check verifies both changes.

Reviewed guides can emit structured investigation questions. Updates also collect
existing research gaps into the persistent internal backlog; triage status survives
refreshes. This does not schedule new research or expose issue fields as learner
prose. Guide-only updates avoid rerunning lexical/tap/expression agents, and explicit
wording-only requests can reuse verified evidence when coverage is unchanged;
forced research still takes precedence. The full Python suite passes 979 tests
(48 skipped), and the complete HSK5/HSK6 publication audit still passes.
The full Flutter suite passes 319 tests, static analysis reports no issues, and
the release web build succeeds. An unchanged real guide-only update was run with
every model call replaced by a failing mock: the dictionary and investigation
backlog remained identical, with no model calls despite the open issues.

## Full HSK5/HSK6 first-chapter rollout (2026-09-27)

The published six-level corpus has 1,975 entries and 7,475 linked usages, including
1,921 reviewed main-entry guides and recursively linked component entries. All
1,998 HSK5 and 2,130 HSK6 first-chapter lexical usages are linked; their 68 and 63
reviewed tap units have canonical dictionary bindings. The 795 new or changed
main-guide jobs and 30 component-guide jobs passed. The final plan reports no
pending main-entry editorial jobs, including the focused 校尉 request.

Approved expression work is checkpointed in bounded batches of 30, preserving
approved identities and allowing interrupted runs to reuse successful batches.
Validation diagnostics identify the offending headword, source span, or identity
instead of asking agents to guess what went wrong. Guide validation reports
duplicate whole-word components explicitly; honest statements about what plural
characters do not specify are recognized without weakening the limits check.

The rejected 平日 decomposition was repaired to 平|日. A focused, independently
verified research request for 校尉 produced structured 校|尉 contributions and
retained the warning against equating this historical title with a specific modern
rank. Existing successful main-guide jobs were reused in the corrective run.

`python -m scripts.audit_hsk56_dictionary_publication` verifies a complete rebuild
against published assets, every lexical source span, every tap binding, structured
component references, and both complete annotation assets including grammar notes.
The HSK3/HSK4 publication audit also passes after this expansion. The full Python
suite passes 968 tests (48 skipped); the full Flutter suite passes 318 tests.
Flutter analysis reports no issues and the release web build succeeds.

Browser checks open 关起来 in HSK5 and 惊倒 in HSK6, retain their contextual
grammar explanations, follow dictionary links and structured word/character
components, and return from real examples to their source readers. Screenshots
confirm the reusable explanations are rendered. Numbered-pinyin search
`guan1 qi3 lai5` also finds 关起来 in the live preview. The browser check script is
`runs/check_hsk56_published_browser.cjs`. These checks establish publication and
interaction coverage, not exhaustive linguistic certification.

Expanded-corpus tests select exact headwords rather than assuming substring search
has only one result. Guide-reuse checks require every unchanged coverage input to
retain its approved row, rather than a fixed pilot-era reuse count: HSK5/HSK6
legitimately add senses to familiar words such as 到 and 做.

## Full HSK3/HSK4 first-chapter rollout (2026-09-26)

The published HSK1–HSK4 dictionary contains 1,187 entries and 3,283 lexical
usages. All 876 HSK3 and 1,755 HSK4 first-chapter lexical usages are linked;
their 25 and 56 reviewed tap units have canonical dictionary bindings.
Every published entry has a reviewed intuitive meaning guide, and structured
components resolve to dictionary senses or hanzi-etymology references.

Unchanged guides and approved occurrence batches are retained. Partial batch
success survives another batch failing; canonical metadata changes invalidate
only affected words. A final plan reports no pending editorial jobs. Ordinary
explanations use offline drafting and independent review, with research reserved
for concrete uncertainty rather than automatically researching every word.

The component audit removed arbitrary fragments from 与众不同, 五颜六色,
and 呼风唤雨. The HSK4 快守 boundary was corrected to independent 快 and 守,
while 守不住 remains one tappable potential-complement unit; clause-final 了
stays outside it. Existing entry identifiers are retained, including retired IDs.

`python -m scripts.audit_hsk34_dictionary_publication` checks a full rebuild
against published assets, every source occurrence, every tap binding, component
references, and both complete app annotation assets. The full Python suite passes
921 tests (48 skipped). Reader grammar now retains overlapping Chinese phrase
notes on ordinary tokens and grouped taps instead of applying Japanese matching
rules. Dictionary links do not replace contextual grammar explanations.
The full Flutter suite passes 316 tests, including both complete chapter readers;
Flutter analysis reports no issues and the release web build succeeds. A browser
check opens 泛上来 in HSK3 and 不受限制 in HSK4, verifies their contextual
explanations, and follows both dictionary links to real source examples.

## Adaptive HSK2 rollout (2026-09-26)

The full HSK1–HSK2 asset contains 291 entries and 620 linked occurrences. Existing
entry/sense identities and reading boundaries are retained. Tests verify coverage
of every HSK2 lexical occurrence and byte-for-byte reuse of unchanged HSK1 guides.
An unchanged full update was run with every model call replaced by a failing mock:
it produced identical output with no model calls, including component expansion.

Ordinary entries now use an offline draft and independent review. For example,
不够 explains 不 negating 够's sufficiency without historical research. The real
HSK2 run escalated specific questions about 许 in 许多 and 连 in 连忙. Completed
verified dossiers were reused rather than discarded during the workflow change.

Routing regressions cover writer/reviewer uncertainty, historical claims, invented
citations, corrected draft classifications, conventional-meaning caveats, explicit
research requests, and safe cache-only misses. A conventional meaning need not
prove its historical origin; correcting an ordinary draft error need not trigger
research. Research and tool-use validation remain required for sourced approval.

The browser check confirmed that 喝酒 has HSK1 and HSK2 examples under the same
entry and sense, both glossed as drinking alcohol; its HSK2 example opens that text.
Flutter tests cover whole-unit 活下来 tapping data and canonical dictionary links.
These checks are safeguards, not a claim of exhaustive linguistic certification.

## Original HSK1 pilot

The 99 HSK1 entries have Luna 6 low drafts and separate high-effort editorial
reviews. Review focuses on the connection between component contributions and
the whole meaning, removing unsupported origins and false precision. This is an
editorial safeguard, not a guarantee of exhaustive linguistic verification.

Spot-checked cases:

- 看到 explains the action/result relationship, distinguishing looking from
  successfully seeing, rather than treating 到 as physical arrival here.
- 叹气 explains an expressive exhalation with 气 contributing breath, not anger.
- 明天 uses 明's time-expression contribution, without inventing a sunrise story.
- Proper names are identified as names, without invented naming motives.
- Single-character entries do not get split into radicals. Less transparent
  words acknowledge what cannot be predicted from individual character meanings.

## Held-out 批评 case

This word is not in the HSK1 corpus and was **not published** as a reading entry.
The same proposal/review workflow produced an overlapping-components explanation:
批 contributes criticism/fault-finding, 评 evaluation/judgment, with an explicit
warning that these are not two distinct steps or a precise difference in method.

The broad evaluative overlap and ordinary fault-focused use were checked against
the Ministry of Education entries for [批](https://dict.concised.moe.edu.tw/dictView.jsp?ID=2938&la=0&powerMode=0),
[評](https://dict.revised.moe.edu.tw/dictView.jsp?ID=963&q=1&word=%E8%A9%95)
and [批評](https://dict.concised.moe.edu.tw/dictView.jsp?ID=2941&la=0&powerMode=0).
This is a present-day learning explanation, not an etymological derivation.
The run artifacts are under `runs/meaning-guide-quality/`.

Automated tests cover input-sensitive caching, unchanged entry/sense identities,
complete coverage, valid component spans, rejected stale/unreviewed data, and the
visible explanation, component contributions and limitations in the Flutter UI.
