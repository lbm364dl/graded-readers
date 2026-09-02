import '../models.dart';

abstract interface class LexiconStore {
  bool get isReady;

  Future<void> initialize();

  List<String> words(Language language);

  Map<String, dynamic>? dictionaryEntry(Language language, String word);

  int? characterLevel(String character);

  Map<String, dynamic>? etymologyEntry(String character);

  Map<String, dynamic>? glyphEntry(String character);
}
