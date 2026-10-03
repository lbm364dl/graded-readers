Fresh Korean annotation chunks use source-span-annotation-v3. Select tap boundaries
with source_start/source_end Unicode character offsets into the supplied chunk_text;
do not emit copied segment text. Cover the entire source in contiguous, nonempty
spans, including spaces, punctuation and final separators. The supplied character
index identifies offsets, not linguistic boundaries. Keep lexical and grammar
identities, complete-form meanings and occurrence links on the chosen spans.
The pipeline derives segment text from the immutable source; older reviewed
formats remain replayable and must never be relabeled or rewritten as new evidence.
