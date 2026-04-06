# Graded Reader Generation Pipeline

A systematic process for producing quality-controlled graded reader texts at each HSK/JLPT level.

## The Problem

AI language models struggle to constrain their output to a specific vocabulary list because:
1. **Word segmentation is ambiguous** — different segmenters produce different tokens, so checking against a word list gives inconsistent results
2. **Word lists are large and hard to internalize** — an AI can't reliably memorize 3000+ words and check each one as it writes
3. **Post-hoc validation depends on segmentation too** — if you validate with jieba/sudachi and the AI "thought" in different segments, you get false positives/negatives

## The Solution: Character-Based Constraints

Instead of word lists, we use **character lists**:
- Extract all unique CJK characters (hanzi/kanji) from each level's word list
- Give the AI a flat string of ~300-1500 allowed characters
- Validate output by checking each character individually — **no segmentation needed**

This works because:
- A character is unambiguously in the set or not — no tokenization disputes
- The character set derived from the word list is a good proxy for level difficulty
- It's easy for both the AI and the validator to check
- The 95/5 rule still applies: ≤5% of characters may be outside the allowed set

## Pipeline Overview

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Word Lists  │────▶│ Extract Charsets  │────▶│  Char Sets   │
│  (CSV files) │     │                  │     │  (per level) │
└──────────────┘     └──────────────────┘     └──────┬───────┘
                                                     │
┌──────────────┐     ┌──────────────────┐            │
│ Source Text  │────▶│  Generate Prompt  │◀───────────┘
│ (original)   │     │                  │
└──────────────┘     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │   AI Generation  │  (Claude, GPT, etc.)
                     │   (manual step)  │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐     ┌──────────────┐
                     │  Validate Output │────▶│   Report     │
                     │  (char-level)    │     │  (pass/fail) │
                     └────────┬─────────┘     └──────────────┘
                              │
                         fail │
                              ▼
                     ┌──────────────────┐
                     │  Retry Prompt    │  (includes failed chars
                     │  (with feedback) │   + previous attempt)
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │   AI Retry       │  (up to 3 iterations)
                     └──────────────────┘
```

## Quick Start

```bash
# 1. Extract character sets from word lists (one-time setup)
python3 -m pipeline.extract_characters

# 2. Validate all existing readers
python3 -m pipeline.validate_all

# 3. Validate a single reader
python3 -m pipeline.validate_text readers/hsk4_02_kong_yiji.md -l hsk4

# 4. Generate a prompt for creating a new reader
python3 -m pipeline.generate_prompt \
    books/chinese/kong_yiji_original.txt \
    -l hsk4 --language chinese --title "孔乙己"

# 5. Run the full pipeline (interactive)
python3 -m pipeline.run_pipeline \
    --source books/chinese/kong_yiji_original.txt \
    --level hsk4 --language chinese --title "孔乙己"
```

## Directory Structure

```
pipeline/
├── extract_characters.py   # Step 1: word lists → character sets
├── generate_prompt.py      # Step 2: build AI prompts with char constraints
├── validate_text.py        # Step 3: validate output (char-level 95/5 check)
├── validate_all.py         # Batch validate all existing readers
├── run_pipeline.py         # Orchestrator: ties all steps together
├── charsets/               # Generated character sets
│   ├── hsk/
│   │   ├── hsk1_chars_from_words.txt   # 300 chars
│   │   ├── hsk2_chars_from_words.txt   # 600 chars (cumulative)
│   │   ├── ...
│   │   └── charset_summary.json
│   └── jlpt/
│       ├── n5_chars_from_words.txt     # 439 kanji
│       ├── ...
│       └── charset_summary.json
├── prompts/                # Generated prompts (gitignored)
├── outputs/                # AI-generated outputs (gitignored)
└── reports/                # Validation reports (gitignored)

books/
├── chinese/
│   ├── kong_yiji_original.txt      # 鲁迅 - 孔乙己 (from 呐喊)
│   ├── luxun_nahan.txt             # 鲁迅 - 呐喊 (Project Gutenberg)
│   ├── xiyouji.txt                 # 西游记 (Project Gutenberg)
│   ├── sanguoyanyi.epub            # 三国演义
│   ├── liaozhai_zhiyi.txt          # 聊斋志异
│   └── liaozhai_selected.txt       # 聊斋志异 selected stories
└── japanese/
    ├── hashire_merosu.txt          # 太宰治 - 走れメロス (Aozora Bunko)
    ├── kokoro.txt                  # 夏目漱石 - こころ (Aozora Bunko)
    ├── wagahai_wa_neko_de_aru.txt  # 夏目漱石 - 吾輩は猫である (Aozora Bunko)
    └── chuumon_no_oi_ryouriten.txt # 宮沢賢治 - 注文の多い料理店 (Aozora Bunko)
