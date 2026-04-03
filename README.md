# Graded Readers — Chinese & Japanese

Classical literature simplified for language learners. Chinese texts graded by HSK level (1–6), Japanese texts graded by JLPT level (N5–N1).

Each book is retold at multiple difficulty levels using progressively richer vocabulary, so a beginner and an advanced learner can both enjoy the same stories. Every reader follows a **95/5 vocabulary constraint**: at least 95% of word tokens come from the target level or below.

**[Download the Android app](https://github.com/lbm364dl/hsk-graded-readers/releases/latest)**

## Content

### Chinese (HSK)

15 classical works across 6 difficulty levels, plus 21 standalone short readers.

| Book | English | Levels |
|------|---------|--------|
| 三国演义 | Romance of the Three Kingdoms | HSK 1–6 |
| 西游记 | Journey to the West | HSK 1–6 |
| 水浒传 | Water Margin | HSK 1–6 |
| 红楼梦 | Dream of the Red Chamber | HSK 1–6 |
| 聊斋志异 | Strange Tales from a Chinese Studio | HSK 1–6 |
| 唐诗三百首 | Classical Chinese Poems | HSK 1–6 |
| 宋词 | Song Dynasty Lyrics | HSK 1–6 |
| 诗经 | Book of Songs | HSK 1–6 |
| 楚辞 | Songs of Chu | HSK 1–6 |
| 古文观止 | Finest of Ancient Prose | HSK 1–6 |
| 世说新语 | A New Account of Tales of the World | HSK 1–6 |
| 论语 | The Analects | HSK 1–6 |
| 孙子兵法 | The Art of War | HSK 1–6 |
| 成语故事 | Chengyu Stories | HSK 1–6 |
| 民间故事 | Folk Tales | HSK 1–6 |

**90 readers, 1,113 chapters, 1.4M characters** of graded Chinese text.

### Japanese (JLPT)

15 classical and modern works across 5 difficulty levels.

| Book | English | Levels |
|------|---------|--------|
| 芥川龍之介 | Akutagawa Short Stories | N5–N1 |
| 坊っちゃん | Botchan (Natsume Soseki) | N5–N1 |
| 源氏物語 | The Tale of Genji | N5–N1 |
| 平家物語 | The Tale of the Heike | N5–N1 |
| 百人一首 | Hundred Poems by Hundred Poets | N5–N1 |
| 怪談 | Kwaidan — Ghost Stories | N5–N1 |
| 今昔物語 | Tales of Times Now Past | N5–N1 |
| ことわざ | Japanese Proverbs | N5–N1 |
| 走れメロス | Run, Melos! (Dazai Osamu) | N5–N1 |
| 宮沢賢治 | Miyazawa Kenji Stories | N5–N1 |
| 昔話 | Japanese Folk Tales | N5–N1 |
| 太平記 | Chronicle of Great Peace | N5–N1 |
| 竹取物語 | The Tale of the Bamboo Cutter | N5–N1 |
| 徒然草 | Essays in Idleness | N5–N1 |
| 吾輩は猫である | I Am a Cat (Natsume Soseki) | N5–N1 |

**97 readers, 800 chapters, 660K characters** of graded Japanese text.

## Mobile App

A Flutter app for Android and iOS with an interactive reading experience.

### Reading

- **Dual language** — toggle between Chinese and Japanese, preference remembered
- **Word segmentation** — CJK text automatically split into tappable words
- **Japanese deinflection** — conjugated verbs (masu, te-form, volitional, conditional, てしまう, irregular する/来る, etc.) map back to dictionary form
- **Conjugation breakdown** — shows intermediate forms between inflected and dictionary form for educational purposes
- **Progress tracking** — remembers chapter and scroll position per reader
- **Vocabulary saving** — bookmark words while reading, review with flashcards
- **Offline** — all content, dictionaries, and etymology data bundled in the app

### Dictionary

- **Instant lookup** — tap any segmented word for definitions
- **Furigana** — kana readings displayed above kanji (on'yomi in katakana, kun'yomi in hiragana)
- **Recursive lookup** — tap any kanji in definition sheets for nested lookups
- **Multi-kanji compounds** — words like 一生懸命 split into tappable sub-words (一生 + 懸命) when both exist in the dictionary

### Character Etymology

Powered by data from [hanzi-etymology-dict](https://github.com/lbm364dl/hanzi-etymology-dict) (27,500+ characters):

- **Decomposition** — formation type (pictographic, ideographic, phono-semantic, indicative), IDS decomposition, tappable semantic/phonetic components with language-aware readings
- **Historical forms** — SVG glyphs from oracle bone, bronze inscription, and seal script (1,565 characters from Dong Chinese and Wikimedia), viewable in a fullscreen gallery
- **Character series** — phonetic series, semantic series, phonetic siblings, and semantic siblings (up to 30 each, all tappable)
- **Etymology notes** — from multiple sources: Dong Chinese, Make Me a Hanzi, Wiktionary, Shuowen Jiezi

### Build

```bash
cd app
flutter pub get
flutter run               # connected device
flutter build apk         # Android APK
```

Or download a prebuilt APK from [Releases](https://github.com/lbm364dl/hsk-graded-readers/releases).

## Known Limitations

- **CJK Extension B+ characters** — Characters from CJK Unified Ideographs Extension B and beyond (codepoints above U+9FFF/U+4DBF) may not render on most mobile devices. The app filters these from series lists to avoid showing empty boxes, but they may still appear in etymology notes from external sources. The character count shown (e.g. "3 of 22") reflects this: 3 renderable characters out of 22 total in the source data.
- **Japanese deinflection** — Rule-based, not morphological analysis. Covers all common conjugation patterns but may miss rare or archaic forms. Some kana-only words may produce false deinflection matches.

## Project Structure

```
├── app/                    Flutter mobile app
│   ├── lib/                Dart source
│   ├── assets/             Bundled content + dictionaries + etymology (JSON)
│   └── test/               Dart tests (270+ tests)
├── books/                  Chinese source texts (Project Gutenberg)
├── data/words/             HSK 3.0 vocabulary CSVs (levels 1–7+)
├── jlpt/                   Japanese content pipeline
│   ├── data/words/         JLPT vocabulary CSVs (N5–N1)
│   └── output/             Generated JLPT graded readers
├── output/                 Generated HSK graded readers
├── readers/                Standalone short readers (HSK 1–6)
├── src/                    Python library
│   ├── abridger/           PDF/EPUB/TXT parser and book abridger
│   ├── generator/          95/5 constraint validator
│   ├── segmentation/       Chinese word segmentation
│   └── vocab/              HSK/JLPT vocabulary loader
└── tests/                  Python test suite
```

## App Assets

| File | Size | Contents |
|------|------|----------|
| content.json | 3.9 MB | Chinese graded readers (90 readers, 1,113 chapters) |
| content_ja.json | 2.0 MB | Japanese graded readers (97 readers, 800 chapters) |
| dictionary.json | 2.6 MB | Chinese dictionary (pinyin, HSK level, definitions) |
| dictionary_ja.json | 1.4 MB | Japanese dictionary (readings, JLPT level, definitions) |
| etymology.json | 12.9 MB | Character etymology (27,500+ entries with decomposition, series, notes) |
| glyphs.json | 11.5 MB | Historical character SVGs (1,565 characters, oracle/bronze/seal) |

## Proficiency Levels

### HSK

| Level | Cumulative Words | Description |
|-------|-----------------|-------------|
| HSK 1 | ~500 | Beginner — simple sentences, daily life |
| HSK 2 | ~1,200 | Elementary — short paragraphs, common topics |
| HSK 3 | ~2,200 | Intermediate — connected narrative |
| HSK 4 | ~3,200 | Upper intermediate — longer texts |
| HSK 5 | ~4,200 | Advanced — complex narrative |
| HSK 6 | ~5,400 | Proficient — near-native reading |

### JLPT

| Level | Cumulative Words | Description |
|-------|-----------------|-------------|
| N5 | ~800 | Beginner — basic expressions |
| N4 | ~1,500 | Elementary — everyday situations |
| N3 | ~3,750 | Intermediate — general topics |
| N2 | ~6,000 | Upper intermediate — news, essays |
| N1 | ~10,000 | Advanced — abstract, literary texts |

## Source Texts

Chinese source texts are public domain from [Project Gutenberg](https://www.gutenberg.org/). Japanese texts are adapted from public domain works by Akutagawa, Natsume Soseki, Dazai Osamu, Miyazawa Kenji, and classical Japanese literature. Etymology data from [hanzi-etymology-dict](https://github.com/lbm364dl/hanzi-etymology-dict).

## License

Source texts are in the public domain. The graded reader adaptations and app code are provided as-is for educational use.
