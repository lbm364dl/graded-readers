Fresh Korean annotation chunks use source-span-links-annotation-v4. Select tap boundaries
with source_start/source_end Unicode character offsets into the supplied chunk_text;
do not emit copied segment text. Cover the entire source in contiguous, nonempty
spans, including spaces, punctuation and final separators. The supplied character
index identifies offsets, not linguistic boundaries. Keep lexical and grammar
identities, complete-form meanings and occurrence links on the chosen spans.
The pipeline derives segment text from the immutable source; older reviewed
formats remain replayable and must never be relabeled or rewritten as new evidence.

Complete grammar and expression surfaces also use source_start/source_end ranges
aligned with full tap boundaries and containing their attached word. Never copy
or paraphrase these surfaces. Direct grammar-tap lessons and links already
explained by a form step use (-1, -1) and an empty display meaning; construction and expression
links need an explicit source range and its complete contextual meaning.
Every grammar-kind tap needs its own direct lesson link whose entry_id matches
its lexical_id. A larger phrase covering that tap does not supply this direct
lesson: phrase links are published at the first tap in their source range,
regardless of which included tap contains the worker link. Moving the worker
attachment within an unchanged range does not change that published position.
Keep one grammar occurrence per published position and identity. Do not add a
phrase link that collides with an existing form-step link for the same lesson;
do not invent an alternative range or identity to evade a collision. Preserve
distinct lessons sharing a tap and the same lesson at distinct source positions.
When validation identifies a link and indexed source boundaries, repair that
specific occurrence using the source and rerun validation. Preserve unrelated
tap boundaries and links; an error is not permission to guess a new range.
