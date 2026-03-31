#!/usr/bin/env python3
"""Build dictionary.json for the HSK Graded Readers Flutter app.

Downloads CC-CEDICT and merges with HSK word lists to create a compact
dictionary JSON bundled with the app.

Usage:
    python scripts/build_dictionary.py
"""

import csv
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR.parent / "data" / "words"
OUTPUT_PATH = SCRIPT_DIR.parent / "app" / "assets" / "dictionary.json"
CEDICT_URL = (
    "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.zip"
)

# Tone mark tables for converting CC-CEDICT number format
_TONES: dict[str, str] = {
    "a": "āáǎàa",
    "e": "ēéěèe",
    "i": "īíǐìi",
    "o": "ōóǒòo",
    "u": "ūúǔùu",
    "ü": "ǖǘǚǜü",
    "v": "ǖǘǚǜü",  # CC-CEDICT uses 'v' for ü
}


def _apply_tone(syl: str) -> str:
    """Convert a single CC-CEDICT syllable like 'hao3' → 'hǎo'."""
    if not syl or not syl[-1].isdigit():
        return syl
    tone_idx = int(syl[-1]) - 1
    syl = syl[:-1]
    if tone_idx == 4:  # neutral tone – no mark
        return syl
    # Priority: a/e take the mark, then last vowel in compound
    for v in ("a", "e"):
        if v in syl:
            return syl.replace(v, _TONES[v][tone_idx], 1)
    if "ou" in syl:
        return syl.replace("o", _TONES["o"][tone_idx], 1)
    for v in reversed(syl):
        if v in _TONES:
            idx = syl.rindex(v)
            return syl[:idx] + _TONES[v][tone_idx] + syl[idx + 1 :]
    return syl


def cedict_pinyin_to_marks(pinyin: str) -> str:
    """Convert 'ni3 hao3' → 'nǐ hǎo'."""
    return " ".join(_apply_tone(s) for s in pinyin.split())


