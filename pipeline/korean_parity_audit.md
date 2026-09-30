# Korean pilot against the Chinese and Japanese pipelines

Scope checked: the current source, agent harness, annotation/publication code,
reviewed registries, app services and reader UI for Chinese *Three Kingdoms*
and Japanese *I Am a Cat*, compared with Korean *Hong Gildong* Level 1,
chapter 1. This records the actual first-chapter implementation, not a claim
that Korean has full pipeline parity. The Japanese sentence-breakdown files are
currently separate, uncommitted work in this workspace; preserve that work.

| Layer | Chinese / Japanese implementation | Korean first-chapter state |
| --- | --- | --- |
| Source and adaptation | Source indexes/manifests, scene plans, chapter runs, source-grounded adaptation reviews and repair ladders in `agent_harness.py`, `japanese_agent_harness.py`, and the two book publishers. | Public-domain scan is recorded, but the four-sentence prose was written manually. `source_alignment.reviewed` and two beat labels are a manual assertion, not source-position evidence or an independent source review. No Korean generation agent or scene-run artifacts exist. |
| Curriculum and readability | Chinese records HSK word status, matched level, character evidence and focus; Japanese records matched editorial JLPT band, story roles, focus and a lexical budget. | NIKL A/B/C identity and a 10% lexical budget are checked. Published occurrences now distinguish beginner target, above-level lookup, name and reviewed story term. NIKL A is not TOPIK 1. Sentence length is checked in space-delimited units, which is a coarse readability screen. |
| Tap annotation | Both languages have independently reviewed segmentation and grammar overlays tied to exact prose. Chinese has reviewed reading units and occasional word-internal component help. | Existing taps reconstruct the text and have contextual meanings; 14 grammar lessons link to reviewed positions. Segmentation and meanings were authored manually; no independent annotation review or phrase grouping/overlay layer exists. |
| Display units and meaning guides | Chinese keeps lexical segments separate from reviewed reader tap units; Japanese can merge linked grammar constructions while retaining original segment positions. Both can explain internal components without changing dictionary identities. | Korean currently displays annotation segments directly. It has no reviewed reader-unit layer, merged tap construction, or reusable component/meaning-guide graph. The phrase `부를 수 없었습니다` now has a linked explanation row on the `부를` tap, but is not a merged tap. |
| Story vocabulary | Both languages distinguish names and essential story words from ordinary target vocabulary and show an optional lookup cue; contextual importance is stored on occurrences. | 홍길동 is a name lookup; 벼슬 is a story-term lookup with a passage-specific importance note. Ordinary A-band words remain targets. No other terms are exempted. |
| Reusable dictionaries | Chinese has shared sense identities, expressions, meaning guides and incremental editorial jobs; Japanese has shared word senses, grammar lessons, component links, reviewed form routes and occurrence examples. | Fourteen manually reviewed word entries and 14 grammar lessons cover the chapter, with distinct source-position examples. The two naming/quotation taps route to grammar only. No independent dictionary editor/reviewer, component graph, sense disambiguation workflow or incremental cache exists. |
| Form explanations | Japanese stores ordered complete-form meanings and separately reviewed grammar destinations, including merged constructions. In its tap sheet, base and transformation rows carry their own links; there is no duplicate link list above a chain. | Seven inflected tap units have complete-form steps. Their links now appear on the base and transformation rows, without a duplicate list. A reviewed extra row links `부를 수 없었습니다` to its larger ability construction, with its complete phrase meaning and source span. It is not yet the multi-stage, merged construction chain Japanese supports. Korean form destinations are embedded in annotation steps rather than independently reviewed form-layer occurrence records. |
| Sentence analysis | Japanese publishes optional, source-aligned sentence parts for selected difficult sentences; simple sentences need no breakdown. | Two of four sentences have source-aligned parts and full translations. The plain opening and closing sentences remain without a breakdown. |
| Publication and app | Chinese/Japanese audit reviewed run evidence, reuse approved entries, reject stale source links and publish dedicated assets. Their readers expose appropriate dictionary, grammar, focus and form layers. | Korean publication validates its one chapter, lexical identities, entries, positions, form data and breakdown spans. The app shows separate word/grammar browsers, lookup cues, linked form stages and selected sentence breakdowns. The construction row's source span and complete meaning are validated. The source-review boolean is not equivalent to the other pipelines' run evidence. |

Before expanding Korean beyond chapter 1, add a source-position scene map and
independent adaptation/annotation review, then use approved word/grammar IDs
incrementally. An agent run should only handle new or changed passages; stable
entries and occurrence links must remain reusable. Add a reviewed
whole-construction chain where the passage needs one, without inventing a word
entry for productive grammar or changing the original tap boundaries. Keep
publication blocked if any required review, source span, exact form meaning or
dictionary link is missing.
