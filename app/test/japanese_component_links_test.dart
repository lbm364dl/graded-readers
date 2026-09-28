import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/screens/usage_dictionary_screen.dart';
import 'package:hsk_graded/services/grammar_dictionary.dart';
import 'package:hsk_graded/services/usage_dictionary.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
      'An inflected word component links to its base and independent grammar',
      (tester) async {
    await tester.runAsync(GrammarDictionary.load);
    final eat = <String, dynamic>{
      'id': 'eat',
      'headword': '食べる',
      'reading': 'たべる',
      'kind': 'word',
      'senses': [
        {'id': 'eat-1', 'definition': 'eat'}
      ],
      'meaning_guide': {
        'structure': 'single_component',
        'explanation_en': 'To eat.',
        'parts': [],
        'caveat_en': ''
      }
    };
    final food = <String, dynamic>{
      'id': 'food',
      'headword': '食べ物',
      'reading': 'たべもの',
      'kind': 'word',
      'senses': [
        {'id': 'food-1', 'definition': 'food'}
      ],
      'meaning_guide': {
        'structure': 'compositional',
        'explanation_en': 'Eat plus thing makes food.',
        'caveat_en': '',
        'parts': [
          {
            'text': '食べ',
            'contribution_en': 'Stem of eat.',
            'start': 0,
            'end': 2,
            'entry_id': 'eat',
            'grammar_entry_ids': ['ja-grammar-conjunctive-stem']
          },
          {'text': '物', 'contribution_en': 'thing', 'start': 2, 'end': 3}
        ]
      }
    };
    final dictionary = UsageDictionary.fromJson({
      'language': 'japanese',
      'sources': {},
      'entries': [food, eat],
      'occurrences': []
    });
    await tester.pumpWidget(MaterialApp(
        home: UsageEntryScreen(dictionary: dictionary, entry: food)));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('meaning-part-0')), findsOneWidget);
    final grammarLink = find.byKey(
        const ValueKey('meaning-part-grammar-0-ja-grammar-conjunctive-stem'));
    expect(grammarLink, findsOneWidget);
    await tester.ensureVisible(grammarLink);
    await tester.tap(grammarLink);
    await tester.pumpAndSettle();
    expect(find.text('連用形（ます語幹）'), findsOneWidget);
    expect(find.text('Used in this passage'), findsNothing);
  });
}
