import 'dart:async';
import 'dart:convert';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models.dart';
import '../theme.dart';
import '../services/dictionary_service.dart';
import '../services/etymology_service.dart';
import '../services/glyph_service.dart';
import '../services/progress_service.dart';
import '../services/segmenter.dart';
import '../services/chinese_reading_units.dart';
import '../services/vocabulary_service.dart';
import '../widgets/content_width.dart';
import '../widgets/copy_text_button.dart';
import '../widgets/japanese_form_chain.dart';
import '../widgets/japanese_grammar_context.dart';
import 'usage_dictionary_screen.dart';
import 'grammar_dictionary_screen.dart';

TextStyle _cjkTextStyle({
  required double fontSize,
  required Language language,
  Color? color,
  FontWeight? fontWeight,
  double? height,
  Color? backgroundColor,
}) {
  final primaryFamily =
      language == Language.japanese ? 'NotoSansJP' : 'NotoSansSC';
  final fallbackFamily =
      language == Language.japanese ? 'NotoSansSC' : 'NotoSansJP';
  return TextStyle(
    fontFamily: primaryFamily,
    fontSize: fontSize,
    color: color,
    fontWeight: fontWeight,
    height: height,
    backgroundColor: backgroundColor,
  ).copyWith(
    fontFamilyFallback: [
      fallbackFamily,
      // System fonts for CJK Extension B+ characters
      'sans-serif',
    ],
  );
}

String _displayDictionaryReading(String reading, Language language) =>
    language == Language.chinese ? reading.toLowerCase() : reading;

// ---------------------------------------------------------------------------
// Furigana helper
// ---------------------------------------------------------------------------

/// Count consecutive kanji in a word.
int _kanjiCount(String word) => word.codeUnits.where(_isKanji).length;

/// For multi-kanji compounds (e.g. 一生懸命), segment into sub-words
/// using the dictionary. Returns null if not a multi-kanji compound
/// or if segmentation just gives single characters.
List<String>? _segmentCompound(String word) {
  if (_kanjiCount(word) < 2) return null;

  final dict = DictionaryService.instance;
  if (!dict.isReady) return null;

  // Max-forward matching but skip the full word itself
  final tokens = <String>[];
  int i = 0;
  while (i < word.length) {
    final maxLen = (word.length - i).clamp(1, dict.maxWordLength);
    bool found = false;
    for (int len = maxLen; len > 1; len--) {
      // Skip if this would match the entire original word
      if (i == 0 && len == word.length) continue;
      final candidate = word.substring(i, i + len);
      if (dict.hasWord(candidate)) {
        tokens.add(candidate);
        i += len;
        found = true;
        break;
      }
    }
    if (!found) {
      tokens.add(word.substring(i, i + 1));
      i++;
    }
  }

  // Only useful if we got multi-char sub-words
  if (tokens.length <= 1) return null;
  if (tokens.every((t) => t.length <= 1)) return null;
  return tokens;
}

bool _isKanji(int code) =>
    (code >= 0x4E00 && code <= 0x9FFF) ||
    (code >= 0x3400 && code <= 0x4DBF) ||
    (code >= 0xF900 && code <= 0xFAFF);

/// Formats a Japanese dictionary form without echoing kana as its own reading.
/// A separate reading only adds information when the form contains kanji.
String formatJapaneseLemmaReading(String lemma, String reading) {
  final cleanLemma = lemma.trim();
  final cleanReading = reading.trim();
  final hasKanji = cleanLemma.codeUnits.any(_isKanji);
  if (cleanReading.isEmpty || cleanReading == cleanLemma || !hasKanji) {
    return cleanLemma;
  }
  return '$cleanLemma（$cleanReading）';
}

/// Builds a furigana (ruby) widget: kana reading displayed above kanji.
/// Falls back to plain text if there's no reading or no kanji.
Widget _buildFurigana({
  required String word,
  required String reading,
  required Language language,
  required double fontSize,
  void Function(String)? onCharTap,
}) {
  // No reading or word is all kana → just show the word
  final hasKanji = word.codeUnits.any(_isKanji);
  if (reading.isEmpty || !hasKanji) {
    return _buildPlainWord(
      word: word,
      language: language,
      fontSize: fontSize,
      onCharTap: onCharTap,
    );
  }

  // Split word into kanji/kana segments paired with reading portions.
  // E.g. 食べる + たべる → [(食,た), (べる,べる)]
  final segments = _alignFurigana(word, reading);

  final rubyFontSize = fontSize * 0.38;

  return Wrap(
    crossAxisAlignment: WrapCrossAlignment.end,
    children: segments.map((seg) {
      final isKanjiSeg = seg.word.codeUnits.any(_isKanji);

      final wordStyle = _cjkTextStyle(
        fontSize: fontSize,
        language: language,
        fontWeight: FontWeight.bold,
        height: 1.2,
      );

      // Kanji segment: show reading above, tappable characters below
      if (isKanjiSeg) {
        final readingWidget = Text(
          seg.reading,
          style: _cjkTextStyle(
            fontSize: rubyFontSize,
            language: language,
            color: Colors.grey[500],
            height: 1.1,
          ),
          textAlign: TextAlign.center,
        );

        if (onCharTap != null) {
          return Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              readingWidget,
              const SizedBox(height: 1),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: seg.word.characters.map((ch) {
                  return GestureDetector(
                    onTap: () => onCharTap(ch),
                    child: Text(ch, style: wordStyle),
                  );
                }).toList(),
              ),
            ],
          );
        }

        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            readingWidget,
            const SizedBox(height: 1),
            Text(seg.word, style: wordStyle),
          ],
        );
      }

      // Kana segment: no ruby, not tappable, align baseline with kanji segments
      return Padding(
        padding: EdgeInsets.only(top: rubyFontSize + 1),
        child: Text(seg.word, style: wordStyle),
      );
    }).toList(),
  );
}

Widget _buildPlainWord({
  required String word,
  required Language language,
  required double fontSize,
  void Function(String)? onCharTap,
}) {
  if (onCharTap != null && word.length > 1) {
    final style = _cjkTextStyle(
      fontSize: fontSize,
      language: language,
      fontWeight: FontWeight.bold,
      height: 1.2,
    );
    return Wrap(
      children: word.characters.map((ch) {
        final isKanji = ch.codeUnits.any(_isKanji);
        if (isKanji) {
          return GestureDetector(
            onTap: () => onCharTap(ch),
            child: Text(ch, style: style),
          );
        }
        return Text(ch, style: style);
      }).toList(),
    );
  }
  return Text(
    word,
    style: _cjkTextStyle(
      fontSize: fontSize,
      language: language,
      fontWeight: FontWeight.bold,
      height: 1.2,
    ),
  );
}

class _FuriganaPair {
  final String word;
  final String reading;
  _FuriganaPair(this.word, this.reading);
}

/// Aligns kanji in [word] with kana in [reading] by matching shared kana.
/// Falls back to showing the full reading over the full word if alignment fails.
List<_FuriganaPair> _alignFurigana(String word, String reading) {
  // Simple case: no kanji at all
  if (!word.codeUnits.any(_isKanji)) {
    return [_FuriganaPair(word, word)];
  }

  // Split word into alternating kanji/kana runs
  final segments = <({String text, bool isKanji})>[];
  int i = 0;
  while (i < word.length) {
    final kanji = _isKanji(word.codeUnitAt(i));
    int j = i + 1;
    while (j < word.length && _isKanji(word.codeUnitAt(j)) == kanji) {
      j++;
    }
    segments.add((text: word.substring(i, j), isKanji: kanji));
    i = j;
  }

  // Try to align: match kana segments in reading to find kanji readings
  final pairs = <_FuriganaPair>[];
  int ri = 0;
  for (int si = 0; si < segments.length; si++) {
    final seg = segments[si];
    if (!seg.isKanji) {
      // Kana segment — advance reading pointer past it
      pairs.add(_FuriganaPair(seg.text, seg.text));
      ri += seg.text.length;
    } else {
      // Kanji segment — find where it ends in the reading by looking
      // for the next kana segment as an anchor
      String? nextKana;
      for (int ni = si + 1; ni < segments.length; ni++) {
        if (!segments[ni].isKanji) {
          nextKana = segments[ni].text;
          break;
        }
      }
      if (nextKana != null && ri < reading.length) {
        final anchor = reading.indexOf(nextKana, ri);
        if (anchor > ri) {
          pairs.add(_FuriganaPair(seg.text, reading.substring(ri, anchor)));
          ri = anchor;
          continue;
        }
      }
      // Last segment or no anchor — consume remaining reading
      final remaining = ri < reading.length ? reading.substring(ri) : '';
      // Strip any trailing kana that belongs to later segments
      int trailingKanaLen = 0;
      for (int ni = si + 1; ni < segments.length; ni++) {
        if (!segments[ni].isKanji) trailingKanaLen += segments[ni].text.length;
      }
      final end = remaining.length - trailingKanaLen;
      if (end > 0) {
        pairs.add(_FuriganaPair(seg.text, remaining.substring(0, end)));
        ri += end;
      } else {
        // Fallback: can't align, just show full reading over full word
        return [_FuriganaPair(word, reading)];
      }
    }
  }

  return pairs;
}

// ---------------------------------------------------------------------------
// Data structures for segmentation & pagination
// ---------------------------------------------------------------------------

bool _isCJK(int code) =>
    (code >= 0x4E00 && code <= 0x9FFF) ||
    (code >= 0x3400 && code <= 0x4DBF) ||
    (code >= 0xF900 && code <= 0xFAFF) ||
    (code >= 0x3040 && code <= 0x309F) ||
    (code >= 0x30A0 && code <= 0x30FF);

/// A CJK token is "tappable" (a real word, not a particle) if it contains
/// kanji or is a multi-character kana word.
bool _isTappableWord(String text) =>
    text.length > 1 ||
    text.codeUnits.any((c) =>
        (c >= 0x4E00 && c <= 0x9FFF) ||
        (c >= 0x3400 && c <= 0x4DBF) ||
        (c >= 0xF900 && c <= 0xFAFF));

/// Parse a markdown table block into rows of cells.
List<List<String>> _parseMarkdownTable(String block) {
  final rows = <List<String>>[];
  for (final line in block.split('\n')) {
    final trimmed = line.trim();
    if (trimmed.isEmpty) continue;
    // Skip separator rows (|---|---|)
    if (RegExp(r'^\|[\s\-:]+\|$').hasMatch(
        trimmed.replaceAll('|', '|').replaceAll(RegExp(r'[^|\-:\s]'), ''))) {
      // More reliable: check if all cells are just dashes/colons/spaces
      final cells =
          trimmed.split('|').where((c) => c.trim().isNotEmpty).toList();
      if (cells.every((c) => RegExp(r'^[\s\-:]+$').hasMatch(c))) continue;
    }
    final cells = trimmed
        .split('|')
        .map((c) => c.trim())
        .where((c) => c.isNotEmpty)
        .toList();
    if (cells.isNotEmpty) rows.add(cells);
  }
  return rows;
}

/// Build etymology section for a character (if available).
Widget _sectionLabel(String text) => Padding(
      padding: const EdgeInsets.only(top: 10, bottom: 4),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w700,
          color: Colors.grey[500],
          letterSpacing: 0.5,
        ),
      ),
    );

