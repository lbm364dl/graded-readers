import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/data.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/korean_dictionary.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('Korean pilot loads with the reviewed source-aligned annotation',
      () async {
    final books = await ContentRepository().loadBooks(Language.korean);
    expect(books, hasLength(1));
    expect(books.single.title, '홍길동전');
    final reader = books.single.levels[1]!;
    expect(reader.levelLabel, 'Level 1');
    final chapter = reader.chapters.single;
    final annotation =
        jsonDecode(await rootBundle.loadString(chapter.annotationAsset))
            as Map<String, dynamic>;
    expect(annotation['text'], chapter.content);
    final segments =
        (annotation['segments'] as List).cast<Map<String, dynamic>>();
    expect(segments.map((segment) => segment['text']).join(), chapter.content);
    expect(
        segments
            .where((segment) => segment['type'] == 'word')
            .every((segment) => (segment['meaning_en'] as String).isNotEmpty),
        isTrue);
  });

  test('Korean word and grammar links require the exact source occurrence',
      () async {
    final dictionary = await KoreanDictionary.load();
    final source = dictionary.words['sources'].keys.single as String;
    final text = dictionary.words['sources'][source]['text'] as String;
    final use = dictionary.wordUse(source, text, 0, '옛날에', 'long ago');
    expect(use?['entry_id'], '옛날/명');
    expect(dictionary.wordUse(source, '$text!', 0, '옛날에', 'long ago'), isNull);
    expect(dictionary.wordUse(source, text, 0, '옛날에', 'yesterday'), isNull);
    expect(dictionary.grammarUses(source, text, 3, '이라는').single['entry_id'],
        'naming-iran');
    expect(dictionary.grammarUses(source, text, 3, '라고'), isEmpty);
  });
}
