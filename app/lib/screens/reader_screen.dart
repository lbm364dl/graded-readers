import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models.dart';
import '../theme.dart';
import '../services/dictionary_service.dart';
import '../services/segmenter.dart';
import '../services/vocabulary_service.dart';

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

  @override
  void initState() {
    super.initState();
    _currentChapter = widget.initialChapter;
    _pageController = PageController(initialPage: _currentChapter);
    _loadPreferences();
    // Pre-warm vocabulary cache so isSaved() works synchronously in sheets
    VocabularyService.instance.loadWords();
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

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  void _showWordDefinition(String word, List<String> allWords) {
    final index = allWords.indexOf(word);
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _WordDefinitionSheet(
        allWords: allWords,
        initialIndex: index < 0 ? 0 : index,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Scaffold(
      appBar: AppBar(
        title: Text(
          '${_currentChapter + 1}/${widget.reader.chapters.length}',
          style: const TextStyle(fontSize: 16),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.text_decrease),
            onPressed: _fontSize > _minFontSize
                ? () {
                    setState(() => _fontSize -= 2);
                    _saveFontSize();
                  }
                : null,
          ),
          IconButton(
            icon: const Icon(Icons.text_increase),
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
        },
        itemBuilder: (context, index) {
          final ch = widget.reader.chapters[index];
          return _ChapterView(
            chapter: ch,
            fontSize: _fontSize,
            isDark: isDark,
            level: widget.reader.level,
            onWordTap: _showWordDefinition,
          );
        },
      ),
      bottomNavigationBar: _buildBottomBar(),
    );
  }

  Widget _buildBottomBar() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        border: Border(top: BorderSide(color: Colors.grey[300]!)),
      ),
      child: SafeArea(
        child: Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            TextButton.icon(
              onPressed: _currentChapter > 0
                  ? () => _pageController.previousPage(
                        duration: const Duration(milliseconds: 300),
                        curve: Curves.easeInOut,
                      )
                  : null,
              icon: const Icon(Icons.arrow_back_ios, size: 16),
              label: const Text('Previous'),
            ),
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: AppTheme.levelColor(
                    widget.reader.level, widget.reader.language),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                widget.reader.levelLabel,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            TextButton.icon(
              onPressed:
                  _currentChapter < widget.reader.chapters.length - 1
                      ? () => _pageController.nextPage(
                            duration: const Duration(milliseconds: 300),
                            curve: Curves.easeInOut,
                          )
                      : null,
              icon: const Icon(Icons.arrow_forward_ios, size: 16),
              label: const Text('Next'),
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _ChapterView extends StatelessWidget {
  final Chapter chapter;
  final double fontSize;
  final bool isDark;
  final int level;
  final void Function(String, List<String>) onWordTap;

  const _ChapterView({
    required this.chapter,
    required this.fontSize,
    required this.isDark,
    required this.level,
    required this.onWordTap,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            chapter.title,
            style: TextStyle(
              fontSize: fontSize + 4,
              fontWeight: FontWeight.bold,
              height: 1.4,
            ),
          ),
          const Divider(height: 24),
          _InteractiveContent(
            text: chapter.content,
            fontSize: fontSize,
            isDark: isDark,
            onWordTap: onWordTap,
          ),
          const SizedBox(height: 60),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------

class _InteractiveContent extends StatefulWidget {
  final String text;
  final double fontSize;
  final bool isDark;
  final void Function(String, List<String>) onWordTap;

  const _InteractiveContent({
    required this.text,
    required this.fontSize,
    required this.isDark,
    required this.onWordTap,
  });

  @override
  State<_InteractiveContent> createState() => _InteractiveContentState();
}

class _InteractiveContentState extends State<_InteractiveContent> {
  // paragraph text → pre-segmented tokens
  late Map<String, List<String>> _segments;
  // flat list of unique CJK words in reading order
  late List<String> _allCjkWords;

  @override
  void initState() {
    super.initState();
    _rebuild();
  }

  @override
  void didUpdateWidget(_InteractiveContent old) {
    super.didUpdateWidget(old);
    if (old.text != widget.text) {
      _rebuild();
    }
  }

  void _rebuild() {
    _segments = _buildSegments();
    // Build ordered unique CJK word list
    final seen = <String>{};
    final words = <String>[];
    for (final tokens in _segments.values) {
      for (final t in tokens) {
        if (t.isNotEmpty && _isCJK(t.codeUnitAt(0)) && seen.add(t)) {
          words.add(t);
        }
      }
    }
    _allCjkWords = words;
  }

  Map<String, List<String>> _buildSegments() {
    final dict = DictionaryService.instance;
    final result = <String, List<String>>{};
    for (final para in widget.text.split('\n\n')) {
      final trimmed = para.trim();
      if (trimmed.isEmpty) continue;
      result[trimmed] = segmentText(trimmed, dict);
    }
    return result;
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: _segments.entries
          .map((e) => _buildParagraph(e.key, e.value))
          .toList(),
    );
  }

  Widget _buildParagraph(String para, List<String> tokens) {
    // Section heading (** ... **)
    if (para.startsWith('**') && para.contains('**')) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Text(
          para.replaceAll('**', ''),
          style: TextStyle(
            fontSize: widget.fontSize - 2,
            fontWeight: FontWeight.bold,
            color: widget.isDark ? Colors.grey[300] : Colors.grey[700],
            height: 1.6,
          ),
        ),
      );
    }

    final baseStyle = TextStyle(
      fontSize: widget.fontSize,
      height: 1.8,
      letterSpacing: 0.5,
      color: widget.isDark ? Colors.grey[200] : AppTheme.textPrimary,
    );

    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Wrap(
        spacing: 0,
        runSpacing: 0,
        children: tokens.map((token) => _buildToken(token, baseStyle)).toList(),
      ),
    );
  }

  Widget _buildToken(String token, TextStyle style) {
    final isCjkWord = token.isNotEmpty &&
        _isCJK(token.codeUnitAt(0)) &&
        DictionaryService.instance.isReady;

    if (!isCjkWord) {
      return Text(token, style: style);
    }

    return _TappableWord(
      word: token,
      style: style,
      onTap: () => widget.onWordTap(token, _allCjkWords),
    );
  }

  bool _isCJK(int code) =>
      (code >= 0x4E00 && code <= 0x9FFF) ||
      (code >= 0x3400 && code <= 0x4DBF) ||
      (code >= 0xF900 && code <= 0xFAFF) ||
      (code >= 0x3040 && code <= 0x309F) || // Hiragana
      (code >= 0x30A0 && code <= 0x30FF);   // Katakana
}