List<Widget> _buildEtymologyWidgets(
  BuildContext context,
  String character,
  bool isDark, {
  void Function(String)? onComponentTap,
}) {
  final etym = EtymologyService.instance.lookup(character);
  final glyphs = GlyphService.instance.lookup(character);
  if (etym == null && glyphs == null) return [];

  final widgets = <Widget>[];

  // --- Decomposition ---
  if (etym != null) {
    final hasDecomp = etym.formationLabel != null ||
        etym.ids != null ||
        etym.components.isNotEmpty;
    if (hasDecomp) {
      widgets.add(_sectionLabel('Decomposition'));

      // Formation type + IDS + strokes
      final meta = <String>[];
      if (etym.formationLabel != null) meta.add(etym.formationLabel!);
      if (etym.ids != null) meta.add(etym.ids!);
      if (etym.strokes != null) meta.add('${etym.strokes} strokes');
      if (meta.isNotEmpty) {
        widgets.add(Text(
          meta.join(' · '),
          style: TextStyle(
            fontSize: 12,
            color: isDark ? Colors.grey[400] : Colors.grey[600],
          ),
        ));
      }

      // Components (semantic + phonetic)
      final dict = DictionaryService.instance;
      final etymSvc = EtymologyService.instance;
      for (final comp in etym.components) {
        final compEntry = dict.lookup(comp);
        final compEtym = etymSvc.lookup(comp);
        final isSemantic = comp == etym.semanticComponent;

        final lang = DictionaryService.instance.activeLanguage;
        final desc = <String>[];
        if (isSemantic) {
          desc.add('semantic');
          // Show definition from active language dict first
          if (compEntry != null && compEntry.definitions.isNotEmpty) {
            desc.add(compEntry.definitions.first);
          } else if (compEtym?.definitions != null) {
            desc.add(compEtym!.definitions!);
          }
        } else {
          desc.add('phonetic');
          // Show reading in active language
          if (lang == Language.japanese) {
            if (compEtym?.japaneseOn != null) {
              desc.add(compEtym!.japaneseOn!);
            }
          } else {
            if (compEtym?.mandarinReading != null) {
              desc.add(compEtym!.mandarinReading!);
            }
          }
        }

        widgets.add(Padding(
          padding: const EdgeInsets.only(top: 3),
          child: GestureDetector(
            onTap: onComponentTap != null ? () => onComponentTap(comp) : null,
            child: Text.rich(
              TextSpan(children: [
                TextSpan(
                  text: comp,
                  style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                    color: isSemantic ? AppTheme.primary : Colors.blue[400],
                  ),
                ),
                TextSpan(
                  text: '  ${desc.join(" · ")}',
                  style: TextStyle(
                    fontSize: 12,
                    color: isDark ? Colors.grey[400] : Colors.grey[600],
                  ),
                ),
              ]),
            ),
          ),
        ));
      }
    }
  }

  // --- Historical Forms ---
  if (glyphs != null) {
    final eras = glyphs.sortedEras;
    widgets.add(_sectionLabel('Historical Forms'));
    widgets.add(Wrap(
      spacing: 8,
      runSpacing: 8,
      children: eras.map((era) {
        final svg = glyphs.eras[era]!;
        return GestureDetector(
          onTap: () => _showGlyphFullscreen(context, character, glyphs, era),
          child: Container(
            width: 48,
            height: 48,
            padding: const EdgeInsets.all(4),
            decoration: BoxDecoration(
              color: isDark ? Colors.grey[800] : Colors.grey[100],
              borderRadius: BorderRadius.circular(8),
            ),
            child: SvgPicture.string(
              svg,
              colorFilter: ColorFilter.mode(
                isDark ? Colors.grey[300]! : Colors.grey[800]!,
                BlendMode.srcIn,
              ),
            ),
          ),
        );
      }).toList(),
    ));
  }

  if (etym == null) return widgets;

  // --- Series sections ---
  for (final (chars, total, label) in [
    (etym.phoneticSeries, etym.phoneticSeriesTotal, 'Phonetic Series'),
    (etym.semanticSeries, etym.semanticSeriesTotal, 'Semantic Series'),
    (etym.phoneticSiblings, etym.phoneticSiblingsTotal, 'Phonetic Siblings'),
    (etym.semanticSiblings, etym.semanticSiblingsTotal, 'Semantic Siblings'),
  ]) {
    if (chars.isEmpty) continue;
    final countNote = total != null && total > chars.length
        ? ' (${chars.length} of $total)'
        : '';
    widgets.add(_sectionLabel('$label$countNote'));
    widgets.add(Wrap(
      spacing: 3,
      runSpacing: 2,
      children: chars
          .map((ch) => GestureDetector(
                onTap: onComponentTap != null ? () => onComponentTap(ch) : null,
                child: Text(
                  ch,
                  style: TextStyle(fontSize: 15, color: Colors.blue[300]),
                ),
              ))
          .toList(),
    ));
  }

  // --- Etymology ---
  if (etym.notes.isNotEmpty) {
    widgets.add(_sectionLabel('Etymology'));
    for (final note in etym.notes) {
      widgets.add(Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (note.source.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 2),
                child: Text(
                  note.source,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    color: Colors.grey[500],
                  ),
                ),
              ),
            _buildTappableText(
              note.text,
              isDark: isDark,
              onCharTap: onComponentTap,
            ),
          ],
        ),
      ));
    }
  }

  return widgets;
}

/// Build text with tappable kanji characters.
Widget _buildTappableText(
  String text, {
  required bool isDark,
  void Function(String)? onCharTap,
}) {
  if (onCharTap == null) {
    return Text(
      text,
      style: TextStyle(
        fontSize: 13,
        height: 1.4,
        color: isDark ? Colors.grey[300] : Colors.grey[700],
      ),
    );
  }

  final baseStyle = TextStyle(
    fontSize: 13,
    height: 1.4,
    color: isDark ? Colors.grey[300] : Colors.grey[700],
  );

  // Split text into runs of kanji vs non-kanji
  final spans = <InlineSpan>[];
  int i = 0;
  while (i < text.length) {
    if (_isKanji(text.codeUnitAt(i))) {
      final ch = text[i];
      spans.add(WidgetSpan(
        alignment: PlaceholderAlignment.middle,
        child: GestureDetector(
          onTap: () => onCharTap(ch),
          child: Text(ch, style: baseStyle.copyWith(color: Colors.blue[300])),
        ),
      ));
      i++;
    } else {
      int j = i + 1;
      while (j < text.length && !_isKanji(text.codeUnitAt(j))) {
        j++;
      }
      spans.add(TextSpan(text: text.substring(i, j), style: baseStyle));
      i = j;
    }
  }

  return Text.rich(TextSpan(children: spans));
}

void _showGlyphFullscreen(BuildContext context, String character,
    GlyphEntry glyphs, String initialEra) {
  final isDark = Theme.of(context).brightness == Brightness.dark;
  final eras = glyphs.sortedEras;
  final initialIndex = eras.indexOf(initialEra).clamp(0, eras.length - 1);

  showDialog(
    context: context,
    builder: (context) {
      int current = initialIndex;
      return StatefulBuilder(
        builder: (context, setState) {
          final era = eras[current];
          final svg = glyphs.eras[era]!;
          final label = GlyphEntry.labelFor(era);

          return Dialog(
            backgroundColor: isDark ? Colors.grey[900] : Colors.white,
            insetPadding: const EdgeInsets.all(32),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 20),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  // Close button
                  Align(
                    alignment: Alignment.topRight,
                    child: GestureDetector(
                      onTap: () => Navigator.pop(context),
                      child:
                          Icon(Icons.close, size: 20, color: Colors.grey[500]),
                    ),
                  ),
                  // SVG
                  SizedBox(
                    width: 220,
                    height: 220,
                    child: SvgPicture.string(
                      svg,
                      colorFilter: ColorFilter.mode(
                        isDark ? Colors.grey[300]! : Colors.grey[800]!,
                        BlendMode.srcIn,
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  // Navigation: < label >
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      IconButton(
                        onPressed: () => setState(
                            () => current = (current - 1) % eras.length),
                        icon: const Icon(Icons.chevron_left),
                        visualDensity: VisualDensity.compact,
                      ),
                      SizedBox(
                        width: 120,
                        child: Text(
                          '$character · $label',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w600,
                            color: isDark ? Colors.grey[300] : Colors.grey[700],
                          ),
                        ),
                      ),
                      IconButton(
                        onPressed: () => setState(
                            () => current = (current + 1) % eras.length),
                        icon: const Icon(Icons.chevron_right),
                        visualDensity: VisualDensity.compact,
                      ),
                    ],
                  ),
                  // Counter
                  Text(
                    '${current + 1} / ${eras.length}',
                    style: TextStyle(fontSize: 11, color: Colors.grey[500]),
                  ),
                ],
              ),
            ),
          );
        },
      );
    },
  );
}

class _TokenEntry {
  final String text;
  final bool isCjk;
  final int globalIndex;
  final int startOffset;
  final int endOffset;
  _TokenEntry({
    required this.text,
    required this.isCjk,
    required this.globalIndex,
    required this.startOffset,
    required this.endOffset,
  });
}

class _TappedWord {
  final int? usageStartOffset;
  final String usageSource;
  final String usageSourceText;
  final int usageSegmentIndex;
  final String surface;
  final AgentSegment? agent;
  final List<AgentGrammarOverlay> grammar;
  final List<_TappedWord> nestedAgentTargets;

  const _TappedWord({
    this.usageStartOffset,
    this.usageSource = '',
    this.usageSourceText = '',
    this.usageSegmentIndex = -1,
    required this.surface,
    this.agent,
    this.grammar = const [],
    this.nestedAgentTargets = const [],
  });
}

enum _BlockType { paragraph, heading, subheading, divider, table, blockquote }

class _ParagraphData {
  final String raw;
  final String plainText;
  final bool isHeading;
  final _BlockType blockType;
  final int headingLevel; // 1 for ##, 2 for ###
  final List<_TokenEntry> tokens;
  final List<List<String>>? tableRows; // for table blocks
  _ParagraphData({
    required this.raw,
    required this.plainText,
    required this.isHeading,
    this.blockType = _BlockType.paragraph,
    this.headingLevel = 0,
    required this.tokens,
    this.tableRows,
  });
}

// ---------------------------------------------------------------------------
// Reader screen
// ---------------------------------------------------------------------------

class ReaderScreen extends StatefulWidget {
  final int? initialCharacterOffset;
  final Reader reader;
  final int initialChapter;

  const ReaderScreen({
    super.key,
    this.initialCharacterOffset,
    required this.reader,
    required this.initialChapter,
  });

  @override
  State<ReaderScreen> createState() => _ReaderScreenState();
}

class _ReaderScreenState extends State<ReaderScreen> {
  double _fontSize = 20.0;
  final double _minFontSize = 14.0;
  final double _maxFontSize = 32.0;
  final ValueNotifier<int> _highlightedIndex = ValueNotifier(-1);
  Timer? _progressSaveTimer;

  // Chapter navigation
  late PageController _chapterController;
  int _currentChapter = 0;
  bool _ready = false;

  // Scroll position per chapter (fraction 0.0-1.0)
  final Map<int, double> _scrollFractions = {};

  // Lazy chapter segmentation cache
  final Map<int, _ChapterSegmented> _segmentCache = {};

  @override
  void initState() {
    super.initState();
    _loadPreferences();
    VocabularyService.instance.loadWords();
  }

