import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';
import 'package:hsk_graded/services/dictionary_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    await DictionaryService.instance.initialize(language: Language.chinese);
  });

  test('single-character lookups keep word and New HSK character bands apart',
      () {
    final compound = DictionaryService.instance.lookup('批评')!;
    final first = DictionaryService.instance.lookup('批')!;
    final second = DictionaryService.instance.lookup('评')!;

    expect(compound.hskLevel, 3);
    expect(compound.characterHskLevel, isNull);

    expect(first.hskLevel, 4);
    expect(first.characterHskLevel, 3);

    expect(second.hskLevel, 6);
    expect(second.characterHskLevel, 3);
  });
}
