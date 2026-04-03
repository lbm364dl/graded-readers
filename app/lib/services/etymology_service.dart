import 'dart:convert';
import 'package:flutter/services.dart';

class EtymologyNote {
  final String source;
  final String text;
  const EtymologyNote({required this.source, required this.text});
}

class EtymologyEntry {
  final String character;
  final String? formationType;
  final String? semanticComponent;
  final String? phoneticComponent;
  final String? ids;
  final int? strokes;
  final List<EtymologyNote> notes;
  final List<String> phoneticFamily;
  final String? mandarinReading;
  final String? japaneseOn;  // in katakana
  final String? japaneseKun; // in hiragana
  final String? definitions;

  const EtymologyEntry({
    required this.character,
    this.formationType,
    this.semanticComponent,
    this.phoneticComponent,
    this.ids,
    this.strokes,
    this.notes = const [],
    this.phoneticFamily = const [],
    this.mandarinReading,
    this.japaneseOn,
    this.japaneseKun,
    this.definitions,
  });

  String? get formationLabel {
    switch (formationType) {
      case 'pictographic':
        return 'Pictographic (象形)';
      case 'ideographic':
        return 'Ideographic (會意)';
      case 'phono-semantic':
        return 'Phono-semantic (形聲)';
      case 'indicative':
        return 'Indicative (指事)';
      case 'phonetic-loan':
        return 'Phonetic loan (假借)';
      default:
        return null;
    }
  }

  List<String> get components {
    final result = <String>[];
    if (semanticComponent != null && semanticComponent!.length == 1) {
      result.add(semanticComponent!);
    }
    if (phoneticComponent != null &&
        phoneticComponent!.length == 1 &&
        phoneticComponent != semanticComponent) {
      result.add(phoneticComponent!);
    }
    return result;
  }
}

// ---------------------------------------------------------------------------
// Romaji → kana conversion
// ---------------------------------------------------------------------------

const _romajiToKatakana = <String, String>{
  'kya': 'キャ', 'kyu': 'キュ', 'kyo': 'キョ',
  'sha': 'シャ', 'shi': 'シ', 'shu': 'シュ', 'sho': 'ショ',
  'cha': 'チャ', 'chi': 'チ', 'chu': 'チュ', 'cho': 'チョ',
  'tsu': 'ツ',
  'nya': 'ニャ', 'nyu': 'ニュ', 'nyo': 'ニョ',
  'hya': 'ヒャ', 'hyu': 'ヒュ', 'hyo': 'ヒョ',
  'mya': 'ミャ', 'myu': 'ミュ', 'myo': 'ミョ',
  'rya': 'リャ', 'ryu': 'リュ', 'ryo': 'リョ',
  'gya': 'ギャ', 'gyu': 'ギュ', 'gyo': 'ギョ',
  'ja': 'ジャ', 'ji': 'ジ', 'ju': 'ジュ', 'jo': 'ジョ',
  'bya': 'ビャ', 'byu': 'ビュ', 'byo': 'ビョ',
  'pya': 'ピャ', 'pyu': 'ピュ', 'pyo': 'ピョ',
  'ka': 'カ', 'ki': 'キ', 'ku': 'ク', 'ke': 'ケ', 'ko': 'コ',
  'sa': 'サ', 'si': 'シ', 'su': 'ス', 'se': 'セ', 'so': 'ソ',
  'ta': 'タ', 'ti': 'チ', 'tu': 'ツ', 'te': 'テ', 'to': 'ト',
  'na': 'ナ', 'ni': 'ニ', 'nu': 'ヌ', 'ne': 'ネ', 'no': 'ノ',
  'ha': 'ハ', 'hi': 'ヒ', 'hu': 'フ', 'fu': 'フ', 'he': 'ヘ', 'ho': 'ホ',
  'ma': 'マ', 'mi': 'ミ', 'mu': 'ム', 'me': 'メ', 'mo': 'モ',
  'ya': 'ヤ', 'yu': 'ユ', 'yo': 'ヨ',
  'ra': 'ラ', 'ri': 'リ', 'ru': 'ル', 're': 'レ', 'ro': 'ロ',
  'wa': 'ワ', 'wi': 'ヰ', 'we': 'ヱ', 'wo': 'ヲ',
  'ga': 'ガ', 'gi': 'ギ', 'gu': 'グ', 'ge': 'ゲ', 'go': 'ゴ',
  'za': 'ザ', 'zi': 'ジ', 'zu': 'ズ', 'ze': 'ゼ', 'zo': 'ゾ',
  'da': 'ダ', 'di': 'ヂ', 'du': 'ヅ', 'de': 'デ', 'do': 'ド',
  'ba': 'バ', 'bi': 'ビ', 'bu': 'ブ', 'be': 'ベ', 'bo': 'ボ',
  'pa': 'パ', 'pi': 'ピ', 'pu': 'プ', 'pe': 'ペ', 'po': 'ポ',
  'a': 'ア', 'i': 'イ', 'u': 'ウ', 'e': 'エ', 'o': 'オ',
  'n': 'ン',
};