  Future<_ChapterSegmented> _getSegmentedAsync(int chapterIndex) async {
    if (_segmentCache.containsKey(chapterIndex)) {
      return _segmentCache[chapterIndex]!;
    }

    final dict = DictionaryService.instance;
    final ch = widget.reader.chapters[chapterIndex];

    if (ch.annotationAsset.isNotEmpty) {
      final encoded = await rootBundle.loadString(ch.annotationAsset);
      final decoded = json.decode(encoded);
      if (decoded is! Map<String, dynamic>) {
        throw StateError('Agent annotation is not a JSON object');
      }
      final annotation = AgentChapterAnnotation.fromJson(decoded);
      if (annotation.text != ch.content ||
          annotation.segments.map((item) => item.text).join() != ch.content) {
        throw StateError(
            'Agent annotation does not reconstruct ${widget.reader.id} chapter ${chapterIndex + 1}');
      }

      final words = <_TappedWord>[];
      final paragraphs = <_ParagraphData>[];
      final paragraphTokens = <_TokenEntry>[];
      final paragraphText = StringBuffer();
      var offset = 0;

      void finishParagraph() {
        if (paragraphTokens.isEmpty) return;
        final text = paragraphText.toString();
        paragraphs.add(_ParagraphData(
          raw: text,
          plainText: text,
          isHeading: false,
          tokens: List.of(paragraphTokens),
        ));
        paragraphTokens.clear();
        paragraphText.clear();
      }

      final displayUnits = widget.reader.language == Language.japanese
          ? buildJapaneseDisplayUnits(annotation)
          : (await ChineseReadingUnits.load())
              .forAnnotation(ch.annotationAsset, annotation);
      final displayUnitsByStart = {
        for (final unit in displayUnits) unit.firstSegment: unit,
      };
      var segmentIndex = 0;
      while (segmentIndex < annotation.segments.length) {
        final unit = displayUnitsByStart[segmentIndex];
        final segment = unit?.segment ?? annotation.segments[segmentIndex];
        final start = offset;
        final end = start + segment.text.length;
        if (segment.type == 'punctuation' &&
            segment.text.contains('\n') &&
            segment.text.trim().isEmpty) {
          finishParagraph();
          offset = end;
          segmentIndex++;
          continue;
        }
        final tappable = segment.type != 'punctuation';
        final grammar = widget.reader.language == Language.chinese
            ? chineseGrammarForSpan(
                start: start,
                end: end,
                overlays: annotation.grammarOverlays,
                readingUnitGrammar: unit?.grammar,
              )
            : unit != null
                ? [unit.grammar]
                : japaneseGrammarForSegment(
                    segment: segment,
                    start: start,
                    end: end,
                    overlays: annotation.grammarOverlays,
                  );
        paragraphTokens.add(_TokenEntry(
          text: segment.text,
          isCjk: segment.text.isNotEmpty && _isCJK(segment.text.codeUnitAt(0)),
          globalIndex: tappable ? words.length : -1,
          startOffset: start,
          endOffset: end,
        ));
        if (tappable) {
          final nestedAgentTargets = <_TappedWord>[];
          if (unit != null) {
            var nestedStart = start;
            for (var i = unit.firstSegment; i <= unit.lastSegment; i++) {
              final original = annotation.segments[i];
              final nestedEnd = nestedStart + original.text.length;
              if (original.type != 'punctuation') {
                nestedAgentTargets.add(_TappedWord(
                  usageSource: ch.annotationAsset,
                  usageSourceText: ch.content,
                  usageSegmentIndex: i,
                  usageStartOffset: nestedStart,
                  surface: original.text,
                  agent: original,
                  grammar: widget.reader.language == Language.chinese
                      ? chineseGrammarForSpan(
                          start: nestedStart,
                          end: nestedEnd,
                          overlays: annotation.grammarOverlays,
                        )
                      : japaneseGrammarForSegment(
                          segment: original,
                          start: nestedStart,
                          end: nestedEnd,
                          overlays: annotation.grammarOverlays,
                        ),
                ));
              }
              nestedStart = nestedEnd;
            }
          }
          words.add(_TappedWord(
            usageStartOffset: start,
            usageSource: ch.annotationAsset,
            usageSourceText: ch.content,
            usageSegmentIndex: segmentIndex,
            surface: segment.text,
            agent: segment,
            grammar: grammar,
            nestedAgentTargets: nestedAgentTargets,
          ));
        }
        paragraphText.write(segment.text);
        offset = end;
        segmentIndex = unit == null ? segmentIndex + 1 : unit.lastSegment + 1;
      }
      finishParagraph();
      final result = _ChapterSegmented(
        index: chapterIndex,
        title: ch.title,
        paragraphs: paragraphs,
        allWords: words,
      );
      _segmentCache[chapterIndex] = result;
      return result;
    }

    // Canonical graded content never falls back to a legacy tokenizer. Until
    // its reviewed agent sidecar exists, render plain non-interactive prose so
    // a tap cannot surface stale segmentation or explanations.
    if (widget.reader.language == Language.chinese ||
        widget.reader.language == Language.japanese) {
      var paragraphOffset = 0;
      final paragraphs = ch.content
          .split(RegExp(r'\n\s*\n'))
          .where((text) => text.trim().isNotEmpty)
          .map((text) {
        final start = ch.content.indexOf(text, paragraphOffset);
        final end = start + text.length;
        paragraphOffset = end;
        return _ParagraphData(
          raw: text,
          plainText: text,
          isHeading: false,
          tokens: [
            _TokenEntry(
              text: text,
              isCjk: true,
              globalIndex: -1,
              startOffset: start,
              endOffset: end,
            ),
          ],
        );
      }).toList();
      final result = _ChapterSegmented(
        index: chapterIndex,
        title: ch.title,
        paragraphs: paragraphs,
        allWords: const [],
      );
      _segmentCache[chapterIndex] = result;
      return result;
    }

    // Split content into blocks, handling markdown patterns
    final paragraphTexts = <String>[];
    for (final para in ch.content.split('\n\n')) {
      final trimmed = para.trim();
      if (trimmed.isNotEmpty) paragraphTexts.add(trimmed);
    }

    // Segment on main thread, yielding between paragraphs for UI responsiveness
    final paragraphs = <_ParagraphData>[];
    final allCjk = <_TappedWord>[];

    for (int pi = 0; pi < paragraphTexts.length; pi++) {
      // Yield every few paragraphs to let the spinner animate
      if (pi % 3 == 0) await Future.delayed(Duration.zero);

      final raw = paragraphTexts[pi];

      // Detect block type from markdown syntax
      _BlockType blockType = _BlockType.paragraph;
      int headingLevel = 0;
      String textToSegment = raw;
      List<List<String>>? tableRows;

      if (raw == '---' || raw == '***' || raw == '___') {
        blockType = _BlockType.divider;
        textToSegment = '';
      } else if (raw.startsWith('### ')) {
        blockType = _BlockType.subheading;
        headingLevel = 2;
        textToSegment = raw.substring(4);
      } else if (raw.startsWith('## ')) {
        blockType = _BlockType.heading;
        headingLevel = 1;
        textToSegment = raw.substring(3);
      } else if (raw.startsWith('> ')) {
        blockType = _BlockType.blockquote;
        textToSegment = raw.replaceAll(RegExp(r'^> ?', multiLine: true), '');
      } else if (raw.startsWith('|') && raw.contains('|')) {
        blockType = _BlockType.table;
        tableRows = _parseMarkdownTable(raw);
        textToSegment = tableRows.map((row) => row.join(' ')).join(' ');
      } else if (raw.startsWith('**') &&
          raw.endsWith('**') &&
          !raw.substring(2, raw.length - 2).contains('**')) {
        blockType = _BlockType.heading;
        headingLevel = 1;
        textToSegment = raw.substring(2, raw.length - 2);
      }

      // Strip inline bold markers for segmentation
      final cleanText = textToSegment.replaceAll('**', '');

      final tokens =
          cleanText.isEmpty ? <String>[] : segmentText(cleanText, dict);
      final isHeading =
          blockType == _BlockType.heading || blockType == _BlockType.subheading;

      final tokenEntries = <_TokenEntry>[];
      int charOffset = 0;
      for (final t in tokens) {
        final isCjk = t.isNotEmpty && _isCJK(t.codeUnitAt(0));
        final tappable = isCjk && _isTappableWord(t);
        tokenEntries.add(_TokenEntry(
          text: t,
          isCjk: isCjk,
          globalIndex: tappable ? allCjk.length : -1,
          startOffset: charOffset,
          endOffset: charOffset + t.length,
        ));
        charOffset += t.length;
        if (tappable) allCjk.add(_TappedWord(surface: t));
      }

      paragraphs.add(_ParagraphData(
        raw: raw,
        plainText: tokens.join(),
        isHeading: isHeading,
        blockType: blockType,
        headingLevel: headingLevel,
        tokens: tokenEntries,
        tableRows: tableRows,
      ));
    }

    final result = _ChapterSegmented(
      index: chapterIndex,
      title: ch.title,
      paragraphs: paragraphs,
      allWords: allCjk,
    );
    _segmentCache[chapterIndex] = result;
    return result;
  }

  Future<void> _loadPreferences() async {
    final prefs = await SharedPreferences.getInstance();
    final savedProgress =
        await ProgressService.instance.getProgress(widget.reader.id);

    if (!mounted) return;

    final chapter = (widget.initialCharacterOffset != null
            ? widget.initialChapter
            : savedProgress?.chapter ?? widget.initialChapter)
        .clamp(0, widget.reader.chapters.length - 1);
    final scrollFraction = savedProgress?.scrollFraction ?? 0.0;

    _fontSize = prefs.getDouble('reader_font_size') ?? 20.0;
    _currentChapter = chapter;
    _scrollFractions[_currentChapter] = scrollFraction;

    // Segment initial chapter in isolate
    await _getSegmentedAsync(_currentChapter);

    if (!mounted) return;
    setState(() {
      _chapterController = PageController(initialPage: _currentChapter);
      _ready = true;
    });

    // Pre-segment adjacent chapters in background
    _preSegmentNearby(_currentChapter);
  }

  Future<void> _preSegmentNearby(int chapter) async {
    final total = widget.reader.chapters.length;
    for (final ci in [chapter - 1, chapter + 1]) {
      if (ci >= 0 && ci < total && !_segmentCache.containsKey(ci)) {
        if (!mounted) return;
        await _getSegmentedAsync(ci);
      }
    }
  }

