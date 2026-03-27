# HSK 分级阅读 — HSK Graded Readers

Chinese classical literature simplified for HSK learners at levels 1–6.

Each book is retold at six difficulty levels using progressively richer vocabulary, so a beginner (HSK 1, ~500 words) and an advanced learner (HSK 6, ~5400 words) can both enjoy the same stories.

## Content

| Book | Chinese | English | Levels |
|------|---------|---------|--------|
| 三国演义 | sanguoyanyi | Romance of the Three Kingdoms | HSK 1–6 |
| 聊斋志异 | liaozhai | Strange Tales from a Chinese Studio | HSK 1–6 |
| 唐诗三百首 | tangshi | Classical Chinese Poems | HSK 1–6 |
| 西游记 | xiyouji | Journey to the West | HSK 1–6 |

Plus 10 standalone short readers in `readers/` covering everyday topics (HSK 1–6).

**Total:** 34 reader entries, 399 chapters, 850K+ characters of graded Chinese text.

## The 95/5 Rule

Every reader follows the **95/5 vocabulary constraint**: at least 95% of word tokens must come from the target HSK level or below. The remaining 5% covers:

- **Glossary words** — proper nouns, character names, place names (`glossary.txt`)
- **Taught vocabulary** — above-level words explicitly introduced at each level (`taught_vocab.txt`)

This is validated by 67 automated tests.

## Project Structure

```
├── app/                  # Flutter mobile app (iOS, Android, Web, Desktop)
│   ├── lib/              # Dart source code
│   └── assets/           # Bundled content (content.json)
├── books/                # Source texts from Project Gutenberg
├── data/words/           # HSK 3.0 vocabulary CSVs (levels 1–7+)
├── output/               # Generated graded readers (4 books × 6 levels)
│   ├── sanguoyanyi/
│   ├── liaozhai/
│   ├── tangshi/
│   └── xiyouji/
├── readers/              # Standalone short readers (10 files)
├── src/                  # Python library
│   ├── abridger/         # PDF/EPUB/TXT parser and book abridger
│   ├── generator/        # 95/5 constraint validator
│   ├── segmentation/     # Chinese word segmentation (jieba + HSK dict)
│   └── vocab/            # HSK vocabulary loader and lookup
└── tests/                # pytest test suite (67 tests)
```

## Getting Started

### Python tools (analysis, validation, CLI)

```bash
pip install -r requirements.txt
python -m pytest tests/          # run all tests
python -m src.cli validate output/xiyouji/hsk3_xiyouji.md --level 3
python -m src.cli analyze output/sanguoyanyi/hsk1_sanguoyanyi.md
```

### Flutter app

```bash
cd app
flutter pub get
flutter run -d chrome     # web
flutter run               # connected device (Android/iOS)
flutter build apk         # Android APK
flutter build ios         # iOS (requires macOS + Xcode)
```

## HSK Levels at a Glance

| Level | Cumulative Words | Description |
|-------|-----------------|-------------|
| HSK 1 | ~500 | Absolute beginner — simple sentences, basic daily life |
| HSK 2 | ~1,200 | Elementary — short paragraphs, common topics |
| HSK 3 | ~2,200 | Intermediate — connected narrative, wider vocabulary |
| HSK 4 | ~3,200 | Upper intermediate — longer texts, abstract topics |
| HSK 5 | ~4,200 | Advanced — complex narrative, literary expression |
| HSK 6 | ~5,400 | Proficient — near-native reading, classical references |

## Source Texts

All source texts are public domain, downloaded from [Project Gutenberg](https://www.gutenberg.org/):

- 三国演义 (#23950) — Luo Guanzhong
- 聊斋志异 (#51828) — Pu Songling
- 西游记 (#23962) — Wu Cheng'en

## License

Source texts are in the public domain. The graded reader adaptations and app code are provided as-is for educational use.
