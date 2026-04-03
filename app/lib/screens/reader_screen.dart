import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models.dart';
import '../theme.dart';
import '../services/dictionary_service.dart';
import '../services/etymology_service.dart';
import '../services/glyph_service.dart';
import '../services/progress_service.dart';
import '../services/segmenter.dart';
import '../services/vocabulary_service.dart';

TextStyle _cjkTextStyle({
  required double fontSize,
  required Language language,
  Color? color,
  FontWeight? fontWeight,
  double? height,
  Color? backgroundColor,
}) {
  final base = language == Language.japanese
      ? GoogleFonts.notoSansJp
      : GoogleFonts.notoSansSc;
  return base(
    fontSize: fontSize,
    color: color,
    fontWeight: fontWeight,
    height: height,
    backgroundColor: backgroundColor,
  ).copyWith(
    fontFamilyFallback: [
      GoogleFonts.notoSerifJp().fontFamily!,
      GoogleFonts.notoSerifSc().fontFamily!,
      // System fonts for CJK Extension B+ characters
      'sans-serif',
      'serif',
    ],
  );
}

// ---------------------------------------------------------------------------
// Furigana helper
// ---------------------------------------------------------------------------

/// Count consecutive kanji in a word.
int _kanjiCount(String word) =>
    word.codeUnits.where(_isKanji).length;

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
            onTap:
                onComponentTap != null ? () => onComponentTap(comp) : null,
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
          onTap: () =>
              _showGlyphFullscreen(context, character, glyphs, era),
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
      children: chars.map((ch) => GestureDetector(
            onTap:
                onComponentTap != null ? () => onComponentTap(ch) : null,
            child: Text(
              ch,
              style: TextStyle(fontSize: 15, color: Colors.blue[300]),
            ),
          )).toList(),
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