def load_hsk_words() -> dict[str, dict]:
    """Return {word: {pinyin, level}} for all HSK 1-9 entries."""
    words: dict[str, dict] = {}
    level_map = {**{i: f"hsk{i}_words.csv" for i in range(1, 7)}, 7: "hsk7to9_words.csv"}
    for level, filename in level_map.items():
        filepath = DATA_DIR / filename
        if not filepath.exists():
            print(f"  Skipping {filename} (not found)", file=sys.stderr)
            continue
        with open(filepath, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                word = row["word"].strip()
                pinyin = row.get("pinyin", "").strip()
                if word and word not in words:
                    words[word] = {"pinyin": pinyin, "level": level}
    return words


def download_cedict() -> str:
    """Download CC-CEDICT zip and return the UTF-8 text content."""
    print(f"  Fetching {CEDICT_URL} …")
    req = urllib.request.Request(
        CEDICT_URL,
        headers={"User-Agent": "Mozilla/5.0 (hsk-reader-builder/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        target = next((n for n in names if n.endswith(".u8")), names[0])
        return zf.read(target).decode("utf-8")


def parse_cedict(content: str) -> dict[str, list[dict]]:
    """Return {simplified: [{pinyin, definitions}, …]}."""
    entries: dict[str, list[dict]] = {}
    pattern = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+/(.+)/$")
    for line in content.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        m = pattern.match(line)
        if not m:
            continue
        _trad, simp, pinyin_raw, defs_raw = m.groups()
        defs = [
            d for d in defs_raw.split("/")
            if d and not d.startswith("old variant") and not d.startswith("CJK")
        ]
        pinyin = cedict_pinyin_to_marks(pinyin_raw)
        entries.setdefault(simp, []).append({"pinyin": pinyin, "definitions": defs})
    return entries


CONTENT_PATH = SCRIPT_DIR.parent / "app" / "assets" / "content.json"
_CJK = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')


def extract_content_ngrams(max_len: int = 6) -> set[str]:
    """Extract all unique Chinese n-grams (len 1..max_len) from content.json."""
    if not CONTENT_PATH.exists():
        return set()
    with open(CONTENT_PATH, encoding="utf-8") as f:
        raw = f.read()
    # Strip JSON structure noise; we just need the Chinese character runs
    runs: list[str] = []
    current: list[str] = []
    for ch in raw:
        if _CJK.match(ch):
            current.append(ch)
        else:
            if current:
                runs.append("".join(current))
                current = []
    if current:
        runs.append("".join(current))

    ngrams: set[str] = set()
    for run in runs:
        for start in range(len(run)):
            for length in range(1, min(max_len, len(run) - start) + 1):
                ngrams.add(run[start : start + length])
    return ngrams


def _merge_cedict_entry(
    cedict_entries: list[dict],
    existing_pinyin: str,
    max_defs: int,
) -> tuple[str, list[str]]:
    """Merge multiple CC-CEDICT readings into one pinyin + definition list."""
    pinyin = existing_pinyin
    seen: set[str] = set()
    defs: list[str] = []
    for ce in cedict_entries:
        if not pinyin and ce["pinyin"]:
            pinyin = ce["pinyin"]
        for d in ce["definitions"]:
            if d not in seen:
                seen.add(d)
                defs.append(d)
            if len(defs) >= max_defs:
                return pinyin, defs
    return pinyin, defs


def build_dictionary(
    hsk_words: dict[str, dict],
    cedict: dict[str, list[dict]],
    content_ngrams: set[str],
) -> dict[str, dict]:
    """Produce the compact output dict {word: {p, l?, d}}."""
    result: dict[str, dict] = {}

    # HSK words first (highest priority, up to 6 definitions)
    for word, info in hsk_words.items():
        pinyin, defs = _merge_cedict_entry(
            cedict.get(word, []), info["pinyin"], max_defs=6
        )
        if pinyin:
            result[word] = {"p": pinyin, "l": info["level"], "d": defs}

    # Non-HSK words that appear in reader content (up to 3 definitions)
    for ngram in content_ngrams:
        if ngram in result:
            continue  # already covered by HSK
        if ngram not in cedict:
            continue
        pinyin, defs = _merge_cedict_entry(cedict[ngram], "", max_defs=3)
        if pinyin:
            result[ngram] = {"p": pinyin, "d": defs}

    return result


def main() -> None:
    print("1. Loading HSK word lists…")
    hsk_words = load_hsk_words()
    print(f"   {len(hsk_words):,} words (HSK 1-9)")

    print("2. Extracting n-grams from reader content…")
    content_ngrams = extract_content_ngrams()
    print(f"   {len(content_ngrams):,} unique Chinese n-grams (1-6 chars)")

    cedict: dict[str, list[dict]] = {}
    print("3. Downloading CC-CEDICT…")
    try:
        raw = download_cedict()
        print(f"   {len(raw):,} bytes received")
        print("4. Parsing CC-CEDICT…")
        cedict = parse_cedict(raw)
        print(f"   {len(cedict):,} simplified entries")
    except Exception as exc:
        print(f"   WARNING: CC-CEDICT download failed: {exc}")
        print("   Building pinyin-only dictionary (no English definitions)")

    print("5. Merging…")
    dictionary = build_dictionary(hsk_words, cedict, content_ngrams)
    hsk_count = sum(1 for v in dictionary.values() if "l" in v)
    extra_count = len(dictionary) - hsk_count
    with_defs = sum(1 for v in dictionary.values() if v["d"])
    print(f"   {len(dictionary):,} entries total")
    print(f"   {hsk_count:,} HSK words + {extra_count:,} content-specific extras")
    print(f"   {with_defs:,} entries with definitions")

    print(f"6. Writing {OUTPUT_PATH}…")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dictionary, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"   Done — {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
