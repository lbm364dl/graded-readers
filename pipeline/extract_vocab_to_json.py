#!/usr/bin/env python3
"""Extract vocabulary sections from graded reader .md files into JSON.

Strips vocab tables, character introductions, and key concept sections
from the main text files, saving them as structured JSON alongside.

Output per book directory:
  - glossary.json          (converted from glossary.txt)
  - {level}_vocab.json     (extracted vocab tables + metadata sections)
  - metadata.json          (book info)
  - .md files              (cleaned to pure reading text)
"""

import json
import re
from pathlib import Path


def parse_glossary_txt(glossary_path: Path) -> dict:
    """Convert glossary.txt to structured JSON."""
    categories = {}
    current_category = "uncategorized"

    for line in glossary_path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# ") and not line.startswith("# 西") and not line.startswith("# 三"):
            # Category header
            current_category = line[2:].strip()
            if current_category not in categories:
                categories[current_category] = []
        elif not line.startswith("#"):
            if current_category not in categories:
                categories[current_category] = []
            categories[current_category].append(line)

    return categories


def parse_vocab_table(lines: list[str]) -> list[dict]:
    """Parse a markdown table into a list of word entries."""
    words = []
    header_found = False
    columns = []

    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]

        if not header_found:
            # First row is header
            columns = [c.lower() for c in cells]
            header_found = True
            continue

        if all(c.replace("-", "").replace(":", "").strip() == "" for c in cells):
            # Separator row
            continue

        entry = {}
        for i, col in enumerate(columns):
            if i < len(cells):
                val = cells[i].strip()
                if val:
                    # Normalize column names
                    key = col
                    if key in ("词语", "字", "字/词"):
                        key = "word"
                    elif key in ("拼音", "读音", "pinyin"):
                        key = "pinyin"
                    elif key in ("意思", "释义", "现代义", "文言义"):
                        key = col  # keep original for specialized tables
                    elif key in ("例句",):
                        key = "example"
                    entry[key] = val

        if entry:
            words.append(entry)

    return words


