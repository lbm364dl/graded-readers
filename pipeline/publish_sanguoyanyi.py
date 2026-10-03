#!/usr/bin/env python3
"""Audit and publish completed, annotated 三国演义 agent-harness runs.

Publication is deliberately deterministic and contains no model calls.  It will
not replace reader files unless every requested level passes every audit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any

from pipeline.agent_harness import ChapterHarness
from pipeline.epub3 import build_epub3
from pipeline.extract_epub_chapters import ExtractionError, verify_manifest as verify_source_manifest

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - exercised by CLI on incomplete installs
    OpenCC = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = ROOT / "books" / "chinese" / "sanguoyanyi"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "chinese" / "sanguoyanyi"
LEVELS = tuple(f"hsk{number}" for number in range(1, 7))
RUN_NAME = re.compile(r"^chapter_(\d{3})-(hsk[1-6])$")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# A conservative diagnostic, not a language detector. These high-frequency
# one-to-one variants are useful evidence that generated prose is simplified.
_VARIANT_PAIRS = (
    "國国 漢汉 軍军 將将 戰战 門门 見见 說说 話话 時时 來来 為为 與与 後后 "
    "東东 長长 張张 劉刘 關关 趙赵 馬马 孫孙 書书 車车 頭头 開开 無无 "
    "萬万 兩两 義义 氣气 體体 力力 麼么 還还 過过 這这 個个 裡里 聽听 "
    "殺杀 敗败 勝胜 敵敌 應应 會会 發发 動动 進进 遠远 邊边 給给"
)
_DISTINCT_VARIANT_PAIRS = tuple(
    pair for pair in _VARIANT_PAIRS.split() if pair[0] != pair[1]
)
TRADITIONAL = frozenset(pair[0] for pair in _DISTINCT_VARIANT_PAIRS)
SIMPLIFIED = frozenset(pair[1] for pair in _DISTINCT_VARIANT_PAIRS)
_OPENCC: Any = None


class PublicationError(ValueError):
    """An input failed a publication gate."""


def _simplify(text: str) -> str:
    """Convert Traditional Chinese to Simplified Chinese deterministically."""
    global _OPENCC
    if OpenCC is None:
        raise PublicationError(
            "OpenCC is required for publication; install project dependencies"
        )
    if _OPENCC is None:
        _OPENCC = OpenCC("t2s")
    simplified = _OPENCC.convert(text)
    # Idempotence is a useful guard against a misconfigured converter.
    if _OPENCC.convert(simplified) != simplified:
        raise PublicationError("OpenCC t2s conversion was not idempotent")
    return simplified


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"cannot read valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _harness_source_digest(path: Path) -> str:
    """Return ChapterHarness.digest(exact decoded source text).

    Current agent manifests fingerprint prompt input as UTF-8 text followed by
    a NUL separator. Older/imported manifests may contain the conventional raw
    file digest. Both are valid only when recomputed from the source file that
    the source manifest has already authenticated.
    """
    hasher = hashlib.sha256()
    hasher.update(path.read_text(encoding="utf-8").encode("utf-8"))
    hasher.update(b"\0")
    return hasher.hexdigest()


def _cjk_count(text: str) -> int:
    return len(CJK.findall(text))


def _discover_runs(run_dirs: list[Path]) -> dict[tuple[int, str], Path]:
    found: dict[tuple[int, str], Path] = {}
    for parent in run_dirs:
        if not parent.is_dir():
            raise PublicationError(f"run directory does not exist: {parent}")
        for candidate in sorted(parent.iterdir()):
            match = RUN_NAME.fullmatch(candidate.name)
            if not match or not candidate.is_dir():
                continue
            key = (int(match.group(1)), match.group(2))
            if key in found:
                raise PublicationError(
                    f"duplicate run for chapter {key[0]:03d} {key[1]}: "
                    f"{found[key]} and {candidate}"
                )
            found[key] = candidate
    return found


def _source_index(source_dir: Path, expected_chapters: int) -> dict[int, dict[str, Any]]:
    manifest = _load_json(source_dir / "manifest.json")
    chapters = manifest.get("chapters")
    if manifest.get("chapter_count") != expected_chapters or not isinstance(chapters, list):
        raise PublicationError(
            f"source manifest must declare exactly {expected_chapters} chapters"
        )
    result: dict[int, dict[str, Any]] = {}
    for expected, item in enumerate(chapters, 1):
        if not isinstance(item, dict) or item.get("number") != expected:
            raise PublicationError(f"source chapter order breaks at {expected:03d}")
        expected_file = f"chapter_{expected:03d}.txt"
        if item.get("file") != expected_file:
            raise PublicationError(
                f"source chapter {expected:03d} must use canonical file {expected_file}"
            )
        path = (source_dir / expected_file).resolve()
        if path.parent != source_dir.resolve():
            raise PublicationError(f"source chapter escapes source directory: {path}")
        if not path.is_file():
            raise PublicationError(f"source chapter missing: {path}")
        actual_hash = _sha256(path)
        if item.get("sha256") != actual_hash:
            raise PublicationError(f"source hash mismatch: {path}")
        result[expected] = {**item, "path": path, "sha256": actual_hash}
    return result


def _audit_chapter(
    run_dir: Path,
    source: dict[str, Any],
    level: str,
    number: int,
    require_annotations: bool,
) -> dict[str, Any]:
    manifest = _load_json(run_dir / "manifest.json")
    report = _load_json(run_dir / "report.json")
    if manifest.get("status") != "complete" or report.get("status") != "complete":
        raise PublicationError(f"run is not complete: {run_dir}")
    if manifest.get("level") != level:
        raise PublicationError(f"level mismatch: {run_dir}")
    manifest_source = Path(str(manifest.get("source", ""))).resolve()
    if manifest_source != source["path"]:
        raise PublicationError(f"source path mismatch: {run_dir}")
    valid_provenance = {
        source["sha256"], _harness_source_digest(source["path"]),
    }
    if manifest.get("source_sha256") not in valid_provenance:
        raise PublicationError(f"source provenance hash mismatch: {run_dir}")

    chapter_path = run_dir / "chapter.txt"
    try:
        text = chapter_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PublicationError(f"accepted chapter missing: {chapter_path}") from exc
    if not text.strip() or _cjk_count(text) == 0:
        raise PublicationError(f"accepted chapter is empty: {chapter_path}")
    if report.get("chapter_cjk") != _cjk_count(text):
        raise PublicationError(f"chapter CJK count does not match report: {run_dir}")
    verdicts = report.get("scene_verdicts")
    if not isinstance(verdicts, dict) or not verdicts or set(verdicts.values()) != {"pass"}:
        raise PublicationError(f"not every source-grounded scene review passed: {run_dir}")

    outline = _load_json(run_dir / "outline.json")
    title = str(outline.get("chapter_title", "")).strip() or f"第{number}章"
    reader_path = run_dir / "reader.json"
    segments: list[dict[str, Any]] = []
    grammar_overlays: list[dict[str, Any]] = []
    annotation_audit: dict[str, Any] | None = None
    if reader_path.exists():
        reader = _load_json(reader_path)
        if reader.get("text") != text:
            raise PublicationError(f"reader text differs from accepted chapter: {run_dir}")
        raw_segments = reader.get("segments")
        if not isinstance(raw_segments, list) or any(not isinstance(x, dict) for x in raw_segments):
            raise PublicationError(f"invalid annotation segments: {reader_path}")
        grammar_overlays = reader.get("grammar_overlays")
        expected_segment = {"text", "type", "pinyin", "meaning_en"}
        expected_overlay = {
            "start", "end", "text", "grammar_candidate_key", "pattern", "meaning_en"
        }
        segment_types = {"word", "particle", "name", "idiom", "punctuation"}
        if any(set(item) != expected_segment or item.get("type") not in segment_types
               for item in raw_segments):
            raise PublicationError(f"annotation segment violates canonical schema: {reader_path}")
        if not isinstance(grammar_overlays, list) or any(
            not isinstance(item, dict) or set(item) != expected_overlay
            for item in grammar_overlays
        ):
            raise PublicationError(f"grammar overlays violate canonical schema: {reader_path}")
        if not ChapterHarness.annotation_reconstructs(text, reader):
            raise PublicationError(f"annotations violate segmentation contract: {reader_path}")
        annotation_audit = reader.get("annotation_audit")
        if not isinstance(annotation_audit, dict) or annotation_audit.get("all_reviewed") is not True:
            raise PublicationError(f"annotations were not all reviewed: {reader_path}")
        acceptance_state = annotation_audit.get("acceptance_state", "reviewed_pass")
        if acceptance_state == "reviewed_and_remediated":
            if (annotation_audit.get("final_review_verdict") != "revise"
                    or not isinstance(annotation_audit.get("remediation_calls"), int)
                    or annotation_audit["remediation_calls"] < 1):
                raise PublicationError(
                    f"annotation remediation evidence is inconsistent: {reader_path}"
                )
        elif acceptance_state != "reviewed_pass":
            raise PublicationError(f"unknown annotation acceptance state: {reader_path}")
        # The reader is a tap-to-explain interface, so a syntactically valid
        # reconstruction is insufficient: ordinary lexical entries may not be
        # clause-sized, and punctuation must not hide inside semantic spans.
        for segment in raw_segments:
            surface = str(segment["text"])
            kind = segment["type"]
            if kind == "punctuation":
                if any(not (char.isspace() or unicodedata.category(char).startswith("P"))
                       for char in surface):
                    raise PublicationError(
                        f"punctuation segment contains lexical text: {reader_path}"
                    )
                if segment["pinyin"] or segment["meaning_en"]:
                    raise PublicationError(
                        f"punctuation segment has a lexical gloss: {reader_path}"
                    )
            else:
                if any(char.isspace() or unicodedata.category(char).startswith("P")
                       for char in surface):
                    raise PublicationError(
                        f"lexical segment contains punctuation/space: {reader_path}"
                    )
                if _cjk_count(surface) > 4:
                    raise PublicationError(
                        f"clause-sized lexical segment ({surface!r}): {reader_path}"
                    )
                if not str(segment["pinyin"]).strip() or not str(segment["meaning_en"]).strip():
                    raise PublicationError(f"lexical gloss is empty: {reader_path}")
        annotation_report_path = run_dir / "annotation-report.json"
        annotation_report = _load_json(annotation_report_path)
        if annotation_report.get("status") != "complete":
            raise PublicationError(f"annotation report is not complete: {run_dir}")
        if annotation_report.get("chapter_sha256") != _sha256(chapter_path):
            raise PublicationError(f"annotation report is stale for chapter: {run_dir}")
        if annotation_report.get("reader_sha256") != _sha256(reader_path):
            raise PublicationError(f"annotation report is stale for reader: {run_dir}")
        chunks = annotation_audit.get("chunks")
        if not isinstance(chunks, int) or chunks < 1 or annotation_report.get("chunks") != chunks:
            raise PublicationError(f"annotation review chunk evidence is inconsistent: {run_dir}")
        segments = raw_segments
        title = str(reader.get("title", title)).strip() or title
    elif require_annotations:
        raise PublicationError(f"reviewed annotations missing: {reader_path}")

    # Normalize text and annotation surfaces together, then prove both exact
    # reconstruction and converter idempotence on the artifact to be published.
    text = _simplify(text)
    title = _simplify(title)
    segments = [{**segment, "text": _simplify(str(segment.get("text", "")))}
                for segment in segments]
    grammar_overlays = [
        {**overlay, "text": _simplify(str(overlay.get("text", "")))}
        for overlay in grammar_overlays
    ]
    if segments and "".join(segment["text"] for segment in segments) != text:
        raise PublicationError(f"normalized annotations do not reconstruct: {run_dir}")
    for overlay in grammar_overlays:
        if overlay["text"] != text[overlay["start"]:overlay["end"]]:
            raise PublicationError(f"normalized grammar overlay does not reconstruct: {run_dir}")
    traditional = sum(char in TRADITIONAL for char in text)
    simplified = sum(char in SIMPLIFIED for char in text)
    # OpenCC is authoritative; this sample is retained as auditable evidence.
    # Any remaining sampled traditional glyph means normalization is broken.
    if traditional:
        raise PublicationError(
            f"traditional variants remain after OpenCC in {run_dir}: "
            f"simplified_markers={simplified}, traditional_markers={traditional}"
        )
    return {
        "number": number,
        "title": title,
        "text": text,
        "segments": segments,
        "grammar_overlays": grammar_overlays,
        "annotation_audit": annotation_audit,
        "cjk": _cjk_count(text),
        "source": {
            "file": source["file"],
            "sha256": source["sha256"],
            "run": str(run_dir.resolve()),
        },
        "simplified_evidence": {
            "simplified_markers": simplified,
            "traditional_markers": traditional,
        },
    }


def audit_runs(
    run_dirs: list[Path],
    source_dir: Path,
    levels: list[str],
    expected_chapters: int = 120,
    require_annotations: bool = True,
) -> dict[str, Any]:
    """Return publishable content or raise without writing any output."""
    if levels != sorted(set(levels), key=LEVELS.index):
        raise PublicationError("levels must be unique and in HSK1-to-HSK6 order")
    if expected_chapters == 120:
        try:
            verify_source_manifest(source_dir / "manifest.json")
        except (ExtractionError, OSError, KeyError, TypeError) as exc:
            raise PublicationError(f"source corpus completeness verification failed: {exc}") from exc
    sources = _source_index(source_dir.resolve(), expected_chapters)
    runs = _discover_runs(run_dirs)
    expected_keys = {
        (chapter, level)
        for chapter in range(1, expected_chapters + 1)
        for level in levels
    }
    missing = sorted(expected_keys - set(runs))
    if missing:
        preview = ", ".join(f"{n:03d}-{level}" for n, level in missing[:12])
        raise PublicationError(f"missing {len(missing)} chapter-level runs: {preview}")

    books: dict[str, list[dict[str, Any]]] = {}
    for level in levels:
        books[level] = [
            _audit_chapter(
                runs[(number, level)], sources[number], level, number, require_annotations
            )
            for number in range(1, expected_chapters + 1)
        ]

    length_failures: list[dict[str, Any]] = []
    for lower, upper in zip(levels, levels[1:]):
        lower_total = sum(item["cjk"] for item in books[lower])
        upper_total = sum(item["cjk"] for item in books[upper])
        if lower_total >= upper_total:
            length_failures.append({"scope": "book", "lower": lower, "upper": upper})
        for number, (low_chapter, high_chapter) in enumerate(
            zip(books[lower], books[upper]), 1
        ):
            if low_chapter["cjk"] >= high_chapter["cjk"]:
                length_failures.append({
                    "scope": "chapter", "chapter": number,
                    "lower": lower, "lower_cjk": low_chapter["cjk"],
                    "upper": upper, "upper_cjk": high_chapter["cjk"],
                })
    if length_failures:
        raise PublicationError(
            f"strictly increasing length audit failed: {length_failures[:12]}"
        )
    return {
        "book": "sanguoyanyi",
        "title_zh": "三国演义",
        "title_en": "Romance of the Three Kingdoms",
        "source_manifest": str((source_dir / "manifest.json").resolve()),
        "expected_chapters": expected_chapters,
        "levels": levels,
        "books": books,
    }


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _build_epub(path: Path, level: str, chapters: list[dict[str, Any]]) -> None:
    """Build a validated, self-contained EPUB containing reviewed annotations."""
    title = f"三国演义（{level.upper()}分级读物）"
    build_epub3(
        path, identifier=f"sanguoyanyi-{level}", title=title, language="zh-CN",
        chapters=chapters,
        chapter_label=lambda item: f"第{item['number']}章：{item['title']}",
    )


def publish(audit: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Write standard Markdown, consolidated annotations and an audit report."""
    published: dict[str, Any] = {}
    for level in audit["levels"]:
        chapters = audit["books"][level]
        preamble = (
            f"# 三国演义（{level.upper()}分级读物）\n\n"
            f"**HSK Level {level[-1]}**\n\n"
            "根据《三国演义》完整一百二十回改写。生词、虚词、姓名和真正的成语见配套注释。\n\n"
        )
        body = "\n\n".join(
            f"## 第{item['number']}章：{item['title']}\n\n{item['text'].strip()}"
            for item in chapters
        ) + "\n"
        markdown_path = output_dir / f"{level}_sanguoyanyi.md"
        annotations_path = output_dir / f"{level}_sanguoyanyi_annotations.json"
        epub_path = output_dir / f"{level}_sanguoyanyi.epub"
        annotation_document = {
            "schema_version": 1,
            "book": audit["book"],
            "title": audit["title_zh"],
            "level": level.upper(),
            "chapter_count": len(chapters),
            "chapters": [{
                "number": item["number"], "title": item["title"],
                "text": item["text"], "segments": item["segments"],
                "grammar_overlays": item["grammar_overlays"],
                "annotation_audit": item["annotation_audit"],
                "source": item["source"],
            } for item in chapters],
        }
        _atomic_text(markdown_path, preamble + body)
        _atomic_text(
            annotations_path,
            json.dumps(annotation_document, ensure_ascii=False, indent=2) + "\n",
        )
        _build_epub(epub_path, level, chapters)
        published[level] = {
            "markdown": str(markdown_path.resolve()),
            "annotations": str(annotations_path.resolve()),
            "epub": str(epub_path.resolve()),
            "chapters": len(chapters),
            "cjk": sum(item["cjk"] for item in chapters),
            "markdown_sha256": _sha256(markdown_path),
            "annotations_sha256": _sha256(annotations_path),
            "epub_sha256": _sha256(epub_path),
            "simplified_markers": sum(
                item["simplified_evidence"]["simplified_markers"] for item in chapters
            ),
            "traditional_markers": sum(
                item["simplified_evidence"]["traditional_markers"] for item in chapters
            ),
        }
    report = {
        "status": "complete",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book": audit["book"],
        "source_manifest": audit["source_manifest"],
        "invariants": {
            "exact_ordered_nonempty_chapters": True,
            "source_provenance_verified": True,
            "all_generation_reviews_passed": True,
            "annotations_reconstruct_text": True,
            "annotations_reviewed": True,
            "simplified_output_evidenced": True,
            "strictly_increasing_per_chapter_and_total_cjk": True,
            "complete_epub_per_level": True,
        },
        "levels": published,
    }
    _atomic_text(
        output_dir / "publication-audit.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-dir", action="append", required=True)
    result.add_argument("--source-dir", default=str(DEFAULT_SOURCE_DIR))
    result.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    result.add_argument("--levels", nargs="+", default=list(LEVELS), choices=LEVELS)
    result.add_argument("--expected-chapters", type=int, default=120)
    result.add_argument("--allow-missing-annotations", action="store_true")
    result.add_argument("--audit-only", action="store_true")
    result.add_argument("--build-app-content", action="store_true")
    result.add_argument("--build-dictionary", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.allow_missing_annotations and not args.audit_only:
            raise PublicationError(
                "--allow-missing-annotations is only valid with --audit-only; "
                "published readers always require reviewed annotations"
            )
        audit = audit_runs(
            [Path(path) for path in args.run_dir], Path(args.source_dir), args.levels,
            args.expected_chapters, not args.allow_missing_annotations,
        )
        if args.audit_only:
            print(json.dumps({
                "status": "complete",
                "levels": {level: {
                    "chapters": len(audit["books"][level]),
                    "cjk": sum(x["cjk"] for x in audit["books"][level]),
                } for level in args.levels},
            }, ensure_ascii=False, indent=2))
            return 0
        report = publish(audit, Path(args.output_dir))
        if args.build_app_content:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_hsk_content_json.py")],
                cwd=ROOT, check=True,
            )
        if args.build_dictionary:
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "build_dictionary.py")],
                cwd=ROOT, check=True,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (PublicationError, subprocess.CalledProcessError) as exc:
        print(f"publication failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