void _showGlyphFullscreen(
    BuildContext context, String character, GlyphEntry glyphs, String initialEra) {
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
                      child: Icon(Icons.close,
                          size: 20, color: Colors.grey[500]),
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
                        onPressed: () => setState(() =>
                            current = (current - 1) % eras.length),
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
                            color: isDark
                                ? Colors.grey[300]
                                : Colors.grey[700],
                          ),
                        ),
                      ),
                      IconButton(
                        onPressed: () => setState(() =>
                            current = (current + 1) % eras.length),
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

class _ParagraphData {
  final String raw;
  final String plainText;
  final bool isHeading;
  final List<_TokenEntry> tokens;
  _ParagraphData({
    required this.raw,
    required this.plainText,
    required this.isHeading,
    required this.tokens,
  });
}

// ---------------------------------------------------------------------------
// Reader screen
// ---------------------------------------------------------------------------

class ReaderScreen extends StatefulWidget {
  final Reader reader;
  final int initialChapter;

  const ReaderScreen({
    super.key,
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

    final paragraphTexts = <String>[];
    for (final para in ch.content.split('\n\n')) {
      final trimmed = para.trim();
      if (trimmed.isNotEmpty) paragraphTexts.add(trimmed);
    }

    // Segment on main thread, yielding between paragraphs for UI responsiveness
    final paragraphs = <_ParagraphData>[];
    final allCjk = <String>[];

    for (int pi = 0; pi < paragraphTexts.length; pi++) {
      // Yield every few paragraphs to let the spinner animate
      if (pi % 3 == 0) await Future.delayed(Duration.zero);

      final raw = paragraphTexts[pi];
      final tokens = segmentText(raw, dict);
      final isHeading = raw.startsWith('**') && raw.contains('**');

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
        if (tappable) allCjk.add(t);
      }

      paragraphs.add(_ParagraphData(
        raw: raw,
        plainText: tokens.join(),
        isHeading: isHeading,
        tokens: tokenEntries,
      ));
    }

    final result = _ChapterSegmented(
      index: chapterIndex,
      title: ch.title,
      paragraphs: paragraphs,
      allCjkWords: allCjk,
    );
    _segmentCache[chapterIndex] = result;
    return result;
  }

  Future<void> _loadPreferences() async {
    final prefs = await SharedPreferences.getInstance();
    final savedProgress =
        await ProgressService.instance.getProgress(widget.reader.id);

    if (!mounted) return;

    final chapter = (savedProgress?.chapter ?? widget.initialChapter)
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
    _saveProgress();
  }

  @override
  void dispose() {
    _chapterController.dispose();
    _highlightedIndex.dispose();
    super.dispose();
  }

  void _showWordDefinition(List<String> allWords, int index) {
    _highlightedIndex.value = index;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: Theme.of(context).colorScheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _WordDefinitionSheet(
        allWords: allWords,
        initialIndex: index,
        highlightedIndex: _highlightedIndex,
      ),
    ).whenComplete(() => _highlightedIndex.value = -1);
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
          IconButton(
            icon: const Icon(Icons.text_decrease, size: 20),
            onPressed: _fontSize > _minFontSize
                ? () {
                    setState(() => _fontSize -= 2);
                    _saveFontSize();
                  }
                : null,
          ),
          IconButton(
            icon: const Icon(Icons.text_increase, size: 20),
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
                  chapter: chapter,
                  fontSize: _fontSize,
                  isDark: isDark,
                  language: widget.reader.language,
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
              padding:
                  const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              decoration: BoxDecoration(
                color: isDark ? Colors.grey[900] : Colors.white,
                border: Border(
                  top: BorderSide(
                    color:
                        isDark ? Colors.grey[800]! : Colors.grey[200]!,
                  ),
                ),
              ),
              child: SafeArea(
                child: Row(
                  children: [
                    IconButton(
                      onPressed: _currentChapter > 0
                          ? () => _chapterController.previousPage(
                                duration:
                                    const Duration(milliseconds: 250),
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
                                duration: const Duration(
                                    milliseconds: 250),
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
  final List<String> allCjkWords;
  _ChapterSegmented({
    required this.index,
    required this.title,
    required this.paragraphs,
    required this.allCjkWords,
  });
}

// ---------------------------------------------------------------------------
// Chapter view (scrollable content for a single chapter)
// ---------------------------------------------------------------------------

class _ChapterView extends StatefulWidget {
  final _ChapterSegmented chapter;
  final double fontSize;
  final bool isDark;
  final Language language;
  final void Function(List<String>, int) onWordTap;
  final ValueNotifier<int> highlightedIndex;
  final double initialScrollFraction;
  final ValueChanged<double> onScrollFractionChanged;

  const _ChapterView({
    required this.chapter,
    required this.fontSize,
    required this.isDark,
    required this.language,
    required this.onWordTap,
    required this.highlightedIndex,
    required this.initialScrollFraction,
    required this.onScrollFractionChanged,
  });

  @override
  State<_ChapterView> createState() => _ChapterViewState();
}

class _ChapterViewState extends State<_ChapterView> {
  final List<GestureRecognizer> _recognizers = [];
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
    for (final r in _recognizers) {
      r.dispose();
    }
    _recognizers.clear();
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: widget.highlightedIndex,
      builder: (context, highlightIdx, _) {
        _disposeRecognizers();
        WidgetsBinding.instance.addPostFrameCallback((_) => _restoreScroll());
        return ListView(
          controller: _scrollController,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          children: [
            SelectableText(
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
              color: widget.isDark ? Colors.grey[700] : Colors.grey[300],
            ),
            const SizedBox(height: 8),
            ...widget.chapter.paragraphs
                .map((p) => _buildParagraph(p, highlightIdx)),
          ],
        );
      },
    );
  }

  Widget _buildParagraph(_ParagraphData para, int highlightIdx) {
    if (para.isHeading) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: SelectableText(
          para.raw.replaceAll('**', ''),
          style: _cjkTextStyle(
            fontSize: widget.fontSize - 2,
            language: widget.language,
            fontWeight: FontWeight.bold,
            color: widget.isDark ? Colors.grey[300] : Colors.grey[700],
            height: 1.6,
          ),
        ),
      );
    }

    final baseStyle = _cjkTextStyle(
      fontSize: widget.fontSize,
      language: widget.language,
      height: 1.8,
      color: widget.isDark ? Colors.grey[200] : AppTheme.textPrimary,
    );

    final spans = <TextSpan>[];
    for (final token in para.tokens) {
      // Tappable: contains kanji or is a multi-char kana word (not a lone particle)
      if (token.globalIndex >= 0 && DictionaryService.instance.isReady) {
        final isHighlighted = token.globalIndex == highlightIdx;
        final recognizer = TapGestureRecognizer()
          ..onTap = () => widget.onWordTap(
              widget.chapter.allCjkWords, token.globalIndex);
        _recognizers.add(recognizer);

        spans.add(TextSpan(
          text: token.text,
          style: baseStyle.copyWith(
            backgroundColor: isHighlighted
                ? AppTheme.primary.withValues(alpha: 0.2)
                : null,
          ),
          recognizer: recognizer,
        ));
      } else {
        spans.add(TextSpan(text: token.text, style: baseStyle));
      }
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: SelectableText.rich(
        TextSpan(children: spans),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Word definition bottom sheet
// ---------------------------------------------------------------------------

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
                        style:
                            TextStyle(color: Colors.grey[400], fontSize: 14))
                    : Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          if (charEntry != null && charEntry.pinyin.isNotEmpty)
                            Text(charEntry.pinyin,
                                style: TextStyle(
                                    fontSize: 13,
                                    color: AppTheme.primary,
                                    fontStyle: FontStyle.italic)),
                          if (charEntry != null && charEntry.definitions.isNotEmpty)
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
        final reading = swEntry?.pinyin ?? '';
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
    final reading = entry?.pinyin ?? '';
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

    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      child: SingleChildScrollView(
        child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.grey[400],
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: 16),

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
              if (entry?.hskLevel != null)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: Builder(builder: (context) {
                    final lang = DictionaryService.instance.activeLanguage;
                    final lvl = entry!.hskLevel!;
                    String label;
                    if (lang == Language.japanese) {
                      const jlpt = {
                        1: 'N5',
                        2: 'N4',
                        3: 'N3',
                        4: 'N2',
                        5: 'N1'
                      };
                      label = 'JLPT ${jlpt[lvl] ?? lvl}';
                    } else {
                      label = 'HSK $lvl';
                    }
                    return Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 3),
                      decoration: BoxDecoration(
                        color: AppTheme.levelColor(lvl, lang),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        label,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    );
                  }),
                ),
              IconButton(
                onPressed: () {
                  Clipboard.setData(ClipboardData(text: _word));
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text('Copied "$_word"'),
                      duration: const Duration(seconds: 1),
                      behavior: SnackBarBehavior.floating,
                    ),
                  );
                },
                icon: const Icon(Icons.copy_rounded, size: 20),
                tooltip: 'Copy',
                visualDensity: VisualDensity.compact,
              ),
              IconButton(
                onPressed: _toggleSave,
                icon: _loading
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Icon(
                        _saved
                            ? Icons.bookmark_rounded
                            : Icons.bookmark_outline_rounded,
                        color: _saved ? AppTheme.primary : null,
                        size: 24,
                      ),
                tooltip:
                    _saved ? 'Remove from vocabulary' : 'Save to vocabulary',
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),

          if (entry != null && entry.hasDefinitions) ...[
            const SizedBox(height: 12),
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
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
                            color:
                                isDark ? Colors.grey[300] : Colors.grey[800],
                          ),
                        ),
                      ),
                    ],
                  ),
                )),
          ] else ...[
            const SizedBox(height: 12),
            if (entry == null && _word.length > 1) ...[
              Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
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
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
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
                    style: TextStyle(fontSize: 11, color: Colors.grey[500]),
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
    );
  }
}

