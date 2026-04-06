# Graded Reader Pipeline

## Overview

This pipeline systematically generates and validates graded reader texts using
**character-level constraints**. Characters are unambiguous to check (no
segmentation needed), unlike word-level constraints.

### How it works

1. **Source text** → original literary text (books/chinese/, books/japanese/)
2. **Character set** → allowed characters for the target level (pipeline/charsets/)
3. **AI prompt** → instructs the AI to rewrite within the charset (pipeline/generate_prompt.py)
4. **AI generation** → simplified text (pipeline/outputs/)
5. **Validation** → character-by-character 95/5 check (pipeline/validate_text.py)
6. **Iteration** → if validation fails, retry with feedback showing above-level chars

### The 95/5 rule

At most 5% of CJK characters in the output may be outside the allowed set for
a given level. Glossary characters (proper nouns, place names) are excluded
from this count.

## Quick start

```bash
# Extract character sets from word/character CSVs
python3 -m pipeline.extract_characters

# Validate a single reader
python3 -m pipeline.validate_text output/xiyouji/hsk3_xiyouji.md -l hsk3

# Validate with glossary
python3 -m pipeline.validate_text output/xiyouji/hsk3_xiyouji.md -l hsk3 \
  --extra-chars "$(grep -v '^#' output/xiyouji/glossary.txt | tr -d '\n')"

# Validate all readers
python3 -m pipeline.validate_all

# Generate a prompt for AI
python3 -m pipeline.generate_prompt books/chinese/xiyouji_ch1.txt \
  -l hsk3 --language chinese --title "西游记"
```

## Directory structure

```
pipeline/
├── charsets/           # Generated character sets per level
│   ├── hsk/            # hsk1_chars.txt .. hsk7to9_chars.txt
│   └── jlpt/           # n5_chars.txt .. n1_chars.txt
├── glossaries/         # Per-reader glossary files (proper nouns)
├── outputs/            # AI-generated texts (validated intermediate outputs)
├── prompts/            # Generated prompts (gitignored)
├── reports/            # Validation reports (gitignored)
├── extract_characters.py
├── generate_prompt.py
├── validate_text.py
├── validate_all.py
└── run_pipeline.py     # Interactive orchestrator
```

## Current state and next steps

### What's been done

**Pipeline infrastructure** is complete and tested:
- Character extraction from both official char CSVs and word-list CSVs
- Prompt generation with charset constraints
- Validation with glossary support
- Batch validation

**Validated chapter-1 outputs** exist in `pipeline/outputs/` for:
- **xiyouji** HSK1-6 (99.6-100% in-level)
- **liaozhai** HSK3-6 (96.8-100% in-level, 画皮 + 聂小倩)
- **sanguoyanyi** HSK3-6 (95.7-99.2% in-level, 桃园结义)
- **JLPT readers**: n3_chuumon, n2_merosu, hsk3_xiyouji, hsk6_guxiang, hsk5_songci

**Individual readers** in `readers/` and `jlpt/readers/` that pass validation:
- hsk3_05_xiyouji.md, hsk4_02_kong_yiji.md, hsk5_02_songci.md
- hsk5_03_niexiaoqian.md, hsk6_02_modern_poetry.md, hsk6_03_guxiang.md
- n5_05_tanjoubi.md, n5_07_kaimono.md, n2_01_wabi_sabi.md
- n2_04_merosu.md, n3_06_chuumon.md

### What still needs to be done

**The main remaining task**: the `output/` directory has 90 readers (15 series
× 6 HSK levels) that were AI-generated from memory without character
constraints. Most of them (79/90) FAIL the 95/5 validation.

The validated texts in `pipeline/outputs/` only cover the **opening chapter**
of each novel. The original `output/` readers are **abridged versions of the
full novels** — for example, sanguoyanyi HSK4 covers 25+ chapters from 桃园结义
through 三国归晋.

**The right approach for fixing these is:**
1. Keep the full-novel story scope from the existing output/ readers
2. Do targeted character-level fixes: find above-level characters and replace
   them with in-level synonyms/paraphrases
3. Use the validated chapter-1 versions in `pipeline/outputs/` as reference for
   the writing style at each level
4. Work level by level — lower levels need more aggressive simplification

**Series with source texts** (prioritize these):
- xiyouji (6 readers) ← books/chinese/xiyouji_ch1.txt, xiyouji_ch4to7.txt
- sanguoyanyi (6 readers) ← books/chinese/sanguoyanyi.epub (ch1 extracted to sanguoyanyi_ch1.txt)
- liaozhai (6 readers) ← books/chinese/liaozhai_zhiyi.txt, niexiaoqian_original.txt
- songci (6 readers) ← books/chinese/songci_collection.txt + individual poems

**Series without source texts** (66 readers, lower priority):
chengyugushi, chuci, guwenguanzhi, hongloumeng, lunyu, minjiangushi,
shijing, shishuoxinyu, shuihuzhuan, sunzibingfa, tangshi

**Songci special case**: These include original classical Chinese poems that
can't be simplified. Need a different validation approach — perhaps exclude
the poem text from the character count, or add poem characters to the glossary.

### Glossary files

Each series in `output/` has a `glossary.txt` with proper nouns and essential
story vocabulary. These characters are excluded from the 95/5 count. When
regenerating readers, check the glossary and add missing proper nouns as needed.

Updated glossaries:
- output/xiyouji/glossary.txt — added 齐天大圣, 菩提老祖
- output/liaozhai/glossary.txt — added 燕赤霞, 陈氏, 兰若寺, 拂尘, 乞丐, 葬
- output/sanguoyanyi/glossary.txt — added 涿郡, 涿县, 讨伐, 黄巾, 朝廷, 誓, 祭, 皇帝, 皇室