  Future<void> _saveFontSize() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('reader_font_size', _fontSize);
  }

  void _saveProgress() {
    final fraction = _scrollFractions[_currentChapter] ?? 0.0;
    ProgressService.instance.saveProgress(ReadingProgress(
      readerId: widget.reader.id,
      bookTitle: widget.reader.bookTitle,
      bookTitleEn: widget.reader.bookTitleEn,
      levelLabel: widget.reader.levelLabel,
      chapter: _currentChapter,
      totalChapters: widget.reader.chapters.length,
      scrollFraction: fraction,
      lastRead: DateTime.now(),
    ));
  }

  void _onScrollFractionChanged(int chapter, double fraction) {
    _scrollFractions[chapter] = fraction;
    _progressSaveTimer?.cancel();
    _progressSaveTimer = Timer(
      const Duration(milliseconds: 400),
      _saveProgress,
    );
  }

  @override
  void dispose() {
    _progressSaveTimer?.cancel();
    if (_ready) {
      _saveProgress();
      _chapterController.dispose();
    }
    _highlightedIndex.dispose();
    super.dispose();
  }

  void _showWordDefinition(List<_TappedWord> allWords, int index) {
    _highlightedIndex.value = index;
    unawaited(showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) {
        if (allWords[index].agent != null) {
          return _AgentDefinitionSheet(
            allWords: allWords,
            initialIndex: index,
            highlightedIndex: _highlightedIndex,
            language: widget.reader.language,
            levelLabel: widget.reader.levelLabel,
          );
        }
        return _WordDefinitionSheet(
          allWords: allWords.map((item) => item.surface).toList(),
          initialIndex: index,
          highlightedIndex: _highlightedIndex,
        );
      },
    ));
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final levelColor =
        AppTheme.levelColor(widget.reader.level, widget.reader.language);
    final totalChapters = widget.reader.chapters.length;

    return Scaffold(
      appBar: AppBar(
        title: _ready
            ? Text(
                widget.reader.chapters[_currentChapter].title,
                style:
                    const TextStyle(fontSize: 14, fontWeight: FontWeight.w500),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              )
            : null,
        actions: [
          if (_ready)
            CopyTextButton(
              text: widget.reader.chapters[_currentChapter].content,
              confirmation: 'Chapter text copied',
              tooltip: 'Copy chapter text',
            ),
          IconButton(
            icon: const Icon(Icons.text_decrease, size: 20),
            tooltip: 'Decrease text size',
            onPressed: _fontSize > _minFontSize
                ? () {
                    setState(() => _fontSize -= 2);
                    _saveFontSize();
                  }
                : null,
          ),
          IconButton(
            icon: const Icon(Icons.text_increase, size: 20),
            tooltip: 'Increase text size',
            onPressed: _fontSize < _maxFontSize
                ? () {
                    setState(() => _fontSize += 2);
                    _saveFontSize();
                  }
                : null,
          ),
        ],
      ),
      body: !_ready
          ? const Center(child: CircularProgressIndicator())
          : PageView.builder(
              controller: _chapterController,
              itemCount: totalChapters,
              onPageChanged: (index) {
                _highlightedIndex.value = -1;
                _progressSaveTimer?.cancel();
                _saveProgress();
                setState(() => _currentChapter = index);
                _saveProgress();
                _preSegmentNearby(index);
              },
              itemBuilder: (context, index) {
                final chapter = _segmentCache[index];
                if (chapter == null) {
                  _getSegmentedAsync(index).then((_) {
                    if (mounted) setState(() {});
                  });
                  return const Center(child: CircularProgressIndicator());
                }
                return _ChapterView(
                  initialCharacterOffset: index == widget.initialChapter
                      ? widget.initialCharacterOffset
                      : null,
                  chapter: chapter,
                  fontSize: _fontSize,
                  isDark: isDark,
                  language: widget.reader.language,
                  readerLevel: widget.reader.level,
                  levelLabel: widget.reader.levelLabel,
                  onWordTap: _showWordDefinition,
                  highlightedIndex: _highlightedIndex,
                  initialScrollFraction: _scrollFractions[index] ?? 0.0,
                  onScrollFractionChanged: (f) =>
                      _onScrollFractionChanged(index, f),
                );
              },
            ),
      bottomNavigationBar: _ready
          ? Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              decoration: BoxDecoration(
                color: isDark ? Colors.grey[900] : Colors.white,
                border: Border(
                  top: BorderSide(
                    color: isDark ? Colors.grey[800]! : Colors.grey[200]!,
                  ),
                ),
              ),
              child: SafeArea(
                child: Row(
                  children: [
                    IconButton(
                      onPressed: _currentChapter > 0
                          ? () => _chapterController.previousPage(
                                duration: const Duration(milliseconds: 250),
                                curve: Curves.easeInOut,
                              )
                          : null,
                      icon: const Icon(Icons.chevron_left),
                    ),
                    const Spacer(),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 2),
                      decoration: BoxDecoration(
                        color: levelColor.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        'Ch. ${_currentChapter + 1}/$totalChapters',
                        style: TextStyle(
                          color: levelColor,
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    const Spacer(),
                    IconButton(
                      onPressed: _currentChapter < (totalChapters - 1)
                          ? () => _chapterController.nextPage(
                                duration: const Duration(milliseconds: 250),
                                curve: Curves.easeInOut,
                              )
                          : null,
                      icon: const Icon(Icons.chevron_right),
                    ),
                  ],
                ),
              ),
            )
          : null,
    );
  }
}

class _ChapterSegmented {
  final int index;
  final String title;
  final List<_ParagraphData> paragraphs;
  final List<_TappedWord> allWords;
  _ChapterSegmented({
    required this.index,
    required this.title,
    required this.paragraphs,
    required this.allWords,
  });

  bool get hasLookupOnly =>
      allWords.any((item) => item.agent?.isLookupOnly == true);

  bool get hasStoryTerms =>
      allWords.any((item) => item.agent?.isStoryTerm == true);
}

// ---------------------------------------------------------------------------
// Chapter view (scrollable content for a single chapter)
// ---------------------------------------------------------------------------

class _ChapterView extends StatefulWidget {
  final int? initialCharacterOffset;
  final _ChapterSegmented chapter;
  final double fontSize;
  final bool isDark;
  final Language language;
  final int readerLevel;
  final String levelLabel;
  final void Function(List<_TappedWord>, int) onWordTap;
  final ValueNotifier<int> highlightedIndex;
  final double initialScrollFraction;
  final ValueChanged<double> onScrollFractionChanged;

  const _ChapterView({
    this.initialCharacterOffset,
    required this.chapter,
    required this.fontSize,
    required this.isDark,
    required this.language,
    required this.readerLevel,
    required this.levelLabel,
    required this.onWordTap,
    required this.highlightedIndex,
    required this.initialScrollFraction,
    required this.onScrollFractionChanged,
  });

  @override
  State<_ChapterView> createState() => _ChapterViewState();
}

class _ChapterViewState extends State<_ChapterView> {
  final _sourceParagraphKey = GlobalKey();
  final Map<int, TapGestureRecognizer> _recognizers = {};
  late ScrollController _scrollController;
  bool _restoredScroll = false;

  @override
  void initState() {
    super.initState();
    _scrollController = ScrollController();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scrollController.removeListener(_onScroll);
    _scrollController.dispose();
    _disposeRecognizers();
    super.dispose();
  }

  void _onScroll() {
    final max = _scrollController.position.maxScrollExtent;
    if (max > 0) {
      widget.onScrollFractionChanged(
          (_scrollController.offset / max).clamp(0.0, 1.0));
    }
  }

  void _restoreScroll() {
    if (_restoredScroll) return;
    final target = _sourceParagraphKey.currentContext;
    if (widget.initialCharacterOffset != null && target != null) {
      _restoredScroll = true;
      Scrollable.ensureVisible(target, alignment: 0.15);
      for (final paragraph in widget.chapter.paragraphs) {
        for (final token in paragraph.tokens) {
          if (token.startOffset <= widget.initialCharacterOffset! &&
              widget.initialCharacterOffset! < token.endOffset) {
            widget.highlightedIndex.value = token.globalIndex;
          }
        }
      }
      return;
    }
    _restoredScroll = true;
    if (widget.initialScrollFraction > 0 &&
        _scrollController.hasClients &&
        _scrollController.position.maxScrollExtent > 0) {
      _scrollController.jumpTo(
        widget.initialScrollFraction *
            _scrollController.position.maxScrollExtent,
      );
    }
  }

  void _disposeRecognizers() {
    for (final r in _recognizers.values) {
      r.dispose();
    }
    _recognizers.clear();
  }

  TapGestureRecognizer _recognizerFor(int index) {
    return _recognizers.putIfAbsent(
      index,
      () => TapGestureRecognizer()
        ..onTap = () => widget.onWordTap(widget.chapter.allWords, index),
    );
  }

