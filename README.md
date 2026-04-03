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

- **Dual language** — toggle between Chinese and Japanese
- **Dictionary lookup** — tap any word for instant definitions with pinyin/furigana
- **Word segmentation** — CJK text automatically split into tappable words
- **Japanese deinflection** — conjugated verbs map back to dictionary form with educational breakdown showing intermediate conjugation steps
- **Furigana** — kana readings displayed above kanji in definition sheets
- **Progress tracking** — remembers chapter and scroll position per reader
- **Vocabulary saving** — bookmark words while reading, review with flashcards
- **Offline** — all content and dictionaries bundled in the app

### Build

```bash
cd app
flutter pub get
flutter run               # connected device
flutter build apk         # Android APK
```

Or download a prebuilt APK from [Releases](https://github.com/lbm364dl/hsk-graded-readers/releases).

## Project Structure

```
├── app/                    Flutter mobile app
│   ├── lib/                Dart source
│   ├── assets/             Bundled content + dictionaries (JSON)
│   └── test/               Dart tests (265 tests)
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

Chinese source texts are public domain from [Project Gutenberg](https://www.gutenberg.org/). Japanese texts are adapted from public domain works by Akutagawa, Natsume Soseki, Dazai Osamu, Miyazawa Kenji, and classical Japanese literature.

## License

Source texts are in the public domain. The graded reader adaptations and app code are provided as-is for educational use.
