import 'dart:convert';

import 'package:flutter/services.dart';
import '../models.dart';

/// Reviewed Korean word senses and grammar lessons tied to exact source spans.
class KoreanDictionary {
  final Map<String, dynamic> words;
  final Map<String, dynamic> grammar;

  KoreanDictionary(this.words, this.grammar) {
    for (final data in [words, grammar]) {
      if (data['language'] != 'korean') {
        throw StateError('Invalid Korean dictionary language');
      }
    }
  }

  static Future<KoreanDictionary>? _pending;
  static Future<KoreanDictionary> load() => _pending ??= _load();

  static Future<KoreanDictionary> _load() async {
    try {
      final values = await Future.wait([
        rootBundle.loadString('assets/usage_dictionary_ko.json'),
        rootBundle.loadString('assets/grammar_dictionary_ko.json'),
      ]);
      return KoreanDictionary(
        jsonDecode(values[0]) as Map<String, dynamic>,
        jsonDecode(values[1]) as Map<String, dynamic>,
      );
    } catch (_) {
      _pending = null;
      rethrow;
    }
  }

  Map<String, dynamic>? wordUse(String source, String sourceText,
      int segmentIndex, String surface, String gloss) {
    if (words['sources'][source]?['text'] != sourceText) return null;
    for (final value in words['occurrences'] as List) {
      final use = value as Map<String, dynamic>;
      if (use['source'] == source &&
          use['segment_index'] == segmentIndex &&
          use['surface'] == surface &&
          use['gloss'] == gloss) {
        return use;
      }
    }
    return null;
  }

  List<Map<String, dynamic>> grammarUses(
      String source, String sourceText, int segmentIndex, String surface) {
    if (grammar['sources'][source]?['text'] != sourceText) return [];
    final uses = (grammar['occurrences'] as List).cast<Map<String, dynamic>>();
    // Validate this exact target before exposing a wider reviewed phrase.
    final attested = [...(words['occurrences'] as List), ...uses].any((use) =>
        use['source'] == source &&
        use['segment_index'] == segmentIndex &&
        use['surface'] == surface);
    if (!attested) return [];
    return uses
        .where((use) =>
            use['source'] == source &&
            (use['segment_index'] == segmentIndex ||
                (use['display_form'] != null &&
                    use['segment_index'] <= segmentIndex &&
                    segmentIndex <= use['display_end_segment_index'])))
        .toList();
  }

  Map<String, dynamic>? formUse(String source, String sourceText,
      int segmentIndex, String surface, int stepIndex, AgentFormStep step) {
    if (grammar['sources'][source]?['text'] != sourceText) return null;
    final forms =
        (grammar['forms'] as List? ?? const []).cast<Map<String, dynamic>>();
    final matches = forms.where((form) =>
        form['source'] == source &&
        form['segment_index'] == segmentIndex &&
        form['surface'] == surface &&
        form['step_index'] == stepIndex &&
        form['form'] == step.form &&
        form['reading'] == step.reading &&
        form['label'] == step.label &&
        form['meaning_en'] == step.meaningEn &&
        (form['grammar_entry_ids'] as List).join('\u0000') ==
            step.grammarEntryIds.join('\u0000'));
    if (matches.length != 1 || step.grammarEntryIds.length != 1) return null;
    return grammarUses(source, sourceText, segmentIndex, surface)
        .where((use) =>
            use['segment_index'] == segmentIndex &&
            use['entry_id'] == step.grammarEntryIds.single)
        .singleOrNull;
  }

  Map<String, dynamic> entry(String id, {required bool isGrammar}) =>
      ((isGrammar ? grammar : words)['entries'] as List)
          .cast<Map<String, dynamic>>()
          .singleWhere((entry) => entry['id'] == id);
}
