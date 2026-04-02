import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models.dart';
import '../theme.dart';
import '../services/dictionary_service.dart';
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
  );
}

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
  late int _currentChapter;
  late PageController _pageController;
  double _fontSize = 20.0;
  final double _minFontSize = 14.0;
  final double _maxFontSize = 32.0;
  final ValueNotifier<int> _highlightedIndex = ValueNotifier(-1);

  @override
  void initState() {
    super.initState();
    _currentChapter = widget.initialChapter;
    _pageController = PageController(initialPage: _currentChapter);
    _loadPreferences();
    VocabularyService.instance.loadWords();
    _saveProgress();
  }

  Future<void> _loadPreferences() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) return;
    setState(() {
      _fontSize = prefs.getDouble('reader_font_size') ?? 20.0;
    });
  }

  Future<void> _saveFontSize() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('reader_font_size', _fontSize);
  }

  void _saveProgress() {
    ProgressService.instance.saveProgress(ReadingProgress(
      readerId: widget.reader.id,
      bookTitle: widget.reader.bookTitle,
      bookTitleEn: widget.reader.bookTitleEn,
      levelLabel: widget.reader.levelLabel,
      chapter: _currentChapter,
      totalChapters: widget.reader.chapters.length,
      lastRead: DateTime.now(),
    ));
  }

  @override
  void dispose() {
    _pageController.dispose();
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

    return Scaffold(
      appBar: AppBar(
        title: Text(
          '${_currentChapter + 1} / ${widget.reader.chapters.length}',
          style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w500),
        ),
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
      body: PageView.builder(
        controller: _pageController,
        itemCount: widget.reader.chapters.length,
        onPageChanged: (index) {
          setState(() => _currentChapter = index);
          _saveProgress();
        },
        itemBuilder: (context, index) {
          final ch = widget.reader.chapters[index];
          return _ChapterView(
            chapter: ch,
            fontSize: _fontSize,
            isDark: isDark,
            language: widget.reader.language,
            onWordTap: _showWordDefinition,
            highlightedIndex: _highlightedIndex,
          );
        },
      ),
      bottomNavigationBar: Container(
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
              TextButton.icon(
                onPressed: _currentChapter > 0
                    ? () => _pageController.previousPage(
                          duration: const Duration(milliseconds: 300),
                          curve: Curves.easeInOut,
                        )
                    : null,
                icon: const Icon(Icons.chevron_left, size: 20),
                label: const Text('Prev'),
              ),
              const Spacer(),
              Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                decoration: BoxDecoration(
                  color: levelColor.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  widget.reader.levelLabel,
                  style: TextStyle(
                    color: levelColor,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const Spacer(),
              TextButton.icon(
                onPressed:
                    _currentChapter < widget.reader.chapters.length - 1
                        ? () => _pageController.nextPage(
                              duration: const Duration(milliseconds: 300),
                              curve: Curves.easeInOut,
                            )
                        : null,
                label: const Icon(Icons.chevron_right, size: 20),
                icon: const Text('Next'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Chapter view
// ---------------------------------------------------------------------------

class _ChapterView extends StatelessWidget {
  final Chapter chapter;
  final double fontSize;
  final bool isDark;
  final Language language;
  final void Function(List<String>, int) onWordTap;
  final ValueNotifier<int> highlightedIndex;

  const _ChapterView({
    required this.chapter,
    required this.fontSize,
    required this.isDark,
    required this.language,
    required this.onWordTap,
    required this.highlightedIndex,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SelectableText(
            chapter.title,
            style: _cjkTextStyle(
              fontSize: fontSize + 4,
              language: language,
              fontWeight: FontWeight.bold,
              height: 1.4,
            ),
          ),
          const SizedBox(height: 8),
          Divider(
            color: isDark ? Colors.grey[700] : Colors.grey[300],
            height: 24,
          ),
          _InteractiveContent(
            text: chapter.content,
            fontSize: fontSize,
            isDark: isDark,
            language: language,
            onWordTap: onWordTap,
            highlightedIndex: highlightedIndex,
          ),
          const SizedBox(height: 80),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Interactive selectable text with tappable CJK words
// ---------------------------------------------------------------------------

bool _isCJK(int code) =>
    (code >= 0x4E00 && code <= 0x9FFF) ||
    (code >= 0x3400 && code <= 0x4DBF) ||
    (code >= 0xF900 && code <= 0xFAFF) ||
    (code >= 0x3040 && code <= 0x309F) ||
    (code >= 0x30A0 && code <= 0x30FF);

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

class _InteractiveContent extends StatefulWidget {
  final String text;
  final double fontSize;
  final bool isDark;
  final Language language;
  final void Function(List<String>, int) onWordTap;
  final ValueNotifier<int> highlightedIndex;

  const _InteractiveContent({
    required this.text,
    required this.fontSize,
    required this.isDark,
    required this.language,
    required this.onWordTap,
    required this.highlightedIndex,
  });

  @override
  State<_InteractiveContent> createState() => _InteractiveContentState();
}

class _InteractiveContentState extends State<_InteractiveContent> {
  late List<_ParagraphData> _paragraphs;
  late List<String> _allCjkWords;
  final List<GestureRecognizer> _recognizers = [];

  @override
  void initState() {
    super.initState();
    _rebuild();
  }

  @override
  void didUpdateWidget(_InteractiveContent old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text) {
      _disposeRecognizers();
      _rebuild();
    }
  }

  @override
  void dispose() {
    _disposeRecognizers();
    super.dispose();
  }

  void _disposeRecognizers() {
    for (final r in _recognizers) {
      r.dispose();
    }
    _recognizers.clear();
  }

  void _rebuild() {
    final dict = DictionaryService.instance;
    _paragraphs = [];
    _allCjkWords = [];

    for (final para in widget.text.split('\n\n')) {
      final trimmed = para.trim();
      if (trimmed.isEmpty) continue;

      final isHeading = trimmed.startsWith('**') && trimmed.contains('**');
      final tokens = segmentText(trimmed, dict);

      final tokenEntries = <_TokenEntry>[];
      int charOffset = 0;
      for (final t in tokens) {
        final isCjk = t.isNotEmpty && _isCJK(t.codeUnitAt(0));
        tokenEntries.add(_TokenEntry(
          text: t,
          isCjk: isCjk,
          globalIndex: isCjk ? _allCjkWords.length : -1,
          startOffset: charOffset,
          endOffset: charOffset + t.length,
        ));
        charOffset += t.length;
        if (isCjk) _allCjkWords.add(t);
      }

      _paragraphs.add(_ParagraphData(
        raw: trimmed,
        plainText: tokens.join(),
        isHeading: isHeading,
        tokens: tokenEntries,
      ));
    }
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: widget.highlightedIndex,
      builder: (context, highlightIdx, _) {
        _disposeRecognizers();
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: _paragraphs
              .map((p) => _buildParagraph(p, highlightIdx))
              .toList(),
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

    // Build spans: every token gets the same base style.
    // CJK tokens additionally get a TapGestureRecognizer and
    // optional highlight background.
    final spans = <TextSpan>[];
    for (final token in para.tokens) {
      if (token.isCjk && DictionaryService.instance.isReady) {
        final isHighlighted = token.globalIndex == highlightIdx;
        final recognizer = TapGestureRecognizer()
          ..onTap =
              () => widget.onWordTap(_allCjkWords, token.globalIndex);
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
                language:
                    DictionaryService.instance.activeLanguage,
                fontWeight: FontWeight.bold,
                color: AppTheme.primary,
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: charEntry == null
                  ? Text('—',
                      style: TextStyle(color: Colors.grey[400], fontSize: 14))
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (charEntry.pinyin.isNotEmpty)
                          Text(charEntry.pinyin,
                              style: TextStyle(
                                  fontSize: 13,
                                  color: AppTheme.primary,
                                  fontStyle: FontStyle.italic)),
                        if (charEntry.definitions.isNotEmpty)
                          Text(
                            charEntry.definitions.first,
                            style: TextStyle(
                              fontSize: 13,
                              color: isDark
                                  ? Colors.grey[300]
                                  : Colors.grey[700],
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
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Drag handle
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

          // Word + action buttons
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _word.length > 1
                        ? Wrap(
                            children: _word.characters.map((ch) {
                              return GestureDetector(
                                onTap: () => _openNestedLookup(ch),
                                child: Text(
                                  ch,
                                  style: _cjkTextStyle(
                                    fontSize: 32,
                                    language: DictionaryService
                                        .instance.activeLanguage,
                                    fontWeight: FontWeight.bold,
                                    height: 1.2,
                                  ),
                                ),
                              );
                            }).toList(),
                          )
                        : Text(
                            _word,
                            style: _cjkTextStyle(
                              fontSize: 32,
                              language:
                                  DictionaryService.instance.activeLanguage,
                              fontWeight: FontWeight.bold,
                              height: 1.2,
                            ),
                          ),
                    if (entry != null && entry.pinyin.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        entry.pinyin,
                        style: TextStyle(
                          fontSize: 17,
                          color: AppTheme.primary,
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    ],
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

          // Definitions
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

          // Prev / Next word navigation
          if (widget.allWords.length > 1) ...[
            const SizedBox(height: 8),
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[200]),
            Row(
              children: [
                // Left: prev button (whole area tappable)
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
                            color: _hasPrev
                                ? null
                                : Colors.grey[400]),
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
                // Center: counter
                Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12),
                  child: Text(
                    '${_currentIndex + 1} / ${widget.allWords.length}',
                    style: TextStyle(
                        fontSize: 11, color: Colors.grey[500]),
                  ),
                ),
                // Right: next button (whole area tappable)
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
                            color: _hasNext
                                ? null
                                : Colors.grey[400]),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
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

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final entry = DictionaryService.instance.lookup(word);
    final lang = DictionaryService.instance.activeLanguage;

    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Drag handle
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

          // Word + pinyin + level
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    word.length > 1
                        ? Wrap(
                            children: word.characters.map((ch) {
                              return GestureDetector(
                                onTap: () => _openNestedLookup(context, ch),
                                child: Text(
                                  ch,
                                  style: _cjkTextStyle(
                                    fontSize: 32,
                                    language: lang,
                                    fontWeight: FontWeight.bold,
                                    height: 1.2,
                                  ),
                                ),
                              );
                            }).toList(),
                          )
                        : Text(
                            word,
                            style: _cjkTextStyle(
                              fontSize: 32,
                              language: lang,
                              fontWeight: FontWeight.bold,
                              height: 1.2,
                            ),
                          ),
                    if (entry != null && entry.pinyin.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        entry.pinyin,
                        style: TextStyle(
                          fontSize: 17,
                          color: AppTheme.primary,
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    ],
                  ],
                ),
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

          // Definitions
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
            const SizedBox(height: 12),
            Text(
              'No dictionary entry found',
              style: TextStyle(
                fontSize: 13,
                color: Colors.grey[500],
                fontStyle: FontStyle.italic,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
