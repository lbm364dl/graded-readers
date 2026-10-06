import 'dart:convert';
import 'package:flutter/services.dart';
import '../models.dart';

/// Tap boundaries are reviewed independently from lexical dictionary boundaries.
class ChineseReadingUnits {
  final Map<String, dynamic> sources;
  ChineseReadingUnits.fromJson(Map<String, dynamic> json)
      : sources = Map<String, dynamic>.from(json['sources'] as Map);

  static ChineseReadingUnits? _cached;
  static Future<ChineseReadingUnits> load() async =>
      _cached ??= ChineseReadingUnits.fromJson(jsonDecode(
              await rootBundle.loadString('assets/chinese_reading_units.json'))
          as Map<String, dynamic>);

  List<ReadingDisplayUnit> forAnnotation(
      String asset, AgentChapterAnnotation annotation) {
    final source = sources[asset];
    if (source == null) return const [];
    final expected = source['segments'] as List;
    if (source['text'] != annotation.text ||
        expected.length != annotation.segments.length) {
      throw StateError('Stale Chinese reading units: $asset');
    }
    final starts = <int>[];
    var offset = 0;
    for (var i = 0; i < annotation.segments.length; i++) {
      final segment = annotation.segments[i];
      if (expected[i][0] != segment.text ||
          expected[i][1] != segment.pinyin ||
          expected[i][2] != segment.meaningEn) {
        throw StateError('Stale Chinese reading-unit annotation: $asset');
      }
      starts.add(offset);
      offset += segment.text.length;
    }
    final used = <int>{};
    return (source['units'] as List).map((raw) {
      final first = raw['first_segment'] as int;
      final last = raw['last_segment'] as int;
      if (first < 0 || last <= first || last >= annotation.segments.length) {
        throw StateError('Invalid Chinese reading-unit range');
      }
      final parts = annotation.segments.sublist(first, last + 1);
      if (parts.map((s) => s.text).join() != raw['text'] ||
          parts.any((s) => s.type == 'punctuation')) {
        throw StateError('Invalid Chinese reading-unit text');
      }
      for (var i = first; i <= last; i++) {
        if (!used.add(i)) throw StateError('Overlapping Chinese reading units');
      }
      final start = starts[first];
      final end = starts[last] + annotation.segments[last].text.length;
      final segment = AgentSegment(
          text: raw['text'] as String,
          type: 'word',
          pinyin: raw['pinyin'] as String,
          meaningEn: raw['meaning_en'] as String,
          targetHskLevel: parts.first.targetHskLevel);
      return ReadingDisplayUnit(
          firstSegment: first,
          lastSegment: last,
          start: start,
          end: end,
          segment: segment,
          grammar: AgentGrammarOverlay(
              start: start,
              end: end,
              text: segment.text,
              grammarCandidateKey: 'reading-unit-$first',
              pattern: segment.text,
              meaningEn: raw['explanation_en'] as String));
    }).toList();
  }
}
