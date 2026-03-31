import 'dictionary_service.dart';

bool _isChinese(int code) =>
    (code >= 0x4E00 && code <= 0x9FFF) ||
    (code >= 0x3400 && code <= 0x4DBF) ||
    (code >= 0xF900 && code <= 0xFAFF);

/// Segments Chinese text into words using max forward matching.
///
/// Each returned token is either a (possibly multi-char) Chinese word or a
/// run of non-Chinese characters (punctuation, whitespace, Latin, etc.).
List<String> segmentText(String text, DictionaryService dict) {
  if (!dict.isReady || text.isEmpty) return [text];

  final result = <String>[];
  int i = 0;

  while (i < text.length) {
    final code = text.codeUnitAt(i);

    if (!_isChinese(code)) {
      // Accumulate non-Chinese run as a single token
      int j = i + 1;
      while (j < text.length && !_isChinese(text.codeUnitAt(j))) {
        j++;
      }
      result.add(text.substring(i, j));
      i = j;
      continue;
    }

    // Chinese character: attempt max forward matching
    final maxLen = dict.maxWordLength.clamp(1, text.length - i);
    bool found = false;

    for (int len = maxLen; len > 1; len--) {
      final candidate = text.substring(i, i + len);
      if (dict.hasWord(candidate)) {
        result.add(candidate);
        i += len;
        found = true;
        break;
      }
    }

    if (!found) {
      // Single character (unknown word or single-char entry)
      result.add(text.substring(i, i + 1));
      i++;
    }
  }

  return result;
}