def extract_and_strip(md_path: Path) -> tuple[str, list[dict]]:
    """Extract vocab/metadata sections from a .md file.

    Returns (cleaned_text, extracted_sections).
    """
    text = md_path.read_text("utf-8")
    lines = text.split("\n")

    cleaned_lines = []
    extracted_sections = []

    i = 0
    in_vocab_section = False
    current_section = None
    current_section_lines = []

    # Patterns that indicate vocab/metadata sections to extract
    vocab_headers = re.compile(
        r"^#{1,3}\s*(?:[一二三四五六七八九十]+[、.]\s*)?"
        r"(生词表|生词学习|生词详解|生词|词汇|"
        r"Key Vocabulary|Detailed Vocabulary|Vocabulary|"
        r"主要概念|主要人物|Key Concepts|Main Characters|"
        r"字词注释|逐字注释|词汇扩展|文言语法要点|"
        r"Vocabulary List|"
        r"語注|古語注|ことば|古語総まとめ|古語文法まとめ|主要語注|"
        r"重要な和歌|参考文献)",
        re.IGNORECASE
    )

    # Section headers that are part of the story/commentary (keep these)
    story_headers = re.compile(
        r"^#{1,3}\s*(第|章|回|篇|一[、.]|二[、.]|三[、.]|四[、.]|五[、.]|六[、.]|七[、.]|八[、.]|九[、.]|十|"
        r"词人介绍|词人传记|词人小传|诗人介绍|诗人小传|"
        r"创作背景|历史背景|过去的情况|"
        r"词意理解|词意详解|诗意理解|诗的意思|"
        r"文学分析|文学特点|文学赏析|写法特点|"
        r"文化意义|文化背景|你知道吗|影响|传承|"
        r"高级分析|哲学思考|后来的影响)"
    )

    while i < len(lines):
        line = lines[i]

        # Check if this is a vocab section header
        if vocab_headers.match(line.strip()):
            # Start extracting
            if current_section:
                extracted_sections.append({
                    "header": current_section["header"],
                    "content": current_section_lines,
                })

            current_section = {"header": line.strip()}
            current_section_lines = [line]
            in_vocab_section = True
            i += 1
            continue

        if in_vocab_section:
            # Check if we've hit a new non-vocab header (end of vocab section)
            is_new_header = line.strip().startswith("#") and not vocab_headers.match(line.strip())
            is_separator = line.strip() == "---"

            if is_new_header:
                # Save extracted section
                extracted_sections.append({
                    "header": current_section["header"],
                    "content": current_section_lines,
                })
                current_section = None
                current_section_lines = []
                in_vocab_section = False
                # Don't skip this line — it belongs to cleaned text
                cleaned_lines.append(line)
                i += 1
                continue
            elif is_separator and i + 1 < len(lines) and lines[i + 1].strip().startswith("#"):
                # Separator before a new chapter — end vocab section
                extracted_sections.append({
                    "header": current_section["header"],
                    "content": current_section_lines,
                })
                current_section = None
                current_section_lines = []
                in_vocab_section = False
                cleaned_lines.append(line)
                i += 1
                continue
            else:
                current_section_lines.append(line)
                i += 1
                continue

        cleaned_lines.append(line)
        i += 1

    # Don't forget last section
    if current_section:
        extracted_sections.append({
            "header": current_section["header"],
            "content": current_section_lines,
        })

    # Parse extracted sections into structured data
    parsed_sections = []
    for section in extracted_sections:
        header = section["header"]
        content_text = "\n".join(section["content"])

        # Try to parse tables
        words = parse_vocab_table(section["content"])

        parsed = {"title": re.sub(r"^#+\s*", "", header).strip()}
        if words:
            parsed["words"] = words
        else:
            # Non-table content (e.g., character descriptions)
            text_content = "\n".join(
                l for l in section["content"]
                if not l.strip().startswith("#")
            ).strip()
            if text_content:
                parsed["text"] = text_content

        parsed_sections.append(parsed)

    cleaned_text = "\n".join(cleaned_lines)
    # Clean up excessive blank lines from removal
    cleaned_text = re.sub(r"\n{4,}", "\n\n\n", cleaned_text)

    return cleaned_text, parsed_sections


def detect_book_metadata(book_dir: Path) -> dict:
    """Detect book title and type from its files."""
    name = book_dir.name

    titles = {
        # Chinese (HSK)
        "chengyugushi": ("成语故事", "Chinese Idiom Stories", "stories"),
        "chuci": ("楚辞", "Songs of Chu", "poetry"),
        "guwenguanzhi": ("古文观止", "Selections of Classical Chinese Prose", "classical_prose"),
        "hongloumeng": ("红楼梦", "Dream of the Red Chamber", "novel"),
        "liaozhai": ("聊斋志异", "Strange Tales from a Chinese Studio", "stories"),
        "lunyu": ("论语", "The Analects", "philosophy"),
        "minjiangushi": ("民间故事", "Chinese Folk Tales", "stories"),
        "sanguoyanyi": ("三国演义", "Romance of the Three Kingdoms", "novel"),
        "shijing": ("诗经", "Book of Songs", "poetry"),
        "shishuoxinyu": ("世说新语", "A New Account of Tales of the World", "stories"),
        "shuihuzhuan": ("水浒传", "Water Margin", "novel"),
        "songci": ("宋词", "Song Dynasty Ci Poetry", "poetry"),
        "sunzibingfa": ("孙子兵法", "The Art of War", "philosophy"),
        "tangshi": ("唐诗", "Tang Dynasty Poetry", "poetry"),
        "xiyouji": ("西游记", "Journey to the West", "novel"),
        # Japanese (JLPT)
        "akutagawa": ("芥川龍之介短編集", "Akutagawa Short Stories", "stories"),
        "botchan": ("坊っちゃん", "Botchan", "novel"),
        "genji": ("源氏物語", "The Tale of Genji", "novel"),
        "heike": ("平家物語", "The Tale of the Heike", "novel"),
        "hyakunin": ("百人一首", "One Hundred Poets", "poetry"),
        "kaidan": ("怪談", "Ghost Stories", "stories"),
        "konjaku": ("今昔物語集", "Tales of Times Now Past", "stories"),
        "kotowaza": ("日本のことわざ", "Japanese Proverbs", "stories"),
        "merosu": ("走れメロス", "Run, Melos!", "novel"),
        "miyazawa": ("宮沢賢治作品集", "Miyazawa Kenji Stories", "stories"),
        "mukashibanashi": ("日本昔話", "Japanese Folk Tales", "stories"),
        "taiheiki": ("太平記", "Chronicle of Great Peace", "novel"),
        "taketori": ("竹取物語", "The Tale of the Bamboo Cutter", "novel"),
        "tsurezure": ("徒然草", "Essays in Idleness", "philosophy"),
        "wagahai": ("吾輩は猫である", "I Am a Cat", "novel"),
    }

    zh, en, book_type = titles.get(name, (name, name, "unknown"))

    levels = sorted(
        f.stem.split("_")[0]
        for f in list(book_dir.glob("hsk*_*.md")) + list(book_dir.glob("n*_*.md"))
    )

    return {
        "id": name,
        "title_zh": zh,
        "title_en": en,
        "type": book_type,
        "levels": levels,
    }