  @override
  void didUpdateWidget(covariant _ChapterView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.chapter.index != widget.chapter.index) {
      _disposeRecognizers();
    }
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: widget.highlightedIndex,
      builder: (context, highlightIdx, _) {
        WidgetsBinding.instance.addPostFrameCallback((_) => _restoreScroll());
        return SelectionArea(
          child: ListView(
            controller: _scrollController,
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
            children: [
              ContentWidth(
                maxWidth: 760,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      widget.chapter.title,
                      style: _cjkTextStyle(
                        fontSize: widget.fontSize + 4,
                        language: widget.language,
                        fontWeight: FontWeight.bold,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Divider(
                      color:
                          widget.isDark ? Colors.grey[700] : Colors.grey[300],
                    ),
                    const SizedBox(height: 8),
                    if (widget.language == Language.chinese &&
                        widget.chapter.hasLookupOnly) ...[
                      Container(
                        margin: const EdgeInsets.only(bottom: 14),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 12, vertical: 10),
                        decoration: BoxDecoration(
                          color: AppTheme.levelColor(widget.readerLevel)
                              .withValues(alpha: widget.isDark ? 0.12 : 0.07),
                          borderRadius: BorderRadius.circular(10),
                          border: Border.all(
                            color: AppTheme.levelColor(widget.readerLevel)
                                .withValues(alpha: 0.2),
                          ),
                        ),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '词',
                              style: _cjkTextStyle(
                                fontSize: 15,
                                language: Language.chinese,
                                fontWeight: FontWeight.w600,
                              ).copyWith(
                                decoration: TextDecoration.underline,
                                decorationStyle: TextDecorationStyle.dotted,
                                decorationColor: Colors.grey[500],
                                decorationThickness: 1.5,
                              ),
                            ),
                            const SizedBox(width: 10),
                            Expanded(
                              child: Text(
                                'Dotted underline: optional lookup for ${widget.levelLabel}.'
                                '${widget.chapter.hasStoryTerms ? ' Warm highlight: important story vocabulary.' : ''}',
                                style:
                                    const TextStyle(fontSize: 12, height: 1.4),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    if (widget.chapter.allWords.isEmpty) ...[
                      Container(
                        margin: const EdgeInsets.only(bottom: 14),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 12, vertical: 10),
                        decoration: BoxDecoration(
                          color: AppTheme.primary.withValues(alpha: 0.08),
                          borderRadius: BorderRadius.circular(10),
                        ),
                        child: const Row(
                          children: [
                            Icon(Icons.hourglass_top_rounded,
                                size: 17, color: AppTheme.primary),
                            SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                'Reviewed agent annotations are not available for this chapter. '
                                'Legacy segmentation and explanations are disabled.',
                                style: TextStyle(fontSize: 12, height: 1.35),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                    ...widget.chapter.paragraphs.map((p) => Container(
                        key: widget.initialCharacterOffset != null &&
                                p.tokens.any((t) =>
                                    t.startOffset <=
                                        widget.initialCharacterOffset! &&
                                    widget.initialCharacterOffset! <
                                        t.endOffset)
                            ? _sourceParagraphKey
                            : null,
                        child: _buildParagraph(p, highlightIdx))),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildParagraph(_ParagraphData para, int highlightIdx) {
    // --- Divider ---
    if (para.blockType == _BlockType.divider) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Divider(
          color: widget.isDark ? Colors.grey[700] : Colors.grey[300],
        ),
      );
    }

    // --- Table ---
    if (para.blockType == _BlockType.table && para.tableRows != null) {
      return _buildTable(para.tableRows!);
    }

    // --- Heading / Subheading ---
    if (para.isHeading) {
      final fontSize =
          para.headingLevel <= 1 ? widget.fontSize : widget.fontSize - 2;
      return Padding(
        padding: EdgeInsets.only(
          top: para.headingLevel <= 1 ? 16 : 8,
          bottom: 8,
        ),
        child: _buildTappableRichText(
          para,
          highlightIdx,
          styleOverride: _cjkTextStyle(
            fontSize: fontSize,
            language: widget.language,
            fontWeight: FontWeight.bold,
            color: widget.isDark ? Colors.grey[300] : Colors.grey[700],
            height: 1.6,
          ),
        ),
      );
    }

    // --- Blockquote ---
    if (para.blockType == _BlockType.blockquote) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 16),
        child: Container(
          decoration: BoxDecoration(
            border: Border(
              left: BorderSide(
                color: widget.isDark ? Colors.grey[600]! : Colors.grey[400]!,
                width: 3,
              ),
            ),
          ),
          padding: const EdgeInsets.only(left: 12),
          child: _buildTappableRichText(
            para,
            highlightIdx,
            styleOverride: _cjkTextStyle(
              fontSize: widget.fontSize,
              language: widget.language,
              height: 1.8,
              color: widget.isDark ? Colors.grey[400] : Colors.grey[600],
            ),
          ),
        ),
      );
    }

    // --- Regular paragraph ---
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: _buildTappableRichText(para, highlightIdx),
    );
  }

  /// Build rich text with tappable CJK words, handling inline **bold** markers.
  Widget _buildTappableRichText(
    _ParagraphData para,
    int highlightIdx, {
    TextStyle? styleOverride,
  }) {
    final unshapedBaseStyle = styleOverride ??
        _cjkTextStyle(
          fontSize: widget.fontSize,
          language: widget.language,
          height: 1.8,
          color: widget.isDark ? Colors.grey[200] : AppTheme.textPrimary,
        );
    // CJK fonts give commas, stops, and quotation marks a full em by default.
    // In flowing reader prose that leaves a conspicuous empty half-cell around
    // punctuation, especially because annotation boundaries create many text
    // runs. Noto Sans CJK's proportional alternates keep punctuation attached
    // to the sentence while preserving full-width Han glyphs.
    final baseStyle = unshapedBaseStyle.copyWith(
      fontFeatures: const [FontFeature('palt')],
    );

    // Check if the raw text has inline bold markers (not a standalone bold heading)
    final hasBold =
        para.raw.contains('**') && para.blockType == _BlockType.paragraph;

    final spans = <TextSpan>[];
    // Track bold state by checking raw text positions
    // For simplicity, apply bold to the entire token-level span list
    // based on whether the raw text contains ** markers
    bool inBold = false;
    int rawPos = 0;

    for (final token in para.tokens) {
      // Advance rawPos through the original text to track ** markers
      if (hasBold) {
        // Find this token's text in raw starting from rawPos
        final searchText = para.raw;
        while (rawPos < searchText.length) {
          if (searchText.startsWith('**', rawPos)) {
            inBold = !inBold;
            rawPos += 2;
          } else {
            break;
          }
        }
        // Skip past this token in the raw text
        final tokenIdx = searchText.indexOf(token.text, rawPos);
        if (tokenIdx >= 0) {
          // Check for ** between rawPos and tokenIdx
          var scanPos = rawPos;
          while (scanPos < tokenIdx) {
            if (searchText.startsWith('**', scanPos)) {
              inBold = !inBold;
              scanPos += 2;
            } else {
              scanPos++;
            }
          }
          rawPos = tokenIdx + token.text.length;
        }
      }

      final tokenStyle =
          inBold ? baseStyle.copyWith(fontWeight: FontWeight.bold) : baseStyle;

      final tappedWord = token.globalIndex >= 0
          ? widget.chapter.allWords[token.globalIndex]
          : null;
      if (tappedWord != null &&
          (tappedWord.agent != null || DictionaryService.instance.isReady)) {
        final isHighlighted = token.globalIndex == highlightIdx;
        final isLookupOnly = tappedWord.agent?.isLookupOnly == true;
        final isStoryTerm = tappedWord.agent?.isStoryTerm == true;
        final recognizer = _recognizerFor(token.globalIndex);

        spans.add(TextSpan(
          text: token.text,
          style: tokenStyle.copyWith(
            backgroundColor: isHighlighted
                ? AppTheme.primary.withValues(alpha: 0.28)
                : isStoryTerm
                    ? const Color(0xFFFFB300).withValues(
                        alpha: widget.isDark ? 0.18 : 0.12,
                      )
                    : null,
            decoration:
                isLookupOnly ? TextDecoration.underline : tokenStyle.decoration,
            decorationStyle: isLookupOnly
                ? TextDecorationStyle.dotted
                : tokenStyle.decorationStyle,
            decorationColor: isLookupOnly
                ? (widget.isDark ? Colors.grey[500] : Colors.grey[600])
                : tokenStyle.decorationColor,
            decorationThickness: isLookupOnly ? 1.4 : null,
          ),
          recognizer: recognizer,
        ));
      } else {
        spans.add(TextSpan(text: token.text, style: tokenStyle));
      }
    }

    return Text.rich(
      TextSpan(children: spans),
    );
  }

  /// Build a table widget from parsed rows.
  Widget _buildTable(List<List<String>> rows) {
    if (rows.isEmpty) return const SizedBox.shrink();

    final headerStyle = _cjkTextStyle(
      fontSize: widget.fontSize - 2,
      language: widget.language,
      fontWeight: FontWeight.bold,
      color: widget.isDark ? Colors.grey[300] : Colors.grey[700],
      height: 1.5,
    );
    final cellStyle = _cjkTextStyle(
      fontSize: widget.fontSize - 2,
      language: widget.language,
      height: 1.5,
      color: widget.isDark ? Colors.grey[200] : AppTheme.textPrimary,
    );
    final borderColor = widget.isDark ? Colors.grey[700]! : Colors.grey[300]!;

    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Table(
        border: TableBorder.all(color: borderColor, width: 0.5),
        defaultVerticalAlignment: TableCellVerticalAlignment.middle,
        children: rows.asMap().entries.map((entry) {
          final isHeader = entry.key == 0;
          return TableRow(
            decoration: isHeader
                ? BoxDecoration(
                    color: widget.isDark ? Colors.grey[800] : Colors.grey[100],
                  )
                : null,
            children: entry.value.map((cell) {
              return Padding(
                padding: const EdgeInsets.all(8),
                child: Text(
                  cell,
                  style: isHeader ? headerStyle : cellStyle,
                ),
              );
            }).toList(),
          );
        }).toList(),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Word definition bottom sheet
// ---------------------------------------------------------------------------

class _CurriculumBadge {
  final int level;
  final String label;

  const _CurriculumBadge(this.level, this.label);
}

Widget _definitionSheetViewport(
  BuildContext context, {
  required Widget child,
}) {
  return ConstrainedBox(
    key: const ValueKey('definition-sheet-viewport'),
    constraints: BoxConstraints(
      // The Material bottom sheet's fixed drag handle occupies about 32 px
      // above this viewport. Keep the complete sheet close to half a screen.
      maxHeight: MediaQuery.sizeOf(context).height * 0.5 - 32,
    ),
    child: child,
  );
}

_CurriculumBadge? _curriculumBadge(
  DictEntry? entry,
  Language language,
  String surface,
) {
  if (entry == null) return null;
  if (language == Language.chinese) {
    final isSingleCharacter = surface.runes.length == 1;
    final level = isSingleCharacter
        ? entry.characterHskLevel ?? entry.hskLevel
        : entry.hskLevel;
    if (level == null) return null;
    return _CurriculumBadge(
      level,
      isSingleCharacter ? 'Character HSK $level' : 'HSK $level',
    );
  }

  final level = entry.hskLevel;
  if (level == null) return null;
  const jlpt = {1: 'N5', 2: 'N4', 3: 'N3', 4: 'N2', 5: 'N1'};
  return _CurriculumBadge(level, 'JLPT ${jlpt[level] ?? level}');
}

class _AgentDefinitionSheet extends StatefulWidget {
  final List<_TappedWord> allWords;
  final int initialIndex;
  final ValueNotifier<int> highlightedIndex;
  final Language language;
  final String levelLabel;

  const _AgentDefinitionSheet({
    required this.allWords,
    required this.initialIndex,
    required this.highlightedIndex,
    required this.language,
    required this.levelLabel,
  });

  @override
  State<_AgentDefinitionSheet> createState() => _AgentDefinitionSheetState();
}

class _AgentDefinitionSheetState extends State<_AgentDefinitionSheet> {
  late int _currentIndex;
  bool _saved = false;
  bool _loading = false;

  _TappedWord get _word => widget.allWords[_currentIndex];
  AgentSegment get _agent => _word.agent!;

  void _openNestedLookup(
    String word, {
    bool exactCanonical = false,
    String preferredDefinition = '',
  }) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SingleWordSheet(
        word: word,
        exactCanonical: exactCanonical,
        preferredDefinition: preferredDefinition,
      ),
    );
  }

  void _openNestedAgentTarget(_TappedWord target) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _AgentDefinitionSheet(
        allWords: [target],
        initialIndex: 0,
        highlightedIndex: widget.highlightedIndex,
        language: widget.language,
        levelLabel: widget.levelLabel,
      ),
    );
  }

  Widget _buildSubsegmentRow(AgentSubsegment part, bool isDark) {
    final entry = DictionaryService.instance.lookup(part.text);
    final etymology = part.text.length == 1
        ? EtymologyService.instance.lookup(part.text)
        : null;
    final reading = _displayDictionaryReading(
      entry?.pinyin ?? etymology?.mandarinReading ?? '',
      Language.chinese,
    );
    final definition =
        part.meaningEn.isNotEmpty ? part.meaningEn : 'Open dictionary entry';

    return Semantics(
      button: true,
      label: 'Open dictionary entry for ${part.text}',
      child: InkWell(
        key: ValueKey('agent-subsegment-${_word.surface}-${part.start}'),
        onTap: () => _openNestedLookup(part.text),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 11),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 60,
                child: Text(
                  part.text,
                  style: _cjkTextStyle(
                    fontSize: 20,
                    language: Language.chinese,
                    fontWeight: FontWeight.w600,
                    color: AppTheme.primary,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (reading.isNotEmpty)
                      Text(
                        reading,
                        style: const TextStyle(
                          fontSize: 13,
                          color: AppTheme.primary,
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    Text(
                      definition,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 13,
                        height: 1.35,
                        color: isDark ? Colors.grey[300] : Colors.grey[700],
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              const Icon(Icons.chevron_right, size: 20),
            ],
          ),
        ),
      ),
    );
  }

  String get _readingCue {
    final targetLabel = widget.levelLabel;
    switch (_agent.lookupReason) {
      case 'proper_name':
        return 'Story name · optional for $targetLabel';
      case 'story_term':
        return 'Story vocabulary · optional for $targetLabel';
      default:
        return 'Extra vocabulary · optional for $targetLabel';
    }
  }

  bool get _hasJapaneseChain =>
      _agent.formSteps.isNotEmpty ||
      (_word.nestedAgentTargets.length > 1 &&
          _word.nestedAgentTargets.last.agent!.formSteps.isNotEmpty);

  Widget _buildJapaneseFormSummary(Color? muted) {
    final details = <String>[
      if (_agent.partOfSpeech.isNotEmpty) _agent.partOfSpeech,
      if (!_hasJapaneseChain &&
          _agent.conjugationForm.isNotEmpty &&
          _agent.conjugationForm != 'non-inflecting')
        _agent.conjugationForm,
    ];
    if (!_hasJapaneseChain && details.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(top: 9),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_hasJapaneseChain)
            JapaneseFormChain(
              segment: _agent,
              source: _word.usageSource,
              sourceText: _word.usageSourceText,
              segmentIndex: _word.usageSegmentIndex,
              startOffset: _word.usageStartOffset ?? -1,
              includeSurfaceMeaning: true,
              parts: _word.nestedAgentTargets.length > 1
                  ? _word.nestedAgentTargets
                      .map((part) => (
                            segment: part.agent!,
                            index: part.usageSegmentIndex,
                            start: part.usageStartOffset ?? -1
                          ))
                      .toList()
                  : const [],
              overlay: _word.grammar.isEmpty ? null : _word.grammar.last,
            ),
          if (details.isNotEmpty)
            Text(details.join(' · '),
                style: TextStyle(fontSize: 12.5, color: muted)),
        ],
      ),
    );
  }

  @override
  void initState() {
    super.initState();
    _currentIndex = widget.initialIndex;
    _saved = VocabularyService.instance.isSaved(_word.surface);
  }

  void _goTo(int index) {
    setState(() {
      _currentIndex = index;
      _saved = VocabularyService.instance.isSaved(_word.surface);
    });
    widget.highlightedIndex.value = index;
  }

  Future<void> _toggleSave() async {
    if (_loading) return;
    setState(() => _loading = true);
    if (_saved) {
      await VocabularyService.instance.removeWord(_word.surface);
    } else {
      await VocabularyService.instance.saveWord(SavedWord(
        word: _word.surface,
        pinyin: _agent.pinyin,
        definitions: [_agent.meaningEn],
        savedAt: DateTime.now(),
      ));
    }
    if (!mounted) return;
    setState(() {
      _saved = !_saved;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final muted = isDark ? Colors.grey[300] : Colors.grey[700];
    return _definitionSheetViewport(context,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 20),
          child: SelectionArea(
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    key: const ValueKey('definition-sheet-scrolling-header'),
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Semantics(
                              label:
                                  '${_word.surface}. Tap a character for its entry.',
                              child: _buildPlainWord(
                                word: _word.surface,
                                language: widget.language,
                                fontSize: 32,
                                onCharTap: _openNestedLookup,
                              ),
                            ),
                            if (_agent.pinyin.isNotEmpty &&
                                widget.language == Language.japanese)
                              JapaneseSegmentText(
                                  segment: _agent,
                                  source: _word.usageSource,
                                  sourceText: _word.usageSourceText,
                                  segmentIndex: _word.usageSegmentIndex,
                                  startOffset: _word.usageStartOffset ?? -1,
                                  reading: true,
                                  style: const TextStyle(
                                      fontSize: 17,
                                      color: AppTheme.primary,
                                      fontStyle: FontStyle.italic))
                            else if (_agent.pinyin.isNotEmpty)
                              Text(
                                _agent.pinyin,
                                style: const TextStyle(
                                  fontSize: 17,
                                  color: AppTheme.primary,
                                  fontStyle: FontStyle.italic,
                                ),
                              ),
                          ],
                        ),
                      ),
                      IconButton(
                        onPressed: _toggleSave,
                        icon: _loading
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child:
                                    CircularProgressIndicator(strokeWidth: 2),
                              )
                            : Icon(
                                _saved
                                    ? Icons.bookmark_rounded
                                    : Icons.bookmark_outline_rounded,
                                color: _saved ? AppTheme.primary : null,
                              ),
                        tooltip: _saved
                            ? 'Remove from vocabulary'
                            : 'Save to vocabulary',
                        visualDensity: VisualDensity.compact,
                      ),
                      CopyTextButton(
                        text: _word.surface,
                        confirmation: 'Word copied',
                        tooltip: 'Copy word',
                        visualDensity: VisualDensity.compact,
                      ),
                    ],
                  ),
                  if (_agent.isLookupOnly) ...[
                    const SizedBox(height: 2),
                    Text.rich(
                      key: const ValueKey('definition-sheet-reading-cue'),
                      TextSpan(
                        style: TextStyle(
                            fontSize: 12.5,
                            height: 1.3,
                            color: Colors.grey[500]),
                        children: [
                          WidgetSpan(
                            alignment: PlaceholderAlignment.middle,
                            child: Padding(
                              padding: const EdgeInsets.only(right: 5),
                              child: Icon(Icons.visibility_outlined,
                                  size: 14, color: Colors.grey[500]),
                            ),
                          ),
                          TextSpan(text: _readingCue),
                        ],
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  Divider(
                    height: 1,
                    color: isDark ? Colors.grey[800] : Colors.grey[200],
                  ),
                  const SizedBox(height: 12),
                  if (widget.language == Language.japanese &&
                      !_hasJapaneseChain)
                    JapaneseSegmentText(
                        segment: _agent,
                        source: _word.usageSource,
                        sourceText: _word.usageSourceText,
                        segmentIndex: _word.usageSegmentIndex,
                        startOffset: _word.usageStartOffset ?? -1,
                        style:
                            TextStyle(fontSize: 15, height: 1.45, color: muted))
                  else if (widget.language != Language.japanese)
                    Text(
                      _agent.meaningEn,
                      style:
                          TextStyle(fontSize: 15, height: 1.45, color: muted),
                    ),
                  if (_word.usageSource.isNotEmpty &&
                      (widget.language != Language.japanese ||
                          !_hasJapaneseChain))
                    UsageDictionaryLink(
                        language: widget.language,
                        startOffset: _word.usageStartOffset,
                        surface: _agent.text,
                        reading: _agent.pinyin,
                        gloss: _agent.meaningEn,
                        source: _word.usageSource,
                        segmentIndex: _word.usageSegmentIndex,
                        sourceText: _word.usageSourceText),
                  if (widget.language == Language.japanese &&
                      !_hasJapaneseChain)
                    GrammarDictionaryLinks(
                        source: _word.usageSource,
                        sourceText: _word.usageSourceText,
                        surface: _agent.text,
                        startOffset: _word.usageStartOffset ?? -1),
                  if (widget.language == Language.japanese)
                    _buildJapaneseFormSummary(muted),
                  if (widget.language == Language.japanese &&
                      _word.grammar.isNotEmpty)
                    JapaneseGrammarContext(
                        source: _word.usageSource,
                        sourceText: _word.usageSourceText,
                        overlays: _word.grammar),
                  if (_agent.storyTermMeaningEn.isNotEmpty &&
                      _agent.storyTermMeaningEn != _agent.meaningEn) ...[
                    const SizedBox(height: 8),
                    Text(
                      _agent.storyTermMeaningEn,
                      style: TextStyle(fontSize: 14, height: 1.4, color: muted),
                    ),
                  ],
                  if (_agent.storyTermImportanceEn.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(
                      _agent.storyTermImportanceEn,
                      style: TextStyle(fontSize: 12, height: 1.4, color: muted),
                    ),
                  ],
                  if (widget.language == Language.chinese &&
                      _agent.hasSubsegments) ...[
                    const SizedBox(height: 14),
                    Text(
                      'How it is built',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        color: Colors.grey[500],
                        letterSpacing: 0.3,
                      ),
                    ),
                    const SizedBox(height: 5),
                    Text(
                      _agent.compositionEn,
                      style: TextStyle(fontSize: 14, height: 1.4, color: muted),
                    ),
                    const SizedBox(height: 7),
                    Column(
                      children:
                          _agent.subsegments.asMap().entries.expand((entry) {
                        final widgets = <Widget>[
                          _buildSubsegmentRow(entry.value, isDark),
                        ];
                        if (entry.key < _agent.subsegments.length - 1) {
                          widgets.add(Divider(
                            height: 1,
                            color: isDark ? Colors.grey[800] : Colors.grey[200],
                          ));
                        }
                        return widgets;
                      }).toList(),
                    ),
                  ],
                  if (_word.grammar.isNotEmpty &&
                      widget.language != Language.japanese) ...[
                    const SizedBox(height: 12),
                    Text(
                      'Grammar',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        color: Colors.grey[500],
                      ),
                    ),
                    const SizedBox(height: 4),
                    ..._word.grammar.map((item) => Padding(
                          padding: const EdgeInsets.only(bottom: 6),
                          child: Text(
                            '${item.pattern} — ${item.meaningEn}',
                            style: TextStyle(
                                fontSize: 14, height: 1.4, color: muted),
                          ),
                        )),
                  ],
                  if (widget.language == Language.chinese &&
                      _word.nestedAgentTargets.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    const Text('Components'),
                    for (final part in _word.nestedAgentTargets)
                      ListTile(
                        key: ValueKey(
                            'reading-unit-component-${part.usageSegmentIndex}'),
                        title: Text('${part.surface}  ${part.agent!.pinyin}'),
                        subtitle: Text(part.agent!.meaningEn),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => _openNestedAgentTarget(part),
                      ),
                  ],
                  if (widget.allWords.length > 1) ...[
                    const SizedBox(height: 12),
                    Divider(
                        color: isDark ? Colors.grey[700] : Colors.grey[200]),
                    Row(
                      children: [
                        IconButton(
                          onPressed: _currentIndex > 0
                              ? () => _goTo(_currentIndex - 1)
                              : null,
                          icon: const Icon(Icons.chevron_left),
                          tooltip: _currentIndex > 0
                              ? widget.allWords[_currentIndex - 1].surface
                              : null,
                        ),
                        Expanded(
                          child: Text(
                            '${_currentIndex + 1} / ${widget.allWords.length}',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: Colors.grey[500]),
                          ),
                        ),
                        IconButton(
                          onPressed: _currentIndex < widget.allWords.length - 1
                              ? () => _goTo(_currentIndex + 1)
                              : null,
                          icon: const Icon(Icons.chevron_right),
                          tooltip: _currentIndex < widget.allWords.length - 1
                              ? widget.allWords[_currentIndex + 1].surface
                              : null,
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ));
  }
}

class _WordDefinitionSheet extends StatefulWidget {
  final List<String> allWords;
  final int initialIndex;
  final ValueNotifier<int> highlightedIndex;