// ---------------------------------------------------------------------------

class _TappableWord extends StatefulWidget {
  final String word;
  final TextStyle style;
  final VoidCallback onTap;

  const _TappableWord({
    required this.word,
    required this.style,
    required this.onTap,
  });

  @override
  State<_TappableWord> createState() => _TappableWordState();
}

class _TappableWordState extends State<_TappableWord> {
  bool _pressed = false;

  void _copyToClipboard() {
    Clipboard.setData(ClipboardData(text: widget.word));
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Copied "${widget.word}"'),
        duration: const Duration(seconds: 1),
        behavior: SnackBarBehavior.floating,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapDown: (_) => setState(() => _pressed = true),
      onTapUp: (_) {
        setState(() => _pressed = false);
        widget.onTap();
      },
      onTapCancel: () => setState(() => _pressed = false),
      onLongPress: _copyToClipboard,
      child: Container(
        decoration: _pressed
            ? BoxDecoration(
                color: AppTheme.primary.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(3),
              )
            : null,
        child: Text(widget.word, style: widget.style),
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

  const _WordDefinitionSheet({
    required this.allWords,
    required this.initialIndex,
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
  }

  List<Widget> _buildCharBreakdown(String word, bool isDark) {
    final dict = DictionaryService.instance;
    final widgets = <Widget>[];
    for (int i = 0; i < word.length; i++) {
      final ch = word[i];
      final charEntry = dict.lookup(ch);
      widgets.add(Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              ch,
              style: TextStyle(
                fontSize: 20,
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
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Drag handle
          Center(
            child: Container(
              width: 40,
              height: 4,
              decoration: BoxDecoration(
                color: Colors.grey[400],
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ),
          const SizedBox(height: 20),

          // Word + action buttons row
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _word,
                      style: const TextStyle(
                        fontSize: 36,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    if (entry != null && entry.pinyin.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        entry.pinyin,
                        style: TextStyle(
                          fontSize: 18,
                          color: AppTheme.primary,
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    ],
                    if (entry?.hskLevel != null) ...[
                      const SizedBox(height: 8),
                      Builder(builder: (context) {
                        final lang =
                            DictionaryService.instance.activeLanguage;
                        final lvl = entry!.hskLevel!;
                        String label;
                        if (lang == Language.japanese) {
                          const jlpt = {
                            1: 'N5', 2: 'N4', 3: 'N3', 4: 'N2', 5: 'N1'
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
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        );
                      }),
                    ],
                  ],
                ),
              ),
              // Copy button
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
                icon: const Icon(Icons.copy, size: 20),
                tooltip: 'Copy',
              ),
              // Save button
              IconButton(
                onPressed: _toggleSave,
                icon: _loading
                    ? const SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : Icon(
                        _saved ? Icons.bookmark : Icons.bookmark_border,
                        color: _saved ? AppTheme.primary : null,
                        size: 28,
                      ),
                tooltip: _saved ? 'Remove from vocabulary' : 'Save to vocabulary',
              ),
            ],
          ),

          // Definitions
          if (entry != null && entry.hasDefinitions) ...[
            const SizedBox(height: 16),
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[300]),
            const SizedBox(height: 8),
            ...entry.definitions.asMap().entries.map((e) => Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${e.key + 1}. ',
                        style: TextStyle(
                          fontSize: 15,
                          color: isDark ? Colors.grey[400] : Colors.grey[600],
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      Expanded(
                        child: Text(
                          e.value,
                          style: const TextStyle(fontSize: 15, height: 1.5),
                        ),
                      ),
                    ],
                  ),
                )),
          ] else ...[
            const SizedBox(height: 16),
            // If multi-char word not found, show individual character lookups
            if (entry == null && _word.length > 1) ...[
              Divider(color: isDark ? Colors.grey[700] : Colors.grey[300]),
              const SizedBox(height: 8),
              Text(
                'Word not in dictionary. Character breakdown:',
                style: TextStyle(fontSize: 13, color: Colors.grey[500]),
              ),
              const SizedBox(height: 8),
              ..._buildCharBreakdown(_word, isDark),
            ] else
              Text(
                entry == null ? 'No dictionary entry found' : 'No definitions available',
                style: TextStyle(
                  fontSize: 14,
                  color: Colors.grey[500],
                  fontStyle: FontStyle.italic,
                ),
              ),
          ],

          // Prev / Next word navigation
          if (widget.allWords.length > 1) ...[
            const SizedBox(height: 16),
            Divider(color: isDark ? Colors.grey[700] : Colors.grey[300]),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                TextButton.icon(
                  onPressed: _hasPrev ? () => _goTo(_currentIndex - 1) : null,
                  icon: const Icon(Icons.arrow_back_ios, size: 14),
                  label: Text(
                    _hasPrev ? widget.allWords[_currentIndex - 1] : '',
                    style: const TextStyle(fontSize: 16),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Text(
                  '${_currentIndex + 1}/${widget.allWords.length}',
                  style: TextStyle(fontSize: 12, color: Colors.grey[500]),
                ),
                TextButton.icon(
                  onPressed: _hasNext ? () => _goTo(_currentIndex + 1) : null,
                  icon: Text(
                    _hasNext ? widget.allWords[_currentIndex + 1] : '',
                    style: const TextStyle(fontSize: 16),
                    overflow: TextOverflow.ellipsis,
                  ),
                  label: const Icon(Icons.arrow_forward_ios, size: 14),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