const _romajiToHiragana = <String, String>{
  'kya': 'きゃ', 'kyu': 'きゅ', 'kyo': 'きょ',
  'sha': 'しゃ', 'shi': 'し', 'shu': 'しゅ', 'sho': 'しょ',
  'cha': 'ちゃ', 'chi': 'ち', 'chu': 'ちゅ', 'cho': 'ちょ',
  'tsu': 'つ',
  'nya': 'にゃ', 'nyu': 'にゅ', 'nyo': 'にょ',
  'hya': 'ひゃ', 'hyu': 'ひゅ', 'hyo': 'ひょ',
  'mya': 'みゃ', 'myu': 'みゅ', 'myo': 'みょ',
  'rya': 'りゃ', 'ryu': 'りゅ', 'ryo': 'りょ',
  'gya': 'ぎゃ', 'gyu': 'ぎゅ', 'gyo': 'ぎょ',
  'ja': 'じゃ', 'ji': 'じ', 'ju': 'じゅ', 'jo': 'じょ',
  'bya': 'びゃ', 'byu': 'びゅ', 'byo': 'びょ',
  'pya': 'ぴゃ', 'pyu': 'ぴゅ', 'pyo': 'ぴょ',
  'ka': 'か', 'ki': 'き', 'ku': 'く', 'ke': 'け', 'ko': 'こ',
  'sa': 'さ', 'si': 'し', 'su': 'す', 'se': 'せ', 'so': 'そ',
  'ta': 'た', 'ti': 'ち', 'tu': 'つ', 'te': 'て', 'to': 'と',
  'na': 'な', 'ni': 'に', 'nu': 'ぬ', 'ne': 'ね', 'no': 'の',
  'ha': 'は', 'hi': 'ひ', 'hu': 'ふ', 'fu': 'ふ', 'he': 'へ', 'ho': 'ほ',
  'ma': 'ま', 'mi': 'み', 'mu': 'む', 'me': 'め', 'mo': 'も',
  'ya': 'や', 'yu': 'ゆ', 'yo': 'よ',
  'ra': 'ら', 'ri': 'り', 'ru': 'る', 're': 'れ', 'ro': 'ろ',
  'wa': 'わ', 'wi': 'ゐ', 'we': 'ゑ', 'wo': 'を',
  'ga': 'が', 'gi': 'ぎ', 'gu': 'ぐ', 'ge': 'げ', 'go': 'ご',
  'za': 'ざ', 'zi': 'じ', 'zu': 'ず', 'ze': 'ぜ', 'zo': 'ぞ',
  'da': 'だ', 'di': 'ぢ', 'du': 'づ', 'de': 'で', 'do': 'ど',
  'ba': 'ば', 'bi': 'び', 'bu': 'ぶ', 'be': 'べ', 'bo': 'ぼ',
  'pa': 'ぱ', 'pi': 'ぴ', 'pu': 'ぷ', 'pe': 'ぺ', 'po': 'ぽ',
  'a': 'あ', 'i': 'い', 'u': 'う', 'e': 'え', 'o': 'お',
  'n': 'ん',
};

/// Convert space-separated uppercase romaji words to kana.
/// E.g. "NETSU ZETSU" → "ネツ・ゼツ" (katakana)
///      "ATSUI KOKORO" → "あつい・こころ" (hiragana)
String _romajiToKana(String romaji, Map<String, String> table) {
  final words = romaji.split(' ');
  final result = <String>[];
  for (final word in words) {
    final lower = word.toLowerCase();
    final buf = StringBuffer();
    int i = 0;
    while (i < lower.length) {
      // Handle double consonant (gemination) → っ/ッ
      if (i + 1 < lower.length &&
          lower[i] == lower[i + 1] &&
          lower[i] != 'a' && lower[i] != 'i' && lower[i] != 'u' &&
          lower[i] != 'e' && lower[i] != 'o' && lower[i] != 'n') {
        buf.write(table == _romajiToKatakana ? 'ッ' : 'っ');
        i++;
        continue;
      }
      // Try 3-char, 2-char, 1-char matches
      bool matched = false;
      for (final len in [3, 2, 1]) {
        if (i + len <= lower.length) {
          final sub = lower.substring(i, i + len);
          if (table.containsKey(sub)) {
            buf.write(table[sub]);
            i += len;
            matched = true;
            break;
          }
        }
      }
      if (!matched) {
        buf.write(lower[i]); // keep unknown chars as-is
        i++;
      }
    }
    result.add(buf.toString());
  }
  return result.join('・');
}

String romajiToKatakana(String romaji) => _romajiToKana(romaji, _romajiToKatakana);
String romajiToHiragana(String romaji) => _romajiToKana(romaji, _romajiToHiragana);

class EtymologyService {
  EtymologyService._();
  static final EtymologyService instance = EtymologyService._();

  Map<String, EtymologyEntry>? _entries;

  bool get isReady => _entries != null;

  Future<void> initialize() async {
    if (_entries != null) return;
    final raw = await rootBundle.loadString('assets/etymology.json');
    final data = json.decode(raw) as Map<String, dynamic>;
    _entries = {};
    for (final e in data.entries) {
      final ch = e.key;
      final v = e.value as Map<String, dynamic>;
      final notesList = (v['en'] as List?)
              ?.map((n) => EtymologyNote(
                    source: (n['src'] as String?) ?? '',
                    text: (n['t'] as String?) ?? '',
                  ))
              .toList() ??
          [];
      final pf = (v['pf'] as List?)?.cast<String>() ?? [];
      final r = v['r'] as Map<String, dynamic>?;
      final onRaw = r?['on'] as String?;
      final kunRaw = r?['kun'] as String?;
      _entries![ch] = EtymologyEntry(
        character: ch,
        formationType: v['ft'] as String?,
        semanticComponent: v['s'] as String?,
        phoneticComponent: v['ph'] as String?,
        ids: v['ids'] as String?,
        strokes: v['st'] as int?,
        notes: notesList,
        phoneticFamily: pf,
        mandarinReading: r?['zh'] as String?,
        japaneseOn: onRaw != null ? romajiToKatakana(onRaw) : null,
        japaneseKun: kunRaw != null ? romajiToHiragana(kunRaw) : null,
        definitions: v['d'] as String?,
      );
    }
  }

  EtymologyEntry? lookup(String character) => _entries?[character];
}