  const _WordDefinitionSheet({
    required this.allWords,
    required this.initialIndex,
    required this.highlightedIndex,
  });

  @override
  State<_WordDefinitionSheet> createState() => _WordDefinitionSheetState();
}

class _WordDefinitionSheetState extends State<_WordDefinitionSheet> {
  late int _currentIndex;
  bool _saved = false;
  bool _loading = false;
  DictEntry? _entry;

  String get _word => widget.allWords[_currentIndex];
  bool get _hasPrev => _currentIndex > 0;
  bool get _hasNext => _currentIndex < widget.allWords.length - 1;

  @override
  void initState() {
    super.initState();
    _currentIndex = widget.initialIndex;
    _loadWord();
  }

  void _loadWord() {
    _entry = DictionaryService.instance.lookup(_word);
    _saved = VocabularyService.instance.isSaved(_word);
  }

  void _goTo(int index) {
    setState(() {
      _currentIndex = index;
      _loading = false;
      _loadWord();
    });
    widget.highlightedIndex.value = _currentIndex;
  }

  void _openNestedLookup(String word) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SingleWordSheet(word: word),
    );
  }

  List<Widget> _buildCharBreakdown(String word, bool isDark) {
    final dict = DictionaryService.instance;
    final widgets = <Widget>[];
    for (int i = 0; i < word.length; i++) {
      final ch = word[i];
      final charEntry = dict.lookup(ch);
      final etym = EtymologyService.instance.lookup(ch);
      widgets.add(Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: GestureDetector(
          onTap: () => _openNestedLookup(ch),
          behavior: HitTestBehavior.opaque,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                ch,
                style: _cjkTextStyle(
                  fontSize: 18,
                  language: DictionaryService.instance.activeLanguage,
                  fontWeight: FontWeight.bold,
                  color: AppTheme.primary,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: (charEntry == null && etym == null)
                    ? Text('—',
                        style: TextStyle(color: Colors.grey[400], fontSize: 14))
                    : Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (charEntry != null && charEntry.pinyin.isNotEmpty)
                            Text(
                                _displayDictionaryReading(
                                  charEntry.pinyin,
                                  DictionaryService.instance.activeLanguage,
                                ),
                                style: TextStyle(
                                    fontSize: 13,
                                    color: AppTheme.primary,
                                    fontStyle: FontStyle.italic)),
                          if (charEntry != null &&
                              charEntry.definitions.isNotEmpty)
                            Text(
                              charEntry.definitions.first,
                              style: TextStyle(
                                fontSize: 13,
                                color: isDark
                                    ? Colors.grey[300]
                                    : Colors.grey[700],
                              ),
                            ),
                          if (etym != null && etym.notes.isNotEmpty)
                            Text(
                              etym.notes.first.text,
                              style: TextStyle(
                                fontSize: 12,
                                color: Colors.grey[500],
                                fontStyle: FontStyle.italic,
                              ),
                            ),
                        ],
                      ),
              ),
            ],
          ),
        ),
      ));
    }
    return widgets;
  }

  Widget _buildSubwordFurigana({
    required List<String> subwords,
    required Language language,
    required double fontSize,
  }) {
    final dict = DictionaryService.instance;
    return Wrap(
      children: subwords.map((sw) {
        final swEntry = dict.lookup(sw);
        final reading = _displayDictionaryReading(
          swEntry?.pinyin ?? '',
          language,
        );
        return GestureDetector(
          onTap: () => _openNestedLookup(sw),
          child: reading.isNotEmpty
              ? _buildFurigana(
                  word: sw,
                  reading: reading,
                  language: language,
                  fontSize: fontSize,
                )
              : Text(
                  sw,
                  style: _cjkTextStyle(
                    fontSize: fontSize,
                    language: language,
                    fontWeight: FontWeight.bold,
                    height: 1.2,
                  ),
                ),
        );
      }).toList(),
    );
  }

  Widget _buildSubwordRow({
    required List<String> subwords,
    required Language language,
    required double fontSize,
  }) {
    return Wrap(
      children: subwords.map((sw) {
        return GestureDetector(
          onTap: () => _openNestedLookup(sw),
          child: Text(
            sw,
            style: _cjkTextStyle(
              fontSize: fontSize,
              language: language,
              fontWeight: FontWeight.bold,
              height: 1.2,
            ),
          ),
        );
      }).toList(),
    );
  }

  List<Widget> _buildEtymSection(BuildContext ctx, String ch, bool isDark) {
    final etymWidgets = _buildEtymologyWidgets(ctx, ch, isDark,
        onComponentTap: _openNestedLookup);
    if (etymWidgets.isEmpty) return [];
    return [
      const SizedBox(height: 8),
      Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
      ...etymWidgets,
    ];
  }

  Widget _buildWordWithReading(DictEntry? entry) {
    final lang = DictionaryService.instance.activeLanguage;
    final reading = _displayDictionaryReading(entry?.pinyin ?? '', lang);
    final dictForm = entry?.word ?? _word;
    final isInflected = dictForm != _word;

    // For multi-kanji compounds, tap opens sub-words; otherwise single chars
    final subwords = _segmentCompound(dictForm);
    final charTap = dictForm.length > 1 ? _openNestedLookup : null;

    // Build deinflection chain for educational display
    final chain = isInflected && lang == Language.japanese
        ? deinflectionChain(_word, dictForm)
        : <String>[];

    // For Japanese: show dictionary form with furigana, chain below
    if (lang == Language.japanese && reading.isNotEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (subwords != null)
            _buildSubwordFurigana(
              subwords: subwords,
              language: lang,
              fontSize: 32,
            )
          else
            _buildFurigana(
              word: dictForm,
              reading: reading,
              language: lang,
              fontSize: 32,
              onCharTap: charTap,
            ),
          if (chain.isNotEmpty) ...[
            const SizedBox(height: 6),
            ...chain.map((form) => Padding(
                  padding: const EdgeInsets.only(top: 1),
                  child: Text(
                    form,
                    style: _cjkTextStyle(
                      fontSize: 14,
                      language: lang,
                      color: Colors.grey[500],
                    ),
                  ),
                )),
          ],
        ],
      );
    }

    // For Chinese or no reading: show word + pinyin below
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (subwords != null)
          _buildSubwordRow(subwords: subwords, language: lang, fontSize: 32)
        else
          _buildPlainWord(
            word: dictForm,
            language: lang,
            fontSize: 32,
            onCharTap: charTap,
          ),
        if (reading.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(
            reading,
            style: TextStyle(
              fontSize: 17,
              color: AppTheme.primary,
              fontStyle: FontStyle.italic,
            ),
          ),
        ],
        if (chain.isNotEmpty) ...[
          const SizedBox(height: 6),
          ...chain.map((form) => Padding(
                padding: const EdgeInsets.only(top: 1),
                child: Text(
                  form,
                  style: TextStyle(fontSize: 14, color: Colors.grey[500]),
                ),
              )),
        ] else if (isInflected) ...[
          const SizedBox(height: 4),
          Text(
            _word,
            style: TextStyle(fontSize: 15, color: Colors.grey[500]),
          ),
        ],
      ],
    );
  }

  Future<void> _toggleSave() async {
    if (_loading) return;
    setState(() => _loading = true);
    final vocab = VocabularyService.instance;
    if (_saved) {
      await vocab.removeWord(_word);
    } else {
      await vocab.saveWord(SavedWord(
        word: _word,
        pinyin: _entry?.pinyin ?? '',
        definitions: _entry?.definitions ?? [],
        savedAt: DateTime.now(),
      ));
    }
    if (!mounted) return;
    setState(() {
      _saved = !_saved;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final entry = _entry;

    return _definitionSheetViewport(context,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
          child: SelectionArea(
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _buildWordWithReading(entry),
                          ],
                        ),
                      ),
                      if (_curriculumBadge(
                            entry,
                            DictionaryService.instance.activeLanguage,
                            _word,
                          ) !=
                          null)
                        Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: Builder(builder: (context) {
                            final lang =
                                DictionaryService.instance.activeLanguage;
                            final badge = _curriculumBadge(entry, lang, _word)!;
                            return Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 3),
                              decoration: BoxDecoration(
                                color: AppTheme.levelColor(badge.level, lang),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: Text(
                                badge.label,
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 11,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            );
                          }),
                        ),
                      CopyTextButton(
                        text: _word,
                        confirmation: 'Word copied',
                        tooltip: 'Copy word',
                        visualDensity: VisualDensity.compact,
                      ),
                      IconButton(
                        onPressed: _toggleSave,
                        icon: _loading
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child:
                                    CircularProgressIndicator(strokeWidth: 2),
                              )
                            : Icon(
                                _saved
                                    ? Icons.bookmark_rounded
                                    : Icons.bookmark_outline_rounded,
                                color: _saved ? AppTheme.primary : null,
                                size: 24,
                              ),
                        tooltip: _saved
                            ? 'Remove from vocabulary'
                            : 'Save to vocabulary',
                        visualDensity: VisualDensity.compact,
                      ),
                    ],
                  ),

                  if (entry != null && entry.hasDefinitions) ...[
                    const SizedBox(height: 12),
                    Divider(
                        color: isDark ? Colors.grey[700] : Colors.grey[200]),
                    const SizedBox(height: 8),
                    ...entry.definitions.asMap().entries.map((e) => Padding(
                          padding: const EdgeInsets.only(bottom: 4),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              SizedBox(
                                width: 24,
                                child: Text(
                                  '${e.key + 1}.',
                                  style: TextStyle(
                                    fontSize: 14,
                                    color: Colors.grey[500],
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                              Expanded(
                                child: Text(
                                  e.value,
                                  style: TextStyle(
                                    fontSize: 14,
                                    height: 1.4,
                                    color: isDark
                                        ? Colors.grey[300]
                                        : Colors.grey[800],
                                  ),
                                ),
                              ),
                            ],
                          ),
                        )),
                  ] else ...[
                    const SizedBox(height: 12),
                    if (entry == null && _word.length > 1) ...[
                      Divider(
                          color: isDark ? Colors.grey[700] : Colors.grey[200]),
                      const SizedBox(height: 8),
                      Text(
                        'Word not in dictionary. Character breakdown:',
                        style: TextStyle(fontSize: 12, color: Colors.grey[500]),
                      ),
                      const SizedBox(height: 8),
                      ..._buildCharBreakdown(_word, isDark),
                    ] else
                      Text(
                        entry == null
                            ? 'No dictionary entry found'
                            : 'No definitions available',
                        style: TextStyle(
                          fontSize: 13,
                          color: Colors.grey[500],
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                  ],

                  // Etymology section (for single characters)
                  if (_word.length == 1) ...[
                    ..._buildEtymSection(context, _word, isDark),
                  ],

                  // Prev / Next word navigation
                  if (widget.allWords.length > 1) ...[
                    const SizedBox(height: 8),
                    Divider(
                        color: isDark ? Colors.grey[700] : Colors.grey[200]),
                    Row(
                      children: [
                        Expanded(
                          child: GestureDetector(
                            onTap: _hasPrev
                                ? () => _goTo(_currentIndex - 1)
                                : null,
                            behavior: HitTestBehavior.opaque,
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.start,
                              children: [
                                Icon(Icons.chevron_left,
                                    size: 22,
                                    color: _hasPrev ? null : Colors.grey[400]),
                                if (_hasPrev)
                                  Flexible(
                                    child: Text(
                                      widget.allWords[_currentIndex - 1],
                                      style: TextStyle(
                                        fontSize: 15,
                                        color: isDark
                                            ? Colors.grey[300]
                                            : Colors.grey[700],
                                      ),
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                  ),
                              ],
                            ),
                          ),
                        ),
                        Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 12),
                          child: Text(
                            '${_currentIndex + 1} / ${widget.allWords.length}',
                            style: TextStyle(
                                fontSize: 11, color: Colors.grey[500]),
                          ),
                        ),
                        Expanded(
                          child: GestureDetector(
                            onTap: _hasNext
                                ? () => _goTo(_currentIndex + 1)
                                : null,
                            behavior: HitTestBehavior.opaque,
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.end,
                              children: [
                                if (_hasNext)
                                  Flexible(
                                    child: Text(
                                      widget.allWords[_currentIndex + 1],
                                      style: TextStyle(
                                        fontSize: 15,
                                        color: isDark
                                            ? Colors.grey[300]
                                            : Colors.grey[700],
                                      ),
                                      overflow: TextOverflow.ellipsis,
                                      textAlign: TextAlign.right,
                                    ),
                                  ),
                                Icon(Icons.chevron_right,
                                    size: 22,
                                    color: _hasNext ? null : Colors.grey[400]),
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ));
  }
}

// ---------------------------------------------------------------------------
// Simple single-word lookup sheet (for recursive lookups)
// ---------------------------------------------------------------------------

/// Open the existing character dictionary/etymology view from a structured link.
Future<void> showDictionaryCharacter(BuildContext context, String character,
    {Language language = Language.chinese}) async {
  if (character.runes.length != 1) return;
  await DictionaryService.instance.initialize(language: language);
  if (!context.mounted) return;
  if (language == Language.japanese) {
    final entry = DictionaryService.instance.lookupExactCanonical(character);
    await showModalBottomSheet<void>(
      context: context,
      useSafeArea: true,
      showDragHandle: true,
      builder: (_) => Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(character,
                  style: Theme.of(context).textTheme.headlineMedium),
              const SizedBox(height: 8),
              const Text('Japanese dictionary lookup'),
              const SizedBox(height: 8),
              if (entry != null) ...[
                Text(entry.pinyin),
                for (final definition in entry.definitions) Text(definition),
              ] else
                const Text(
                    'No standalone entry in the local Japanese dictionary.'),
              const SizedBox(height: 8),
              const Text(
                  'These are dictionary senses, not a historical explanation '
                  'of the kanji or its contribution to every compound.'),
            ]),
      ),
    );
    return;
  }
  await showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (_) => _SingleWordSheet(word: character),
  );
}

