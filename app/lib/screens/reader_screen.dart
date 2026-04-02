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

// ---------------------------------------------------------------------------
// Data structures for segmentation & pagination
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

/// A page is a slice of paragraphs from a chapter.
class _PageData {
  final int chapterIndex;
  final String chapterTitle;
  final List<_ParagraphData> paragraphs;
  final List<String> allCjkWords; // shared across the chapter
  final bool isFirstPageOfChapter;

  _PageData({
    required this.chapterIndex,
    required this.chapterTitle,
    required this.paragraphs,
    required this.allCjkWords,
    required this.isFirstPageOfChapter,
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

  // Pagination state
  List<_PageData>? _pages;
  PageController? _pageController;
  int _currentPageIndex = 0;
  int _initialPage = 0;
  double _lastAvailableHeight = 0;

  // Lazy chapter segmentation cache
  final Map<int, _ChapterSegmented> _segmentCache = {};

  @override
  void initState() {
    super.initState();
    _loadPreferences();
    VocabularyService.instance.loadWords();
  }

  _ChapterSegmented _getSegmented(int chapterIndex) {
    if (_segmentCache.containsKey(chapterIndex)) {
      return _segmentCache[chapterIndex]!;
    }

    final dict = DictionaryService.instance;
    final ch = widget.reader.chapters[chapterIndex];
    final paragraphs = <_ParagraphData>[];
    final allCjk = <String>[];

    for (final para in ch.content.split('\n\n')) {
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
          globalIndex: isCjk ? allCjk.length : -1,
          startOffset: charOffset,
          endOffset: charOffset + t.length,
        ));
        charOffset += t.length;
        if (isCjk) allCjk.add(t);
      }

      paragraphs.add(_ParagraphData(
        raw: trimmed,
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

  bool _isPaginating = false;

  Future<void> _paginate(double availableHeight) async {
    if (availableHeight <= 0 || _isPaginating) return;
    _isPaginating = true;
    _lastAvailableHeight = availableHeight;

    final pages = <_PageData>[];
    final totalChapters = widget.reader.chapters.length;

    for (int ci = 0; ci < totalChapters; ci++) {
      final chapter = _getSegmented(ci);
      // Yield to let UI breathe between chapters
      if (ci > 0 && ci % 2 == 0) {
        await Future.delayed(Duration.zero);
        if (!mounted) { _isPaginating = false; return; }
      }
      final titleHeight = 50.0;
      final paraSpacing = 16.0;
      var remaining = availableHeight - titleHeight;
      var currentPageParas = <_ParagraphData>[];
      var isFirst = true;

      for (final para in chapter.paragraphs) {
        // Estimate paragraph height: chars per line depends on font size
        // Rough: each line ~ (availableWidth / fontSize) chars,
        // line height ~ fontSize * 1.8
        final lineHeight = _fontSize * 1.8;
        final charsPerLine = (300 / _fontSize).floor().clamp(8, 40);
        final lines =
            (para.plainText.length / charsPerLine).ceil().clamp(1, 1000);
        final paraHeight = lines * lineHeight + paraSpacing;

        if (remaining - paraHeight < 0 && currentPageParas.isNotEmpty) {
          // Flush current page
          pages.add(_PageData(
            chapterIndex: chapter.index,
            chapterTitle: chapter.title,
            paragraphs: List.of(currentPageParas),
            allCjkWords: chapter.allCjkWords,
            isFirstPageOfChapter: isFirst,
          ));
          isFirst = false;
          currentPageParas = [para];
          remaining = availableHeight - paraHeight;
        } else {
          currentPageParas.add(para);
          remaining -= paraHeight;
        }
      }

      // Flush remaining
      if (currentPageParas.isNotEmpty) {
        pages.add(_PageData(
          chapterIndex: chapter.index,
          chapterTitle: chapter.title,
          paragraphs: currentPageParas,
          allCjkWords: chapter.allCjkWords,
          isFirstPageOfChapter: isFirst,
        ));
      }
    }

    // Find the page to start on
    int startPage = 0;
    if (_pages == null) {
      // First pagination — restore from saved progress
      startPage = _initialPage;
      // Clamp
      if (startPage >= pages.length) startPage = pages.length - 1;
      if (startPage < 0) startPage = 0;
    } else {
      // Re-pagination (font size change) — try to stay on same chapter
      final curChapter = _pages![_currentPageIndex].chapterIndex;
      startPage = pages.indexWhere((p) => p.chapterIndex == curChapter);
      if (startPage < 0) startPage = 0;
    }

    if (!mounted) { _isPaginating = false; return; }
    setState(() {
      _pages = pages;
      _currentPageIndex = startPage;
      _pageController?.dispose();
      _pageController = PageController(initialPage: startPage);
    });

    _isPaginating = false;
    _saveProgress();
  }

  Future<void> _loadPreferences() async {
    final prefs = await SharedPreferences.getInstance();
    final savedProgress =
        await ProgressService.instance.getProgress(widget.reader.id);

    if (!mounted) return;
    setState(() {
      _fontSize = prefs.getDouble('reader_font_size') ?? 20.0;
    });

    // Compute initial page from saved chapter + page
    if (savedProgress != null) {
      _initialPage = _computeGlobalPage(
          savedProgress.chapter, savedProgress.page);
    } else {
      _initialPage = _computeGlobalPage(widget.initialChapter, 0);
    }

    // Re-paginate if we already had a height
    if (_lastAvailableHeight > 0) {
      _paginate(_lastAvailableHeight);
    }
  }

  int _computeGlobalPage(int chapter, int pageInChapter) {
    // This needs _pages to be built. If not yet, store and apply later.
    // For now, estimate: count pages of chapters before this one.
    // We'll correct when _paginate runs.
    if (_pages != null) {
      int target = 0;
      for (int i = 0; i < _pages!.length; i++) {
        if (_pages![i].chapterIndex == chapter) {
          target = i + pageInChapter;
          break;
        }
      }
      return target.clamp(0, _pages!.length - 1);
    }
    // Rough estimate before pagination: assume 3 pages per chapter
    return chapter * 3 + pageInChapter;
  }

  Future<void> _saveFontSize() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble('reader_font_size', _fontSize);
  }

  void _saveProgress() {
    if (_pages == null || _pages!.isEmpty) return;
    final page = _pages![_currentPageIndex];

    // Count page within chapter
    int pageInChapter = 0;
    for (int i = _currentPageIndex - 1; i >= 0; i--) {
      if (_pages![i].chapterIndex == page.chapterIndex) {
        pageInChapter++;
      } else {
        break;
      }
    }

    // Count total pages in this chapter
    int totalPagesInChapter =
        _pages!.where((p) => p.chapterIndex == page.chapterIndex).length;

    ProgressService.instance.saveProgress(ReadingProgress(
      readerId: widget.reader.id,
      bookTitle: widget.reader.bookTitle,
      bookTitleEn: widget.reader.bookTitleEn,
      levelLabel: widget.reader.levelLabel,
      chapter: page.chapterIndex,
      totalChapters: widget.reader.chapters.length,
      page: pageInChapter,
      totalPages: totalPagesInChapter,
      lastRead: DateTime.now(),
    ));
  }

  @override
  void dispose() {
    _pageController?.dispose();
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
        title: _pages != null
            ? Text(
                _pages![_currentPageIndex].chapterTitle,
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
                    _paginate(_lastAvailableHeight);
                  }
                : null,
          ),
          IconButton(
            icon: const Icon(Icons.text_increase, size: 20),
            onPressed: _fontSize < _maxFontSize
                ? () {
                    setState(() => _fontSize += 2);
                    _saveFontSize();
                    _paginate(_lastAvailableHeight);
                  }
                : null,
          ),
        ],
      ),
      body: LayoutBuilder(
        builder: (context, constraints) {
          final height = constraints.maxHeight;
          if (_pages == null || height != _lastAvailableHeight) {
            WidgetsBinding.instance.addPostFrameCallback((_) {
              _paginate(height);
            });
          }

          if (_pages == null || _pageController == null) {
            return const Center(child: CircularProgressIndicator());
          }

          return PageView.builder(
            controller: _pageController!,
            itemCount: _pages!.length,
            onPageChanged: (index) {
              setState(() => _currentPageIndex = index);
              _saveProgress();
            },
            itemBuilder: (context, index) {
              return _PageView(
                page: _pages![index],
                fontSize: _fontSize,
                isDark: isDark,
                language: widget.reader.language,
                onWordTap: _showWordDefinition,
                highlightedIndex: _highlightedIndex,
              );
            },
          );
        },
      ),
      bottomNavigationBar: _pages != null
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
                      onPressed: _currentPageIndex > 0
                          ? () => _pageController!.previousPage(
                                duration:
                                    const Duration(milliseconds: 250),
                                curve: Curves.easeInOut,
                              )
                          : null,
                      icon: const Icon(Icons.chevron_left),
                    ),
                    const Spacer(),
                    Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 10, vertical: 2),
                          decoration: BoxDecoration(
                            color: levelColor.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Text(
                            'Ch. ${_pages![_currentPageIndex].chapterIndex + 1}/${widget.reader.chapters.length}',
                            style: TextStyle(
                              color: levelColor,
                              fontSize: 11,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          '${_currentPageIndex + 1} / ${_pages!.length}',
                          style: TextStyle(
                              fontSize: 10, color: Colors.grey[500]),
                        ),
                      ],
                    ),
                    const Spacer(),
                    IconButton(
                      onPressed:
                          _currentPageIndex < (_pages!.length - 1)
                              ? () => _pageController!.nextPage(
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
// Page view (a slice of paragraphs from a chapter)
// ---------------------------------------------------------------------------

class _PageView extends StatefulWidget {
  final _PageData page;
  final double fontSize;
  final bool isDark;
  final Language language;
  final void Function(List<String>, int) onWordTap;
  final ValueNotifier<int> highlightedIndex;

  const _PageView({
    required this.page,
    required this.fontSize,
    required this.isDark,
    required this.language,
    required this.onWordTap,
    required this.highlightedIndex,
  });

  @override
  State<_PageView> createState() => _PageViewState();
}

class _PageViewState extends State<_PageView> {
  final List<GestureRecognizer> _recognizers = [];

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

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: widget.highlightedIndex,
      builder: (context, highlightIdx, _) {
        _disposeRecognizers();
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (widget.page.isFirstPageOfChapter) ...[
                SelectableText(
                  widget.page.chapterTitle,
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
              ],
              ...widget.page.paragraphs
                  .map((p) => _buildParagraph(p, highlightIdx)),
            ],
          ),
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
      if (token.isCjk && DictionaryService.instance.isReady) {
        final isHighlighted = token.globalIndex == highlightIdx;
        final recognizer = TapGestureRecognizer()
          ..onTap = () => widget.onWordTap(
              widget.page.allCjkWords, token.globalIndex);
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
                  language: DictionaryService.instance.activeLanguage,
                  fontWeight: FontWeight.bold,
                  color: AppTheme.primary,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: charEntry == null
                    ? Text('—',
                        style:
                            TextStyle(color: Colors.grey[400], fontSize: 14))
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
