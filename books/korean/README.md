# Hong Gildong source editions

The generation input is the complete original-text transcription of the
**30-sheet Gyeongpan edition**, supplied by the Jikji Project on Wikisource.
We pin [revision 460078](https://ko.wikisource.org/w/index.php?title=홍길동전_(30장_경판본)&oldid=460078).
[The provenance discussion](https://ko.wikisource.org/wiki/토론:홍길동전_(30장_경판본))
identifies the Jikji source and the complete public-domain original.
Wikisource marks the original PD-old-100; site contributions are under
CC BY-SA 4.0. Retain the Wikisource/Jikji attribution and revision link.
The adaptations are newly written modern Korean, not a modern translation.

`honggildong/original.wiki` preserves the pinned raw transcription;
`original.txt` extracts its original Hangul without modernization.
`source.json` records both checksums and Unicode source spans. Source units
are pipeline divisions, not chapter headings in the original. The first two
units are mapped; only the first chapter is currently requested for publication.
Run `.venv/bin/python -m pipeline.korean_sources` to verify the local snapshot,
or add `--import` to redownload the pinned revision. A changed revision fails.

The archived `honggildong_1920.pdf` is a **different, 24-sheet edition**, the
1920 Baek Du-yong woodblock scan held by the National Library of Korea,
control number `CNTS-00047987469`. It is not the generation input.
Its SHA-256 is `bc837d646005cb06b44f4bc564caf4d9132c3a0c732824e8f3bdbfba0d6eb8be`.
[Scan and public-domain record](https://commons.wikimedia.org/wiki/File:CNTS-00047987469_%ED%99%8D%EA%B8%B8%EB%8F%99%EC%A0%84.pdf).
Do not mix plot details from these editions.

Reviewed interpretation notes in `honggildong/source-notes.json` retain research
references and exact source spans. In particular, the father's apparent given
name is an unspecified-name placeholder, not a literal name “Moe/Mo”; see
[Yu Gwang-su (2019), page 442, footnote 16](https://journal.kci.go.kr/insewtdgu/archive/articlePdf?artiId=ART002526509).
Use his attested surname/title or family relationship in beginner adaptations.
The manifest fingerprints these notes so updates invalidate stale run evidence.
