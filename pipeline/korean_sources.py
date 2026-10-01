"""Import and verify a pinned, searchable original Korean source edition."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "books/korean/honggildong"
EDITION = "wikisource-honggildong-gyeongpan30-460078"
URL = "https://ko.wikisource.org/w/index.php?title=홍길동전_(30장_경판본)&oldid=460078"
RAW_URL = "https://ko.wikisource.org/w/index.php?title=%ED%99%8D%EA%B8%B8%EB%8F%99%EC%A0%84_(30%EC%9E%A5_%EA%B2%BD%ED%8C%90%EB%B3%B8)&oldid=460078&action=raw"
RAW_SHA = "d4c8bba88653fcea7f4bb57f6d8b5dea753e1188741419a4cdf1c0aa01e8d808"
TEXT_SHA = "310f0d76f9a9c614d4cf920e9b3303e30473649c7d22e79169165e7d4b88d197"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract(raw: bytes) -> str:
    if sha(raw) != RAW_SHA:
        raise ValueError("Korean source revision content changed")
    wiki = raw.decode("utf-8")
    text = wiki.split("{{옛한글/시작}}", 1)[1].split("{{옛한글/끝}}", 1)[0].strip() + "\n"
    if sha(text.encode()) != TEXT_SHA:
        raise ValueError("Korean source extraction changed")
    return text


def import_source(raw: bytes | None = None) -> dict:
    if raw is None:
        request = Request(RAW_URL, headers={"User-Agent": "GradedReadersSourceImporter/1.0"})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    text = extract(raw)
    paragraphs = text.strip().split("\n\n")
    offsets, cursor = [], 0
    for paragraph in paragraphs:
        offsets.append((cursor, cursor + len(paragraph)))
        cursor += len(paragraph) + 2
    manifest = {
        "schema_version": 1, "edition": EDITION, "book": "honggildong",
        "title": "홍길동전 (30장 경판본)", "revision": 460078,
        "url": URL, "raw_url": RAW_URL, "raw_file": "original.wiki",
        "text_file": "original.txt", "raw_sha256": RAW_SHA, "text_sha256": TEXT_SHA,
        "provenance_url": "https://ko.wikisource.org/wiki/토론:홍길동전_(30장_경판본)",
        "provenance": "Wikisource transcription from the Jikji Project; original 30-sheet Gyeongpan edition.",
        "rights": "Original text marked PD-old-100 by Wikisource. Site contributions are under CC BY-SA 4.0; retain Wikisource/Jikji attribution and revision link.",
        "units": [
            {"number": 1, "start": offsets[0][0], "end": offsets[4][1], "label": "Family and childhood"},
            {"number": 2, "start": offsets[5][0], "end": offsets[16][1], "label": "Night conversation with his father"},
        ],
    }
    note_path = DIRECTORY / "source-notes.json"
    if note_path.exists():
        manifest.update(notes_file=note_path.name, notes_sha256=sha(note_path.read_bytes()))
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    (DIRECTORY / "original.wiki").write_bytes(raw)
    (DIRECTORY / "original.txt").write_text(text, encoding="utf-8")
    (DIRECTORY / "source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_unit(number: int, directory: Path = DIRECTORY) -> tuple[dict, dict, str]:
    manifest = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    raw = (directory / manifest["raw_file"]).read_bytes()
    text_bytes = (directory / manifest["text_file"]).read_bytes()
    if (manifest["edition"] != EDITION or manifest["raw_sha256"] != RAW_SHA
            or manifest["text_sha256"] != TEXT_SHA or sha(raw) != RAW_SHA
            or sha(text_bytes) != TEXT_SHA or extract(raw).encode() != text_bytes):
        raise ValueError("Korean source snapshot or edition is stale")
    text = text_bytes.decode("utf-8")
    if "notes_file" in manifest:
        note_bytes = (directory / manifest["notes_file"]).read_bytes()
        notes = json.loads(note_bytes)
        if sha(note_bytes) != manifest["notes_sha256"] or notes.get("reviewed") is not True or notes.get("edition") != EDITION:
            raise ValueError("Korean source notes are stale or unreviewed")
        for note in notes["notes"]:
            if text[note["start"]:note["end"]] != note["quote"]:
                raise ValueError("Korean source note lost exact source alignment")
    units = manifest["units"]
    if len({unit["number"] for unit in units}) != len(units):
        raise ValueError("duplicate Korean source unit")
    for unit in units:
        if not 0 <= unit["start"] < unit["end"] <= len(text):
            raise ValueError("invalid Korean source unit span")
    unit = next((unit for unit in units if unit["number"] == number), None)
    if unit is None:
        raise ValueError(f"Korean source unit {number} has not been mapped")
    return manifest, unit, text[unit["start"]:unit["end"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import", dest="download", action="store_true")
    args = parser.parse_args()
    if args.download:
        import_source()
    manifest, _, _ = load_unit(1)
    print(json.dumps({"edition": manifest["edition"], "mapped_units": len(manifest["units"]), "verified": True}))


if __name__ == "__main__":
    main()