```

## Current Validation Results

All HSK readers pass. Some JLPT readers fail — these are candidates for regeneration:

| Status | Level | In-level | File |
|--------|-------|----------|------|
| FAIL | N5 | 91.8% | n5_07_kaimono.md |
| FAIL | N5 | 93.8% | n5_05_tanjoubi.md |
| FAIL | N4 | 87.9% | n4_02_ryokou.md |
| FAIL | N4 | 89.3% | n4_01_shuumatsu_no_keikaku.md |
| FAIL | N3 | 92.9% | n3_02_matsuri.md |
| FAIL | N3 | 94.7% | n3_04_manga_to_anime.md |
| FAIL | N2 | 93.7% | n2_01_wabi_sabi.md |

## How the Prompt Works

The key insight: instead of saying "use only these 3000 words" (which requires the AI to segment its own output and check each word), we say "use only these 1200 characters" (which is a simple lookup the AI can do character by character).

The generated prompt includes:
1. **The source text** — what to simplify
2. **The allowed character set** — laid out in rows of 50 for visual scanning
3. **Writing guidelines** — sentence length, text length, level-appropriate grammar
4. **Strategy tips** — explicit instructions to check each character before writing
5. **On retries** — the previous attempt + list of offending characters

## Maximizing Level Compliance: Best Practices

### For the AI prompt

1. **Present the charset visually** — rows of 50 chars that the AI can scan
2. **Emphasize the check-before-write strategy** — tell the AI to mentally verify each character
3. **Allow a 5% margin** — perfection is impossible; proper nouns and key concepts need flexibility
4. **Provide glossary exceptions** — pre-approve characters for names, places, story-specific terms
5. **On retries, show exactly which characters failed** — so the AI knows what to replace

### For validation

1. **Character-level is better than word-level** — no segmentation ambiguity
2. **Use the cumulative charset** — HSK4 includes HSK1+2+3+4 characters
3. **Allow glossary overrides** — some stories need specific characters (孔乙己 needs 乙己)
4. **Track per-character frequency** — a single rare character used once is fine; one used 20 times is a problem

### For the overall process

1. **Start from real source texts** — simplifying a real story produces better narratives than asking the AI to write from scratch
2. **Iterate with feedback** — 2-3 rounds of generate→validate→retry typically gets to 95%+
3. **Lower levels need more iterations** — HSK1 (300 chars) is much harder to constrain than HSK6 (1800 chars)
4. **Japanese is harder** — kanji derived from JLPT word lists may miss common kanji that appear in everyday text but aren't in the vocabulary lists
5. **Human review is still essential** — the pipeline ensures character compliance, but meaning, naturalness, and engagement require human judgment

## Source Texts

### What to use as source material

For **literary adaptations** (higher levels):
- Public domain classics from Project Gutenberg (Chinese) or Aozora Bunko (Japanese)
- The original text gets simplified and constrained to level-appropriate characters
- Examples: 孔乙己 → HSK4, 骆驼祥子 → HSK5, 走れメロス → N2

For **original content** (lower levels):
- The source "text" is really just a topic prompt (e.g., "daily routine", "my family")
- Create a short source outline instead of a full text
- The AI generates from the outline rather than simplifying an existing work

### Where to find open-source texts

**Chinese:**
- [Project Gutenberg](https://www.gutenberg.org/) — 西游记, 三国演义, 鲁迅作品集
- [Chinese Text Project (ctext.org)](https://ctext.org/) — classical Chinese texts
- [Wikisource Chinese](https://zh.wikisource.org/) — modern and classical works

**Japanese:**
- [Aozora Bunko (青空文庫)](https://www.aozora.gr.jp/) — thousands of public domain works
- [Wikisource Japanese](https://ja.wikisource.org/) — additional texts

### Texts still needed

| Reader | Source | Status |
|--------|--------|--------|
| hsk1-3 original topics | Topic outlines | ✓ Original content, no source needed |
| hsk4_02 孔乙己 | 鲁迅《呐喊》 | ✓ Downloaded (Project Gutenberg) |
| hsk5_02 宋词 | Various Song Ci | Needs: curated selection of public domain poems |
| hsk5_03 骆驼祥子 | 老舍 | Not public domain in most jurisdictions |
| hsk6_01 教育 | Original essay | ✓ Original content |
| hsk6_02 现代诗 | 徐志摩, 冰心 | Public domain poems — need curation |
| JLPT N5-N3 topics | Topic outlines | ✓ Original content |
| JLPT N2-N1 literary | Aozora Bunko | ✓ Downloaded: こころ, 走れメロス, 注文の多い料理店 |

## Extending the Pipeline

### Adding a new language

1. Create word list CSVs in `data/words/` with a `word` column
2. Add a `build_*_charsets()` function in `extract_characters.py`
3. Add level metadata JSON
4. Run `extract_characters` to generate charsets

### Automating the AI step

The current pipeline requires manual AI interaction. To automate:

```python
# Future: call Claude API directly
import anthropic

client = anthropic.Anthropic()
prompt = Path("pipeline/prompts/hsk4_kong_yiji_v1_prompt.md").read_text()

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    messages=[{"role": "user", "content": prompt}],
)

output = response.content[0].text
Path("pipeline/outputs/hsk4_kong_yiji_v1.txt").write_text(output)
```

This would allow fully automated generate→validate→retry loops.

### Adding word-level validation as a secondary check

The character-level check is the primary gate, but word-level segmentation
can still be useful as a secondary quality signal. The existing
`src/generator/constraints.py` (HSK) and `jlpt/src/generator/constraints.py`
(JLPT) provide word-level checks that can be run after the character check passes.
