# Generate Graded Reader with Word Segmentation

You are generating a Chinese graded reader — a simplified retelling of a classical Chinese text adapted for language learners at a specific HSK level. You will produce **two outputs**: the story text (markdown) and a word-by-word segmentation (JSON).

## Inputs

- **Source work**: The title and a summary/text of the original Chinese literary work
- **HSK level**: 1–6 (determines vocabulary and complexity constraints)
- **Book ID**: e.g. `sanguoyanyi`, `xiyouji`
- **Glossary**: Character names, place names, and story-essential terms that are permitted regardless of HSK level

## Output 1: Graded Reader Text (Markdown)

Write the story as a markdown file. Follow these constraints:

### Vocabulary & Grammar by Level

| Level | Cumulative Words | Sentence Length | Description |
|-------|-----------------|-----------------|-------------|
| HSK1 | ~500 | 3–8 chars | Extremely simple. Short sentences. Lots of repetition. Use 不在了 for death, 坏人 for villain, etc. |
| HSK2 | ~1,272 | 5–12 chars | Simple connectors (因为/所以/虽然/但是/如果). More descriptive. |
| HSK3 | ~2,245 | 6–15 chars | Richer narrative. Military/political vocab OK. Dialogue. |
| HSK4 | ~3,245 | 8–20 chars | Detailed storytelling. Complex sentences. Rich dialogue. Literary flavor. |
| HSK5 | ~4,316 | 10–25 chars | Near-literary. Idioms. Abstract concepts. |
| HSK6 | ~5,456 | 10–30 chars | Full literary adaptation. Classical echoes. |

### Length Progression

The texts should grow incrementally across levels. Use these approximate targets:

| Level | Target Size | Chapters |
|-------|-------------|----------|
| HSK1 | ~10KB | 8–10 |
| HSK2 | ~20KB | 10–12 |
| HSK3 | ~50KB | 12–14 |
| HSK4 | ~200KB | 18–20 |
| HSK5 | ~350KB | 40–50 |
| HSK6 | ~450KB | 40–50 |

### Format

```markdown

## 第一章 · Title

Paragraph text here.

More paragraphs separated by blank lines.

---

## 第二章 · Title

...

---

**Closing quote or theme.**

**~ 完 ~**
```

- Use `## ` for chapter headings
- Use `---` between chapters
- Use `"` and `"` (fullwidth curly quotes) for dialogue
- Bold `**` for epilogue/closing lines
- Start file with a blank line (no frontmatter)

### Writing Principles

- **Tell the whole story.** Cover all major plot points even at HSK1. Lower levels simplify and compress; higher levels expand and add detail.
- **Repetition is good at low levels.** HSK1/2 learners benefit from seeing the same structures repeatedly.
- **Circumlocution over unknown words.** At HSK1, Zhuge Liang is "很会想的人" (a person who thinks well). At HSK3, he's "军师" (strategist).
- **Names are always OK.** Character names and place names from the glossary can be used at any level.
- **不在了 = died** at HSK1/2. Use age-appropriate euphemisms at low levels.
- **Progressive detail.** HSK1 says "they fought." HSK4 describes the battle formation, the weather, the strategy.

## Output 2: Word Segmentation (JSON)

For each level, produce a `_segmented.json` file — a JSON array where every element of the text is segmented into individual words with pinyin and definitions.

### Segment Format

```json
[
  {"w": "第一", "p": "dì yī", "d": "first"},
  {"w": "章", "p": "zhāng", "d": "chapter"},
  {"w": " · "},
  {"w": "三", "p": "sān", "d": "three"},
  {"w": "个", "p": "gè", "d": "(measure word)"},
  {"w": "好", "p": "hǎo", "d": "good"},
  {"w": "朋友", "p": "péngyou", "d": "friend(s)"},
  {"w": "\n\n"},
  {"w": "很", "p": "hěn", "d": "very"},
  {"w": "早", "p": "zǎo", "d": "early"}
]
```

Each object has:
- `w` — the Chinese text (word, punctuation, or structural marker)
- `p` — pinyin with tone marks (omitted for punctuation/breaks)
- `d` — brief English definition (omitted for punctuation/breaks)

### Segmentation Rules

**This is LLM-based segmentation, not algorithmic.** Use your understanding of Chinese to segment in a way most useful for learners.

1. **Compound words stay together**: 高兴, 朋友, 觉得, 男人, 晚上, 看到, 回到, 国家, 百姓, 聪明, 办法, 朝廷, 将军, 投降, 胜利, 越来越, etc.
2. **Grammar combos split**: 不好 → 不 + 好; 很多 → 很 + 多; 太大 → 太 + 大
3. **没有 stays together** (it's a word, not 没 + 有)
4. **Particles get contextual definitions**:
   - 了: "(completed action)", "(change of state)", "(new situation)", "(excessive degree)" etc.
   - 的: "(possessive particle)", "(particle linking modifier to noun)", "(nominalizer)" etc.
   - 着: "(continuous state)", "(manner)" etc.
   - 得: "(complement particle)" etc.
   - 过: "(past experience)" etc.
5. **Personal names as single units**: 刘备, 关羽, 诸葛亮, etc.
6. **Place names as single units**: 荆州, 赤壁, etc.
7. **Verb-complement structures stay together**: 看到, 回到, 打过, 听到, 走进, 跑出来, 打不过, etc.
8. **Four-character idioms stay together**: 桃园结义, 草船借箭, etc.

### Structural Elements

| Element | JSON |
|---------|------|
| Paragraph break | `{"w": "\n\n"}` |
| Chapter separator | `{"w": "---"}` with `{"w": "\n\n"}` before and after |
| Punctuation | `{"w": "，"}` — no p or d fields |
| Bold markers | `{"w": "**"}` |
| Chinese quotes | `{"w": "\u201c"}` and `{"w": "\u201d"}` — **must use unicode escapes, never literal curly quotes** |

### Reconstructability

The original markdown text must be perfectly reconstructable by concatenating all `w` values in order:
```
text = "".join(segment["w"] for segment in segments)
```

## File Naming

For a book with ID `sanguoyanyi` at HSK level 3:
- Text: `output/chinese/sanguoyanyi/hsk3_sanguoyanyi.md`
- Segmentation: `output/chinese/sanguoyanyi/hsk3_sanguoyanyi_segmented.json`

## Workflow for Agents

When generating both outputs for a given level:

1. **Write the markdown** first (the graded reader text)
2. **Segment chapter by chapter** — for texts longer than ~20KB, process each chapter as a separate unit and combine:
   - Write each chapter's segments to a temp file (`/tmp/hsk{N}_ch{M}.json`)
   - Each chapter after the first starts with `{"w":"---"},{"w":"\n\n"}` 
   - Combine all chapters into the final `_segmented.json`
3. **Validate** the combined JSON is parseable and segments concatenate to match the source

### Parallelization

For efficiency with larger texts:
- The markdown can be written by one agent
- Segmentation can be parallelized: one agent per chapter, all running simultaneously
- A combine step merges the chapter JSONs into the final file

### Chinese Quote Encoding Warning

The Write tool converts Unicode curly quotes (`"` U+201C, `"` U+201D) to ASCII straight quotes. This corrupts JSON since `{"w":""}` becomes three `"` in a row. **Always use the escape sequences** `\u201c` and `\u201d` in JSON output.
