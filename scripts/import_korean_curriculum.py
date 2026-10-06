"""Extract NIKL's original vocabulary/grammar rows without guessing identities.

Run with a Python environment containing openpyxl. The workbook is read only;
the original spelling, homonym numbers, combined POS fields and guide text are
retained. Consumers must review crosswalks to older dictionary identities.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/korean/words/nikl_2017_curriculum_20201117.xlsx"
DESTINATION = SOURCE.with_suffix(".json")
SOURCE_URL = "https://m.korean.go.kr/front/reportData/reportDataView.do?mn_id=45&report_seq=932"


def extract(source: Path = SOURCE) -> dict:
    import openpyxl

    workbook = openpyxl.load_workbook(source, read_only=True, data_only=True)
    result = {"schema_version": 1, "source_url": SOURCE_URL,
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "edition": "2017 curriculum, corrected 2020-11-17",
              "attribution": "국립국어원 (National Institute of Korean Language)",
              "license": "KOGL Type 1: attribution",
              "level_system": "NIKL International Standard Curriculum levels 1–6; not an exhaustive TOPIK exam word list"}
    for sheet, kind in (("어휘", "vocabulary"), ("문법", "grammar")):
        rows = list(workbook[sheet].values)
        entries = []
        for row_number, row in enumerate(rows[1:], 2):
            number, within_level, raw_level = row[:3]
            if raw_level not in {f"{n}급" for n in range(1, 7)}:
                raise ValueError(f"Invalid curriculum level at {sheet}!{row_number}")
            # The vocabulary workbook repeats the header 등급. Its values are
            # identical in this edition; never silently discard a disagreement.
            fields = {}
            for header, value in zip(rows[0], row):
                if header in fields and fields[header] != value:
                    raise ValueError(f"Conflicting duplicate source column at {sheet}!{row_number}: {header}")
                fields[header] = value
            entries.append({"id": f"nikl2017-{kind}-{number:05d}",
                            "source_row": row_number, "level": int(raw_level[0]),
                            "source_fields": fields})
        expected = ({1: 735, 2: 1100, 3: 1655, 4: 2200, 5: 2365, 6: 2580}
                    if kind == "vocabulary" else {1: 45, 2: 45, 3: 67, 4: 67, 5: 56, 6: 56})
        if Counter(e["level"] for e in entries) != expected:
            raise ValueError(f"Unexpected {kind} grade counts")
        if len({e["id"] for e in entries}) != len(entries):
            raise ValueError(f"Duplicate {kind} identities")
        result[kind] = entries
    workbook.close()
    return result


def main():
    value = extract()
    DESTINATION.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({kind: len(value[kind]) for kind in ("vocabulary", "grammar")}))


if __name__ == "__main__":
    main()
