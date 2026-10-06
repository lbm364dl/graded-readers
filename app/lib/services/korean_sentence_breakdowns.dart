import 'dart:convert';

import 'package:flutter/services.dart';

class KoreanSentenceBreakdown {
  final String source;
  final int start;
  final String sentence;
  final String translationEn;
  final List<({String text, String explanationEn})> parts;

  KoreanSentenceBreakdown.fromJson(Map<String, dynamic> json)
      : source = json['source'] as String,
        start = json['start'] as int,
        sentence = json['sentence'] as String,
        translationEn = json['translation_en'] as String,
        parts = (json['parts'] as List)
            .map((part) => (
                  text: part['text'] as String,
                  explanationEn: part['explanation_en'] as String
                ))
            .toList();
}

class KoreanSentenceBreakdowns {
  final Map<String, dynamic> sources;
  final List<KoreanSentenceBreakdown> breakdowns;
  KoreanSentenceBreakdowns.fromJson(Map<String, dynamic> json)
      : sources = Map<String, dynamic>.from(json['sources'] as Map),
        breakdowns = (json['breakdowns'] as List)
            .map((item) => KoreanSentenceBreakdown.fromJson(item))
            .toList();

  static Future<KoreanSentenceBreakdowns>? _pending;
  static Future<KoreanSentenceBreakdowns> load() => _pending ??= _load();
  static Future<KoreanSentenceBreakdowns> _load() async {
    try {
      return KoreanSentenceBreakdowns.fromJson(jsonDecode(await rootBundle
          .loadString('assets/korean_sentence_breakdowns.json')));
    } catch (_) {
      _pending = null;
      rethrow;
    }
  }

  KoreanSentenceBreakdown? at(String source, String sourceText, int offset) {
    if (sources[source] != sourceText) return null;
    for (final item in breakdowns) {
      if (item.source == source &&
          item.start <= offset &&
          offset < item.start + item.sentence.length &&
          sourceText.substring(item.start, item.start + item.sentence.length) ==
              item.sentence &&
          item.parts.map((part) => part.text).join() == item.sentence) {
        return item;
      }
    }
    return null;
  }
}
