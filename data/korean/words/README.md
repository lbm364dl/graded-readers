# Korean curriculum sources

`nikl_2017_curriculum_20201117.xlsx` is the original NIKL six-level vocabulary
and grammar workbook, corrected 2020-11-17, downloaded from the attachment to
https://m.korean.go.kr/front/reportData/reportDataView.do?mn_id=45&report_seq=932 .
Credit: 국립국어원 (National Institute of Korean Language). The source page
licenses reuse under KOGL Type 1 (attribution).

Original SHA-256: `2cde28ab90e04728513e65ef5df4baaa400a4055fe2abd1856889cb983c4c3ac`.
`nikl_2017_curriculum_20201117.json` preserves every source row and field;
reproduce it with `python -m scripts.import_korean_curriculum` in an environment
with openpyxl. Runtime generation uses the checked JSON and needs no spreadsheet
dependency. Do not overwrite source fields when normalizing lookup candidates.

| Level | Vocabulary introduced | Grammar entries |
|---|---:|---:|
| 1 | 735 | 45 |
| 2 | 1,100 | 45 |
| 3 | 1,655 | 67 |
| 4 | 2,200 | 67 |
| 5 | 2,365 | 56 |
| 6 | 2,580 | 56 |

Higher-level reader vocabulary is cumulative. These are curriculum grades,
not an exhaustive list of vocabulary permitted in each TOPIK examination.
Existing dictionary identities remain stable. Homonym numbers differ across
editions; crosswalks require reviewed sense/POS evidence. Productive formations
and lexical units covered by grammar need explicit, independently reviewed
prerequisites; a missing source headword must not silently become Level 1.

## Older compatibility lexicon

`nikl_2003.tsv` is the National Institute of Korean Language's
*한국어 학습용 어휘 목록* (2003), downloaded from
https://korean.go.kr/front/etcData/etcDataView.do?etc_seq=70&mn_id=46&pageIndex=54 .
The site marks it KOGL Type 1 (reuse with attribution). Credit: 국립국어원
(National Institute of Korean Language). The downloaded CP949 text was
converted to UTF-8 with LF line endings; its rows and fields are unchanged.
SHA-256 of the normalized file:
`00249cf0509427fc2e434f318b1a37c599c694b807dc37f8b17b4601e2482c6e`.

The source contains 982 A, 2,111 B, and 2,872 C entries. A was the initial pilot's
beginner vocabulary baseline, **not** an official TOPIK word list. Its
homonym numbers and parts of speech are preserved as exact identities. The
publisher grades reviewed lexical occurrences, not surface endings or Hangul
syllables. Narrative exceptions live in a separate reviewed registry and have
a small budget.