class _SingleWordSheet extends StatefulWidget {
  final String word;
  final bool exactCanonical;
  final String preferredDefinition;

  const _SingleWordSheet({
    required this.word,
    this.exactCanonical = false,
    this.preferredDefinition = '',
  });

  @override
  State<_SingleWordSheet> createState() => _SingleWordSheetState();
}

class _SingleWordSheetState extends State<_SingleWordSheet> {
  late final Future<void>? _assetLoad;

  String get word => widget.word;

  @override
  void initState() {
    super.initState();
    if (word.length != 1) {
      _assetLoad = null;
      return;
    }
    final etymologyLoad = EtymologyService.instance.initialize();
    _assetLoad = etymologyLoad;
    unawaited(
        etymologyLoad.then((_) => GlyphService.instance.initialize()).then(
      (_) {
        if (mounted) setState(() {});
      },
    ));
  }

  void _openNestedLookup(BuildContext context, String w) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _SingleWordSheet(word: w),
    );
  }

  Widget _buildSubwordFurigana({
    required BuildContext context,
    required List<String> subwords,
    required Language language,
    required double fontSize,
  }) {
    final dict = DictionaryService.instance;
    return Wrap(
      children: subwords.map((sw) {
        final swEntry = dict.lookup(sw);
        final reading = _displayDictionaryReading(
          swEntry?.pinyin ?? '',
          language,
        );
        return GestureDetector(
          onTap: () => _openNestedLookup(context, sw),
          child: reading.isNotEmpty
              ? _buildFurigana(
                  word: sw,
                  reading: reading,
                  language: language,
                  fontSize: fontSize,
                )
              : Text(
                  sw,
                  style: _cjkTextStyle(
                    fontSize: fontSize,
                    language: language,
                    fontWeight: FontWeight.bold,
                    height: 1.2,
                  ),
                ),
        );
      }).toList(),
    );
  }

  Widget _buildSubwordRow({
    required BuildContext context,
    required List<String> subwords,
    required Language language,
    required double fontSize,
  }) {
    return Wrap(
      children: subwords.map((sw) {
        return GestureDetector(
          onTap: () => _openNestedLookup(context, sw),
          child: Text(
            sw,
            style: _cjkTextStyle(
              fontSize: fontSize,
              language: language,
              fontWeight: FontWeight.bold,
              height: 1.2,
            ),
          ),
        );
      }).toList(),
    );
  }

  List<Widget> _etymSection(BuildContext context, String ch, bool isDark) {
    final etymWidgets = _buildEtymologyWidgets(context, ch, isDark,
        onComponentTap: (c) => _openNestedLookup(context, c));
    if (etymWidgets.isEmpty) return [];
    return [
      const SizedBox(height: 8),
      Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
      ...etymWidgets,
    ];
  }

  Widget _buildSingleWordDisplay(
      BuildContext context, DictEntry? entry, Language lang) {
    final reading = _displayDictionaryReading(entry?.pinyin ?? '', lang);
    final dictForm = entry?.word ?? word;
    final isInflected = dictForm != word;
    final subwords = _segmentCompound(dictForm);
    final charTap = dictForm.length > 1
        ? (String ch) => _openNestedLookup(context, ch)
        : null;
    final chain = isInflected && lang == Language.japanese
        ? deinflectionChain(word, dictForm)
        : <String>[];

    // For single characters without a dict reading, show readings from etymology
    List<Widget> extraReadings = [];
    if (word.length == 1 && reading.isEmpty) {
      final etym = EtymologyService.instance.lookup(word);
      if (etym != null) {
        final parts = <TextSpan>[];
        final style = TextStyle(fontSize: 14, color: Colors.grey[500]);
        if (lang == Language.japanese) {
          if (etym.japaneseKun != null) {
            parts.add(TextSpan(text: etym.japaneseKun!, style: style));
          }
          if (etym.japaneseOn != null) {
            if (parts.isNotEmpty) parts.add(TextSpan(text: '  ', style: style));
            parts.add(TextSpan(text: etym.japaneseOn!, style: style));
          }
        } else {
          if (etym.mandarinReading != null) {
            parts.add(TextSpan(
              text: _displayDictionaryReading(
                etym.mandarinReading!,
                Language.chinese,
              ),
              style: style,
            ));
          }
        }
        if (parts.isNotEmpty) {
          extraReadings = [
            const SizedBox(height: 2),
            Text.rich(TextSpan(children: parts)),
          ];
        }
      }
    }

    if (lang == Language.japanese && reading.isNotEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (subwords != null)
            _buildSubwordFurigana(
              context: context,
              subwords: subwords,
              language: lang,
              fontSize: 32,
            )
          else
            _buildFurigana(
              word: dictForm,
              reading: reading,
              language: lang,
              fontSize: 32,
              onCharTap: charTap,
            ),
          ...extraReadings,
          if (chain.isNotEmpty) ...[
            const SizedBox(height: 6),
            ...chain.map((form) => Padding(
                  padding: const EdgeInsets.only(top: 1),
                  child: Text(
                    form,
                    style: _cjkTextStyle(
                      fontSize: 14,
                      language: lang,
                      color: Colors.grey[500],
                    ),
                  ),
                )),
          ],
        ],
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (subwords != null)
          _buildSubwordRow(
            context: context,
            subwords: subwords,
            language: lang,
            fontSize: 32,
          )
        else
          _buildPlainWord(
            word: dictForm,
            language: lang,
            fontSize: 32,
            onCharTap: charTap,
          ),
        if (reading.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(
            reading,
            style: TextStyle(
              fontSize: 17,
              color: AppTheme.primary,
              fontStyle: FontStyle.italic,
            ),
          ),
        ],
        if (chain.isNotEmpty) ...[
          const SizedBox(height: 6),
          ...chain.map((form) => Padding(
                padding: const EdgeInsets.only(top: 1),
                child: Text(
                  form,
                  style: TextStyle(fontSize: 14, color: Colors.grey[500]),
                ),
              )),
        ] else if (isInflected) ...[
          const SizedBox(height: 4),
          Text(
            word,
            style: TextStyle(fontSize: 15, color: Colors.grey[500]),
          ),
        ],
      ],
    );
  }

  Widget _buildContent(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    var entry = widget.exactCanonical
        ? DictionaryService.instance.lookupExactCanonical(word)
        : DictionaryService.instance.lookup(word);
    final lang = DictionaryService.instance.activeLanguage;

    // Fallback: if no dictionary entry, build one from etymology data
    if (entry == null && word.length == 1) {
      final etym = EtymologyService.instance.lookup(word);
      if (etym != null) {
        String reading = '';
        if (lang == Language.japanese) {
          final parts = <String>[];
          if (etym.japaneseKun != null) parts.add(etym.japaneseKun!);
          if (etym.japaneseOn != null) parts.add(etym.japaneseOn!);
          reading = parts.join(' · ');
        }
        // Fallback to mandarin if no reading found for active language
        if (reading.isEmpty) {
          reading = etym.mandarinReading ?? '';
        }
        final defs = etym.definitions;
        entry = DictEntry(
          word: word,
          pinyin: reading,
          definitions: defs != null ? [defs] : [],
        );
      }
    }

    return _definitionSheetViewport(context,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(24, 0, 24, 24),
          child: SelectionArea(
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      Expanded(
                        child: _buildSingleWordDisplay(context, entry, lang),
                      ),
                      if (_curriculumBadge(entry, lang, word) case final badge?)
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: AppTheme.levelColor(badge.level, lang),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            badge.label,
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ),
                      CopyTextButton(
                        text: word,
                        confirmation: 'Word copied',
                        tooltip: 'Copy word',
                        visualDensity: VisualDensity.compact,
                      ),
                    ],
                  ),

                  if (entry != null && entry.hasDefinitions) ...[
                    const SizedBox(height: 12),
                    Divider(
                        color: isDark ? Colors.grey[700] : Colors.grey[200]),
                    const SizedBox(height: 8),
                    ...([
                      if (widget.preferredDefinition.isNotEmpty)
                        widget.preferredDefinition,
                      ...entry.definitions.where(
                        (definition) =>
                            definition != widget.preferredDefinition,
                      ),
                    ]).asMap().entries.map((e) => Padding(
                          padding: const EdgeInsets.only(bottom: 4),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              SizedBox(
                                width: 24,
                                child: Text(
                                  '${e.key + 1}.',
                                  style: TextStyle(
                                    fontSize: 14,
                                    color: Colors.grey[500],
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                              Expanded(
                                child: Text(
                                  e.value,
                                  style: TextStyle(
                                    fontSize: 14,
                                    height: 1.4,
                                    color: isDark
                                        ? Colors.grey[300]
                                        : Colors.grey[800],
                                  ),
                                ),
                              ),
                            ],
                          ),
                        )),
                  ] else if (word.length > 1) ...[
                    const SizedBox(height: 12),
                    Divider(
                        color: isDark ? Colors.grey[700] : Colors.grey[200]),
                    const SizedBox(height: 8),
                    Text(
                      'Word not in dictionary. Tap characters above for breakdown.',
                      style: TextStyle(fontSize: 12, color: Colors.grey[500]),
                    ),
                  ] else ...[
                    // No dictionary entry — etymology may still be available
                    const SizedBox(height: 12),
                    if (_buildEtymologyWidgets(context, word, isDark,
                        onComponentTap: (ch) =>
                            _openNestedLookup(context, ch)).isEmpty)
                      Text(
                        'No dictionary entry found',
                        style: TextStyle(
                          fontSize: 13,
                          color: Colors.grey[500],
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                  ],

                  // Etymology section (for single characters)
                  if (word.length == 1) ...[
                    ..._etymSection(context, word, isDark),
                  ],
                ],
              ),
            ),
          ),
        ));
  }

  @override
  Widget build(BuildContext context) {
    final assetLoad = _assetLoad;
    if (assetLoad == null || EtymologyService.instance.isReady) {
      return _buildContent(context);
    }
    return FutureBuilder<void>(
      future: assetLoad,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.done) {
          if (snapshot.hasError) {
            return Padding(
              padding: const EdgeInsets.all(32),
              child: Text(
                'Character history could not be loaded.',
                style: TextStyle(color: Colors.grey[600]),
              ),
            );
          }
          return _buildContent(context);
        }
        return const SizedBox(
          height: 180,
          child: Center(child: CircularProgressIndicator()),
        );
      },
    );
  }
}