def process_book(book_dir: Path):
    """Process all files in a book directory."""
    print(f"\n{'='*60}")
    print(f"Processing: {book_dir.name}")
    print(f"{'='*60}")

    # 1. Metadata
    metadata = detect_book_metadata(book_dir)
    metadata_path = book_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), "utf-8")
    print(f"  Created: metadata.json")

    # 2. Glossary
    glossary_txt = book_dir / "glossary.txt"
    if glossary_txt.exists():
        categories = parse_glossary_txt(glossary_txt)
        glossary_json = book_dir / "glossary.json"
        glossary_json.write_text(
            json.dumps(categories, ensure_ascii=False, indent=2), "utf-8"
        )
        print(f"  Created: glossary.json ({sum(len(v) for v in categories.values())} entries)")

    # 3. Process each level's .md file
    md_files = sorted(list(book_dir.glob("hsk*_*.md")) + list(book_dir.glob("n*_*.md")))
    for md_path in md_files:
        level = md_path.stem.split("_")[0]

        cleaned_text, extracted = extract_and_strip(md_path)

        if extracted:
            # Save vocab JSON
            vocab_path = book_dir / f"{level}_vocab.json"
            vocab_data = {
                "book": book_dir.name,
                "level": level,
                "sections": extracted,
            }
            vocab_path.write_text(
                json.dumps(vocab_data, ensure_ascii=False, indent=2), "utf-8"
            )

            # Overwrite .md with cleaned text
            md_path.write_text(cleaned_text, "utf-8")

            word_count = sum(len(s.get("words", [])) for s in extracted)
            print(f"  {md_path.name}: stripped {len(extracted)} sections ({word_count} words) → {level}_vocab.json")
        else:
            print(f"  {md_path.name}: clean (no vocab sections found)")


def main():
    base = Path(__file__).resolve().parent.parent

    # Process Chinese (HSK) books
    output_dir = base / "output"
    for book_dir in sorted(output_dir.iterdir()):
        if not book_dir.is_dir() or book_dir.name.startswith("_"):
            continue
        process_book(book_dir)

    # Process Japanese (JLPT) books
    jlpt_output = base / "jlpt" / "output"
    if jlpt_output.exists():
        for book_dir in sorted(jlpt_output.iterdir()):
            if not book_dir.is_dir():
                continue
            process_book(book_dir)

    print(f"\n{'='*60}")
    print("Done! All books processed.")


if __name__ == "__main__":
    main()
