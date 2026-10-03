import 'dart:convert';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/grammar_dictionary.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
      'Old component IDs resolve to canonical words without duplicate search results',
      () {
    final dictionary = UsageDictionary.fromJson({
      'language': 'japanese',
      'sources': {},
      'occurrences': [],
      'entries': [
        {
          'id': 'canonical',
          'headword': 'する',
          'reading': 'する',
          'kind': 'word',
          'senses': [
            {'definition': 'do'}
          ]
        },
        {
          'id': 'component',
          'headword': 'する',
          'reading': 'する',
          'kind': 'word',
          'superseded_by': 'canonical',
          'senses': [
            {'definition': 'do'}
          ]
        },
      ],
    });
    expect(dictionary.entry('component')['id'], 'canonical');
    expect(dictionary.search('する'), hasLength(1));
    expect(dictionary.search(''), hasLength(1));
  });
  test(
      'Every published Japanese level resolves source words and reviewed form steps',
      () async {
    final words = await UsageDictionary.load(language: Language.japanese);
    final grammar = await GrammarDictionary.load();
    expect(grammar.sources.keys.toSet(), words.sources.keys.toSet());
    var forms = 0;
    for (final source in words.sources.keys) {
      final annotation = AgentChapterAnnotation.fromJson(
          jsonDecode(await rootBundle.loadString(source))
              as Map<String, dynamic>);
      expect(annotation.text, words.sources[source]['text']);
      var offset = 0;
      for (var index = 0; index < annotation.segments.length; index++) {
        final segment = annotation.segments[index];
        if (segment.type != 'punctuation') {
          final use = words.occurrence(source, index, annotation.text,
              surface: segment.text,
              reading: segment.pinyin,
              gloss: segment.meaningEn,
              startOffset: offset);
          expect(use, isNotNull, reason: '$source: ${segment.text}');
          expect(words.entry(use!['entry_id'] as String)['meaning_guide'],
              isNotNull);
        }
        for (var stepIndex = 0;
            stepIndex < segment.formSteps.length;
            stepIndex++) {
          final step = segment.formSteps[stepIndex];
          final use = grammar.formStep(
              source,
              annotation.text,
              offset,
              segment.text,
              index,
              stepIndex,
              step.form,
              step.reading,
              step.label,
              step.meaningEn);
          expect(use, isNotNull, reason: '$source: ${step.form}');
          for (final id in (use!['entry_ids'] as List).cast<String>()) {
            expect(grammar.entry(id)['explanation_en'], isNotEmpty);
          }
          if (words.sources[source]['level'] != 'N5') {
            expect(use['display_meaning_en'], isNotEmpty);
            expect(use['display_base_meaning_en'], isNotEmpty);
          }
          forms++;
        }
        offset += segment.text.length;
      }
      expect(offset, annotation.text.length);
    }
    expect(forms, greaterThan(0));
  });
}
