#!/usr/bin/env python3
"""Emit non-authoritative Chinese boundary proposals for constrained correction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import re
from typing import Any

import jieba
from opencc import OpenCC

from pipeline.fixed_boundary_annotation import refined_fixed_segments

_T2S = OpenCC("t2s")
_HAN = r"[\u3400-\u9fff]"
_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹刘严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董"
    "梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌"
    "霍虞万支柯管卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程"
    "邢裴陆荣翁荀羊甄曲封储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓"
)
_TITLE_SUFFIXES = (
    "大将军", "车骑将军", "太守", "县尉", "县令", "将军", "督邮",
    "太后", "美人", "常侍", "刺史", "校尉", "丞相", "国舅", "皇帝",
    "帝", "王", "侯", "公", "尹", "丞",
)


def _surfaces_with_offsets(text: str, surfaces: Iterable[str]) -> list[dict[str, Any]]:
    result, offset = [], 0
    for surface in surfaces:
        end = offset + len(surface)
        if text[offset:end] != surface:
            raise ValueError("proposal surfaces do not reconstruct text")
        result.append({"start": offset, "end": end, "text": surface})
        offset = end
    if offset != len(text):
        raise ValueError("proposal surfaces do not cover text")
    return result


def _proposal(text: str, surfaces: Iterable[str]) -> dict[str, Any]:
    segments = _surfaces_with_offsets(text, surfaces)
    return {
        "segments": segments,
        "boundaries": [item["end"] for item in segments[:-1]],
        "reconstructs": "".join(item["text"] for item in segments) == text,
    }


def _metadata_strings(value: Any, path: str = "metadata") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, _T2S.convert(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _metadata_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _metadata_strings(item, f"{path}[{index}]")


def generic_gazetteer_spans(text: str, metadata: Any) -> list[dict[str, Any]]:
    """Return overlapping entity/title candidates; never assert correctness."""
    candidates: dict[tuple[str, str], set[str]] = {}
    for source, value in _metadata_strings(metadata):
        # Candidate personal names: emit both two- and three-character forms.
        # Overlap is intentional because this is evidence for a corrector.
        for match in re.finditer(_HAN + r"{2,3}", value):
            run = match.group()
            for start in range(len(run)):
                if run[start] not in _SURNAMES:
                    continue
                for size in (2, 3):
                    candidate = run[start:start + size]
                    if len(candidate) == size:
                        candidates.setdefault((candidate, "name_candidate"), set()).add(source)
        for suffix in _TITLE_SUFFIXES:
            pattern = _HAN + r"{0,4}" + re.escape(suffix)
            for match in re.finditer(pattern, value):
                candidates.setdefault((match.group(), "title_candidate"), set()).add(source)

    spans: list[dict[str, Any]] = []
    for (candidate, kind), sources in candidates.items():
        for match in re.finditer(re.escape(candidate), text):
            spans.append({
                "start": match.start(), "end": match.end(), "text": candidate,
                "kind": kind, "metadata_sources": sorted(sources),
            })
    return sorted(spans, key=lambda item: (item["start"], -item["end"], item["kind"]))


def _disagreement_regions(
    text: str, plain_boundaries: list[int], refined_boundaries: list[int]
) -> list[dict[str, Any]]:
    plain, refined = set(plain_boundaries), set(refined_boundaries)
    common = sorted({0, len(text)} | (plain & refined))
    result = []
    for start, end in zip(common, common[1:]):
        plain_inside = sorted(item for item in plain if start < item < end)
        refined_inside = sorted(item for item in refined if start < item < end)
        if plain_inside != refined_inside:
            result.append({
                "start": start, "end": end, "text": text[start:end],
                "plain_boundaries": plain_inside,
                "refined_boundaries": refined_inside,
            })
    return result


def boundary_proposals(text: str, metadata: Any = None) -> dict[str, Any]:
    """Build evidence for correction without selecting any final boundaries."""
    # A private Tokenizer is unaffected by ChineseSegmenter's global HSK edits.
    plain = _proposal(text, jieba.Tokenizer().lcut(text))
    refined = _proposal(text, refined_fixed_segments(text))
    return {
        "contract": "non_authoritative_boundary_proposals_v1",
        "text": text,
        "text_length": len(text),
        "proposals": {"plain_jieba": plain, "refined": refined},
        "gazetteer_spans": generic_gazetteer_spans(text, metadata),
        "disagreement_regions": _disagreement_regions(
            text, plain["boundaries"], refined["boundaries"]
        ),
        "selected_boundaries": None,
    }


def constrained_correction_prompt(proposals: dict[str, Any]) -> str:
    """Format the shared contract for a Chinese/JLPT-style correction layer."""
    return """Choose learner-facing Chinese word boundaries using the evidence below.
Neither tokenizer is authoritative. Gazetteer spans are candidates, not facts. You may
choose boundaries only after checking the exact text and local context. Preserve every
character exactly and return explicit offsets; never rewrite the text. Concentrate review
on disagreement_regions, names, titles, idioms, particles, and compositional phrases.

BOUNDARY PROPOSALS:
""" + json.dumps(proposals, ensure_ascii=False, indent=2)
