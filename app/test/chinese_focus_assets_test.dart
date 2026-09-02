import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('every published Chinese reader has visible reviewed focus cues',
      () async {
    final content = (json.decode(
      await rootBundle.loadString('assets/content.json'),
    ) as List<dynamic>)
        .cast<Map<String, dynamic>>();

    expect(content, isNotEmpty);
    for (final reader in content) {
      for (final rawChapter in reader['chapters'] as List<dynamic>) {
        final chapter = rawChapter as Map<String, dynamic>;
        final asset = chapter['annotationAsset'] as String;
        final annotation = json.decode(
          await rootBundle.loadString(asset),
        ) as Map<String, dynamic>;
        final segments = (annotation['segments'] as List<dynamic>)
            .cast<Map<String, dynamic>>();
        final lexical = segments
            .where((segment) => segment['type'] != 'punctuation')
            .toList();

        expect(
          segments.map((segment) => segment['text'] as String).join(),
          annotation['text'],
          reason: '$asset must exactly reconstruct its prose',
        );
        expect(
          lexical.where((segment) => segment['learning_focus'] == 'lookup'),
          isNotEmpty,
          reason: '$asset must expose optional lookup markings',
        );
        expect(
          lexical
              .where((segment) => (segment['story_term'] as String).isNotEmpty),
          isNotEmpty,
          reason: '$asset must expose reviewed story-vocabulary markings',
        );
        expect(
          annotation['learning_focus_audit']['semantic_focus_reviewed'],
          isTrue,
          reason: '$asset must not publish unreviewed focus metadata',
        );
      }
    }
  });
}
