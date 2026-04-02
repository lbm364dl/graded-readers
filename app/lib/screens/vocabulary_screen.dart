import 'package:flutter/material.dart';
import '../services/vocabulary_service.dart';
import '../theme.dart';

class VocabularyScreen extends StatefulWidget {
  const VocabularyScreen({super.key});

  @override
  State<VocabularyScreen> createState() => _VocabularyScreenState();
}

class _VocabularyScreenState extends State<VocabularyScreen> {
  List<SavedWord>? _words;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final words = await VocabularyService.instance.loadWords();
    if (!mounted) return;
    setState(() => _words = words);
  }

  Future<void> _remove(String word) async {
    await VocabularyService.instance.removeWord(word);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final words = _words;

    if (words == null) {
      return const Center(child: CircularProgressIndicator());
    }

    if (words.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.bookmark_border, size: 64, color: Colors.grey[400]),
            const SizedBox(height: 16),
            Text(
              'No saved vocabulary yet',
              style: TextStyle(fontSize: 16, color: Colors.grey[500]),
            ),
            const SizedBox(height: 8),
            Text(
              'Tap words while reading, then tap the bookmark to save',
              style: TextStyle(fontSize: 13, color: Colors.grey[400]),
            ),
          ],
        ),
      );
    }

    return Column(
      children: [
        // Stats + flashcard button
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Row(
            children: [
              Text(
                '${words.length} words',
                style: TextStyle(
                  fontSize: 14,
                  color: Colors.grey[600],
                ),
              ),
              const Spacer(),
              FilledButton.icon(
                onPressed: words.isEmpty
                    ? null
                    : () => Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) =>
                                FlashcardScreen(words: List.of(words)),
                          ),
                        ).then((_) => _load()),
                icon: const Icon(Icons.school, size: 18),
                label: const Text('Review'),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            itemCount: words.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (context, i) => _WordTile(
              word: words[i],
              onRemove: () => _remove(words[i].word),
            ),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------

class _WordTile extends StatelessWidget {
  final SavedWord word;
  final VoidCallback onRemove;

  const _WordTile({required this.word, required this.onRemove});

  @override
  Widget build(BuildContext context) {
    return Dismissible(
      key: ValueKey(word.word),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.only(right: 20),
        color: Colors.red[400],
        child: const Icon(Icons.delete_outline, color: Colors.white),
      ),
      onDismissed: (_) => onRemove(),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Row(
          children: [
            // Word + pinyin
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text(
                        word.word,
                        style: const TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Text(
                        word.pinyin,
                        style: TextStyle(
                          fontSize: 14,
                          color: AppTheme.primary,
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    ],
                  ),
                  if (word.definitions.isNotEmpty) ...[
                    const SizedBox(height: 2),
                    Text(
                      word.definitions.first,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 13,
                        color: Colors.grey[600],
                      ),
                    ),
                  ],
                ],
              ),
            ),
            if (word.isLearned)
              Icon(Icons.check_circle_outline,
                  size: 20, color: Colors.green[400]),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Flashcard review screen
// ---------------------------------------------------------------------------

class FlashcardScreen extends StatefulWidget {
  final List<SavedWord> words;

  const FlashcardScreen({super.key, required this.words});

  @override
  State<FlashcardScreen> createState() => _FlashcardScreenState();
}

class _FlashcardScreenState extends State<FlashcardScreen> {
  late final List<SavedWord> _deck;
  int _index = 0;
  bool _revealed = false;
  int _knownCount = 0;

  @override
  void initState() {
    super.initState();
    _deck = [
      ...widget.words.where((w) => !w.isLearned),
      ...widget.words.where((w) => w.isLearned),
    ];
  }

  bool get _done => _index >= _deck.length;

  void _reveal() => setState(() => _revealed = true);

  Future<void> _answer({required bool known}) async {
    final word = _deck[_index];
    if (known) {
      _knownCount++;
      await VocabularyService.instance.markLearned(word.word, learned: true);
    } else {
      _deck.add(word);
      await VocabularyService.instance.markLearned(word.word, learned: false);
    }
    if (!mounted) return;
    setState(() {
      _index++;
      _revealed = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: _done
            ? const Text('Review Complete')
            : Text('${_index + 1} / ${_deck.length}'),
      ),
      body: _done ? _buildSummary() : _buildCard(),
    );
  }

  Widget _buildCard() {
    final word = _deck[_index];
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        children: [
          LinearProgressIndicator(
            value: _index / _deck.length,
            backgroundColor:
                isDark ? Colors.grey[800] : Colors.grey[200],
          ),
          const Spacer(),
          Text(
            word.word,
            style: const TextStyle(
              fontSize: 64,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 24),
          AnimatedOpacity(
            opacity: _revealed ? 1.0 : 0.0,
            duration: const Duration(milliseconds: 200),
            child: Column(
              children: [
                if (word.pinyin.isNotEmpty)
                  Text(
                    word.pinyin,
                    style: TextStyle(
                      fontSize: 22,
                      color: AppTheme.primary,
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                const SizedBox(height: 16),
                ...word.definitions.take(3).map((d) => Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Text(
                        d,
                        style: TextStyle(
                          fontSize: 16,
                          color: isDark
                              ? Colors.grey[300]
                              : Colors.grey[700],
                        ),
                        textAlign: TextAlign.center,
                      ),
                    )),
              ],
            ),
          ),
          const Spacer(),
          if (!_revealed)
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: _reveal,
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                ),
                child:
                    const Text('Show Answer', style: TextStyle(fontSize: 16)),
              ),
            )
          else
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => _answer(known: false),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      side: BorderSide(color: Colors.red[300]!),
                      foregroundColor: Colors.red[400],
                    ),
                    child: const Text("Don't Know",
                        style: TextStyle(fontSize: 15)),
                  ),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: FilledButton(
                    onPressed: () => _answer(known: true),
                    style: FilledButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      backgroundColor: Colors.green[600],
                    ),
                    child:
                        const Text('Know It', style: TextStyle(fontSize: 15)),
                  ),
                ),
              ],
            ),
          const SizedBox(height: 16),
        ],
      ),
    );
  }

  Widget _buildSummary() {
    final total = widget.words.length;
    final pct = total > 0 ? (_knownCount / total * 100).round() : 0;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              pct >= 80 ? Icons.star : Icons.school,
              size: 72,
              color: pct >= 80 ? Colors.amber : AppTheme.primary,
            ),
            const SizedBox(height: 24),
            Text(
              '$_knownCount / $total',
              style: const TextStyle(
                  fontSize: 48, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            Text(
              'You knew $pct% of the words',
              style: TextStyle(fontSize: 16, color: Colors.grey[600]),
            ),
            const SizedBox(height: 40),
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Done'),
            ),
          ],
        ),
      ),
    );
  }
}
