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
or paraphrase these surfaces. Grammar links already explained by a form step
use (-1, -1) and an empty display meaning; other construction and expression
links need an explicit source range and its complete contextual meaning.
