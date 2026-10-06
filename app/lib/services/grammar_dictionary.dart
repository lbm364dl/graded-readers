import 'dart:convert';
import 'package:flutter/services.dart';

class GrammarDictionary {
  final Map<String, dynamic> sources;
  final List<Map<String, dynamic>> entries;
  final List<Map<String, dynamic>> occurrences;
  final Map<String, dynamic> wordRoutes;
  late final Map<String, Map<String, dynamic>> _entriesById = {
    for (final entry in entries) entry['id'] as String: entry,
  };
  late final Map<String, List<Map<String, dynamic>>> _usesBySource = () {
    final indexed = <String, List<Map<String, dynamic>>>{};
    for (final use in occurrences) {
      indexed.putIfAbsent(use['source'] as String, () => []).add(use);
    }
    return indexed;
  }();
  late final Map<(String, int, int), List<Map<String, dynamic>>>
      _formsBySegmentStep = () {
    final indexed = <(String, int, int), List<Map<String, dynamic>>>{};
    for (final use in occurrences.where((use) => use['layer'] == 'form')) {
      indexed
          .putIfAbsent(
              (use['source'] as String, use['segment_index'] as int,
                  use['step_index'] as int),
              () => [])
          .add(use);
    }
    return indexed;
  }();
  GrammarDictionary.fromJson(Map<String, dynamic> json)
      : sources = Map<String, dynamic>.from(json['sources'] as Map),
        entries = (json['entries'] as List).cast<Map<String, dynamic>>(),
        occurrences =
            (json['occurrences'] as List).cast<Map<String, dynamic>>(),
        wordRoutes =
            Map<String, dynamic>.from(json['word_routes'] as Map? ?? {});
  static Future<GrammarDictionary>? _pending;
  static GrammarDictionary? _cached;
  static Future<GrammarDictionary> load() =>
      _cached != null ? Future.value(_cached!) : _pending ??= _load();
  static Future<GrammarDictionary> _load() async {
    try {
      return _cached = GrammarDictionary.fromJson(jsonDecode(
              await rootBundle.loadString('assets/grammar_dictionary_ja.json'))
          as Map<String, dynamic>);
    } catch (_) {
      _pending = null;
      rethrow;
    }
  }

  Map<String, dynamic> entry(String id) =>
      _entriesById[id] ?? (throw StateError('Unknown grammar entry: $id'));

  /// Different annotation layers can describe the same sentence occurrence.
  /// Group examples by source position, never by sentence text alone.
  List<Map<String, dynamic>> examples(String entryId) {
    final linked = occurrences
        .where((o) => (o['entry_ids'] as List).contains(entryId))
        .toList();
    final actual = linked.where((o) => o['layer'] != 'form').toList();
    final groups = <String, List<Map<String, dynamic>>>{};
    for (final use in actual.isEmpty ? linked : actual) {
      final key = '${use['source']}:${use['sentence_start']}:${use['sentence']}'
          '${actual.isEmpty ? ':${use['form']}:${use['form_label']}' : ''}';
      groups.putIfAbsent(key, () => []).add(use);
    }
    return groups.values.map((group) {
      // The wider phrase provides the best source navigation target.
      final ordered = [...group]..sort((a, b) =>
          ((b['end'] as int) - (b['start'] as int))
              .compareTo((a['end'] as int) - (a['start'] as int)));
      return <String, dynamic>{
        ...ordered.first,
        'context_en':
            ordered.map((o) => o['context_en'] as String).toSet().join('\n')
      };
    }).toList();
  }

  Map<String, dynamic>? particleLesson(
      String source, String text, int start, String surface, int segmentIndex) {
    final matches = uses(source, text, start, surface)
        .where((use) =>
            use['layer'] == 'segment' &&
            use['surface'] == surface &&
            use['segment_index'] == segmentIndex &&
            (use['entry_ids'] as List).length == 1)
        .toList();
    if (matches.length != 1) return null;
    final lesson =
        entry((matches.single['entry_ids'] as List).single as String);
    return lesson['kind'] == 'particle' ? lesson : null;
  }

  List<Map<String, dynamic>> uses(
      String source, String text, int start, String surface,
      {bool includeForms = false}) {
    if (sources[source]?['text'] != text ||
        start < 0 ||
        start + surface.length > text.length ||
        text.substring(start, start + surface.length) != surface) {
      return [];
    }
    final runeStart = text.substring(0, start).runes.length;
    final runeEnd = runeStart + surface.runes.length;
    return (_usesBySource[source] ?? const <Map<String, dynamic>>[])
        .where((o) =>
            (includeForms || o['layer'] != 'form') &&
            (o['start'] as int) >= runeStart &&
            (o['end'] as int) <= runeEnd)
        .toList();
  }

  Map<String, dynamic>? formStep(
      String source,
      String text,
      int start,
      String surface,
      int segmentIndex,
      int stepIndex,
      String form,
      String reading,
      String label,
      String meaning) {
    if (sources[source]?['text'] != text ||
        start < 0 ||
        start + surface.length > text.length ||
        text.substring(start, start + surface.length) != surface) {
      return null;
    }
    final runeStart = text.substring(0, start).runes.length;
    final runeEnd = runeStart + surface.runes.length;
    final matches = (_formsBySegmentStep[(source, segmentIndex, stepIndex)] ??
            const <Map<String, dynamic>>[])
        .where((o) =>
            (o['start'] as int) >= runeStart &&
            (o['end'] as int) <= runeEnd &&
            o['surface'] == surface &&
            o['form'] == form &&
            o['form_reading'] == reading &&
            o['form_label'] == label &&
            o['form_meaning_en'] == meaning)
        .toList();
    return matches.length == 1 ? matches.single : null;
  }

  List<Map<String, dynamic>> search(String query) {
    String kana(String value) => String.fromCharCodes(value
        .toLowerCase()
        .runes
        .map((r) => r >= 0x30a1 && r <= 0x30f6 ? r - 0x60 : r));
    final q = kana(query.trim());
    return entries
        .where((e) => kana(
                '${e['title']} ${e['reading']} ${e['summary_en']} ${e['explanation_en']}')
            .contains(q))
        .toList();
  }
}