// ---------------------------------------------------------------------------
// Simple single-word lookup sheet (for recursive lookups)
// ---------------------------------------------------------------------------

class _SingleWordSheet extends StatelessWidget {
  final String word;

  const _SingleWordSheet({required this.word});

  void _openNestedLookup(BuildContext context, String w) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
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
        final reading = swEntry?.pinyin ?? '';
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
    final reading = entry?.pinyin ?? '';
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
          if (etym.japaneseKun != null) parts.add(TextSpan(text: etym.japaneseKun!, style: style));
          if (etym.japaneseOn != null) {
            if (parts.isNotEmpty) parts.add(TextSpan(text: '  ', style: style));
            parts.add(TextSpan(text: etym.japaneseOn!, style: style));
          }
        } else {
          if (etym.mandarinReading != null) {
            parts.add(TextSpan(text: etym.mandarinReading!, style: style));
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

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    var entry = DictionaryService.instance.lookup(word);
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

    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      child: SingleChildScrollView(
        child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Center(
            child: Container(
              width: 36,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.grey[400],
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: 16),

          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: _buildSingleWordDisplay(context, entry, lang),
              ),
              if (entry?.hskLevel != null)
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: AppTheme.levelColor(entry!.hskLevel!, lang),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text(
                    lang == Language.japanese
                        ? 'JLPT ${const {1: 'N5', 2: 'N4', 3: 'N3', 4: 'N2', 5: 'N1'}[entry.hskLevel] ?? entry.hskLevel}'
                        : 'HSK ${entry.hskLevel}',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              IconButton(
                onPressed: () {
                  Clipboard.setData(ClipboardData(text: word));
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text('Copied "$word"'),
                      duration: const Duration(seconds: 1),
                      behavior: SnackBarBehavior.floating,
                    ),
                  );
                },
                icon: const Icon(Icons.copy_rounded, size: 20),
                tooltip: 'Copy',
                visualDensity: VisualDensity.compact,
              ),
            ],
          ),

          if (entry != null && entry.hasDefinitions) ...[
            const SizedBox(height: 12),
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
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
                            color:
                                isDark ? Colors.grey[300] : Colors.grey[800],
                          ),
                        ),
                      ),
                    ],
                  ),
                )),
          ] else if (word.length > 1) ...[
            const SizedBox(height: 12),
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
            const SizedBox(height: 8),
            Text(
              'Word not in dictionary. Tap characters above for breakdown.',
              style: TextStyle(fontSize: 12, color: Colors.grey[500]),
            ),
          ] else ...[
            // No dictionary entry — etymology may still be available
            const SizedBox(height: 12),
            if (_buildEtymologyWidgets(context, word, isDark,
                    onComponentTap: (ch) => _openNestedLookup(context, ch))
                .isEmpty)
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
    );
  }
}
