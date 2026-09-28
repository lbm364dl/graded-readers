import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../data.dart';
import '../models.dart';
import '../services/usage_dictionary.dart';
import '../services/grammar_dictionary.dart';
import 'grammar_dictionary_screen.dart';
import 'reader_screen.dart';

class UsageDictionaryScreen extends StatefulWidget {
  final Language language;
  const UsageDictionaryScreen({super.key, this.language = Language.chinese});
  @override
  State<UsageDictionaryScreen> createState() => _UsageDictionaryScreenState();
}

class _UsageDictionaryScreenState extends State<UsageDictionaryScreen> {
  late final _dictionary = UsageDictionary.load(language: widget.language);
  String _query = '';

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Reading dictionary')),
        body: Column(children: [
          FutureBuilder<UsageDictionary>(
              future: _dictionary,
              builder: (context, snapshot) {
                final levels = widget.language == Language.japanese
                    ? (snapshot.data?.sources.values
                            .map((source) => source['level'] as String)
                            .toSet()
                            .toList() ??
                        <String>[])
                    : <String>[];
                levels.sort((a, b) => int.parse(b.substring(1))
                    .compareTo(int.parse(a.substring(1))));
                return Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(widget.language == Language.japanese
                        ? levels.isEmpty
                            ? 'Japanese dictionary'
                            : 'Japanese dictionary · JLPT ${levels.join(', ')}'
                        : snapshot.data?.sources.values.any((source) =>
                                    source['dictionary_coverage']?['scope'] ==
                                    'sample') ==
                                true
                            ? 'Words and meanings from HSK 1 + an HSK 2 excerpt'
                            : 'Words and meanings from our reading texts'));
              }),
          Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: TextField(
                decoration: InputDecoration(
                    labelText: widget.language == Language.japanese
                        ? 'Search words, kana, or meanings'
                        : 'Search words, pinyin, or meanings',
                    hintText: widget.language == Language.japanese
                        ? 'e.g. みる or 見る'
                        : 'e.g. hen3 duo1',
                    prefixIcon: Icon(Icons.search)),
                onChanged: (value) => setState(() => _query = value),
              )),
          Expanded(
              child: FutureBuilder<UsageDictionary>(
                  future: _dictionary,
                  builder: (context, snapshot) {
                    if (snapshot.hasError) {
                      return const Center(
                          child: Text('Dictionary could not be loaded.'));
                    }
                    if (!snapshot.hasData) {
                      return const Center(child: CircularProgressIndicator());
                    }
                    final dictionary = snapshot.data!;
                    final entries = dictionary.search(_query);
                    if (entries.isEmpty) {
                      return const Center(
                          child:
                              Text('No matching words in this reading yet.'));
                    }
                    return ListView.builder(
                        itemCount: entries.length,
                        itemBuilder: (context, index) {
                          final entry = entries[index];
                          return ListTile(
                              title: Text(
                                  '${entry['headword']}  ${entry['reading']}'),
                              subtitle: Text((entry['senses'] as List)
                                  .map((s) => s['definition'])
                                  .join('; ')),
                              trailing: const Icon(Icons.chevron_right),
                              onTap: () => Navigator.of(context).push(
                                  MaterialPageRoute<void>(
                                      builder: (_) => UsageEntryScreen(
                                          dictionary: dictionary,
                                          entry: entry))));
                        });
                  })),
        ]),
      );
}

class UsageEntryScreen extends StatelessWidget {
  final UsageDictionary dictionary;
  final Map<String, dynamic> entry;

  /// Set only by an occurrence link from a reading passage.
  final String? selectedSense;
  const UsageEntryScreen(
      {super.key,
      required this.dictionary,
      required this.entry,
      this.selectedSense});

  Future<void> _openSource(
      BuildContext context, Map<String, dynamic> use) async {
    try {
      final source = dictionary.sources[use['source']];
      final readers =
          await ContentRepository().loadReaders(dictionary.language);
      final reader = readers.singleWhere((r) => r.id == source['reader_id']);
      final chapter = (source['chapter'] as int) - 1;
      if (reader.chapters[chapter].content != source['text']) {
        throw StateError('Source edition has changed');
      }
      // Stored offsets count Unicode code points; Flutter strings use UTF-16.
      final offset = String.fromCharCodes(
              (source['text'] as String).runes.take(use['start'] as int))
          .length;
      if (!context.mounted) return;
      await Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => ReaderScreen(
              reader: reader,
              initialChapter: chapter,
              initialCharacterOffset: offset)));
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
            content: Text('This example’s source edition is unavailable.')));
      }
    }
  }

  @override
  Widget build(BuildContext context) => dictionary.isGrammarOnly(entry)
      ? FutureBuilder<GrammarDictionary>(
          future: GrammarDictionary.load(),
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return const Scaffold(
                  body: Center(
                      child: Text('Grammar dictionary could not be loaded.')));
            }
            if (!snapshot.hasData) {
              return const Scaffold(
                  body: Center(child: CircularProgressIndicator()));
            }
            final ids = (snapshot.data!.wordRoutes[entry['id']] as List? ?? [])
                .cast<String>();
            if (ids.length == 1) {
              return GrammarEntryScreen(
                  dictionary: snapshot.data!,
                  entry: snapshot.data!.entry(ids.single));
            }
            return GrammarDictionaryScreen(entryIds: ids);
          })
      : Scaffold(
          appBar: AppBar(title: Text(entry['headword'] as String)),
          body: ListView(padding: const EdgeInsets.all(20), children: [
            Text(entry['reading'] as String,
                style: Theme.of(context).textTheme.titleLarge),
            Text(
                '${entry['kind'] == 'construction' ? 'Grammar construction' : entry['kind']}${entry['origin'] == 'component_word' ? ' · used within other words' : ''}'),
            if (entry['kind'] == 'construction')
              const Text(
                  'A reusable grammatical pattern, not an indivisible word.'),
            if (entry['character_ref'] != null)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  key: const ValueKey('entry-character-link'),
                  icon: const Icon(Icons.history_edu),
                  label: Text(dictionary.language == Language.japanese
                      ? 'Japanese character dictionary'
                      : 'Character & etymology'),
                  onPressed: () => showDictionaryCharacter(
                      context, entry['character_ref']['character'] as String,
                      language: dictionary.language),
                ),
              ),
            const SizedBox(height: 16),
            for (final sense in entry['senses'] as List)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (sense['id'] == selectedSense) ...[
                        Text('Used in this passage',
                            style: Theme.of(context)
                                .textTheme
                                .labelSmall
                                ?.copyWith(
                                    color:
                                        Theme.of(context).colorScheme.primary)),
                        const SizedBox(height: 4),
                      ],
                      Text(
                          '${sense['parent_entry_id'] == null ? '' : 'In ${dictionary.entry(sense['parent_entry_id'] as String)['headword']}: '}'
                          '${sense['definition']}',
                          style: Theme.of(context).textTheme.titleMedium),
                    ]),
              ),
            const SizedBox(height: 16),
            if (entry['meaning_guide'] != null) ...[
              _meaningGuide(
                  context, entry['meaning_guide'] as Map<String, dynamic>),
              const SizedBox(height: 20),
            ],
            if (entry['base_entry_id'] != null) ...[
              const Text('Based on'),
              for (final component in entry['components'] as List)
                _relatedEntry(
                    context,
                    dictionary.entry(component['entry_id'] as String),
                    component['role'] as String),
              const SizedBox(height: 16),
            ],
            if (dictionary
                .expressionsFor(entry['id'] as String)
                .isNotEmpty) ...[
              Text('Expressions with ${entry['headword']}'),
              for (final related
                  in dictionary.expressionsFor(entry['id'] as String))
                _relatedEntry(
                    context,
                    related,
                    (related['senses'] as List)
                        .map((s) => s['definition'])
                        .join('; ')),
              const SizedBox(height: 16),
            ],
            if ((entry['component_uses'] as List? ?? []).isNotEmpty) ...[
              Text('Used in', style: Theme.of(context).textTheme.titleMedium),
              for (final use in entry['component_uses'] as List)
                _relatedEntry(
                    context,
                    dictionary.entry(use['parent_entry_id'] as String),
                    use['contribution_en'] as String),
              const SizedBox(height: 16),
            ],
            for (final sense in entry['senses'] as List) ...[
              if (entry['origin'] != 'component_word') ...[
                Text(
                    (entry['senses'] as List).length == 1
                        ? 'Examples from our texts'
                        : 'Examples · ${sense['definition']}',
                    style: Theme.of(context).textTheme.titleMedium),
                for (final use in dictionary.occurrences
                    .where((o) => o['sense_id'] == sense['id']))
                  Card(
                      child: ListTile(
                    key: ValueKey('dictionary-example-${use['id']}'),
                    title: _example(context, use),
                    subtitle: Text('${use['reading']} · ${use['gloss']}\n'
                        '${dictionary.sources[use['source']]['title']} · ${dictionary.language == Language.japanese ? 'JLPT' : 'HSK'} ${dictionary.sources[use['source']]['level']} · Chapter ${dictionary.sources[use['source']]['chapter']}'),
                    isThreeLine: true,
                    trailing: const Icon(Icons.open_in_new),
                    onTap: () => _openSource(context, use),
                  )),
                const SizedBox(height: 20),
              ],
            ],
          ]),
        );

  Widget _meaningGuide(BuildContext context, Map<String, dynamic> guide) =>
      Container(
        key: const ValueKey('dictionary-meaning-guide'),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.surfaceContainerLow,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('How this word makes sense',
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          Text(guide['explanation_en'] as String,
              style: const TextStyle(height: 1.5)),
          for (final part in guide['parts'] as List) ...[
            const SizedBox(height: 10),
            if (part['entry_id'] != null || part['character_ref'] != null)
              Material(
                type: MaterialType.transparency,
                child: ListTile(
                  contentPadding: EdgeInsets.zero,
                  key: ValueKey('meaning-part-${part['start']}'),
                  title: Text('${part['text']} — ${part['contribution_en']}'),
                  trailing: const Icon(Icons.chevron_right),
                  subtitle: part['character_ref'] != null
                      ? Text(dictionary.language == Language.japanese
                          ? 'Japanese character dictionary'
                          : 'Character · etymology')
                      : dictionary.language == Language.japanese
                          ? Text(
                              '${dictionary.isGrammarOnly(dictionary.entry(part['entry_id'] as String)) ? 'Related grammar entry' : 'Related dictionary entry'} · '
                              '${dictionary.entry(part['entry_id'] as String)['reading']}')
                          : null,
                  onTap: () => part['character_ref'] != null
                      ? showDictionaryCharacter(
                          context, part['character_ref']['character'] as String,
                          language: dictionary.language)
                      : Navigator.of(context).push(MaterialPageRoute<void>(
                          builder: (_) => UsageEntryScreen(
                              dictionary: dictionary,
                              entry: dictionary
                                  .entry(part['entry_id'] as String)))),
                ),
              )
            else
              Text.rich(
                  TextSpan(children: [
                    TextSpan(
                        text: '${part['text']} — ',
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    TextSpan(text: part['contribution_en'] as String),
                  ]),
                  style: const TextStyle(height: 1.5)),
            for (final id in (part['grammar_entry_ids'] as List? ?? const [])
                .cast<String>())
              FutureBuilder<GrammarDictionary>(
                future: GrammarDictionary.load(),
                builder: (context, snapshot) {
                  if (!snapshot.hasData) return const SizedBox.shrink();
                  final lesson = snapshot.data!.entry(id);
                  return TextButton.icon(
                    key: ValueKey('meaning-part-grammar-${part['start']}-$id'),
                    icon: const Icon(Icons.account_tree_outlined),
                    label: Text('Grammar · ${lesson['title']}'),
                    onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                            builder: (_) => GrammarEntryScreen(
                                dictionary: snapshot.data!, entry: lesson))),
                  );
                },
              ),
          ],
          if ((guide['caveat_en'] as String).isNotEmpty) ...[
            const SizedBox(height: 12),
            Text(guide['caveat_en'] as String,
                style:
                    const TextStyle(fontStyle: FontStyle.italic, height: 1.5)),
          ],
          if (guide['research'] != null)
            _researchEvidence(
                context, guide['research'] as Map<String, dynamic>),
        ]),
      );

  Widget _researchEvidence(
          BuildContext context, Map<String, dynamic> research) =>
      Material(
        type: MaterialType.transparency,
        child: ExpansionTile(
          key: const ValueKey('dictionary-research-evidence'),
          tilePadding: EdgeInsets.zero,
          title: const Text('Sources and research notes'),
          children: [
            for (final claim in research['claims'] as List)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(
                    '${claim['text_en']} [${(claim['source_ids'] as List).join(', ')}]'),
              ),
            for (final gap in research['gaps'] as List)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text('Not established by this research: $gap'),
              ),
            for (final source in research['sources'] as List)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${source['id']} · ${source['title']}'),
                      Text(source['summary_en'] as String),
                      SelectableText(source['url'] as String),
                      Text('Consulted ${source['accessed_at']}'),
                      TextButton.icon(
                        icon: const Icon(Icons.copy, size: 16),
                        label: const Text('Copy source link'),
                        onPressed: () async {
                          await Clipboard.setData(
                              ClipboardData(text: source['url'] as String));
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(
                                    content: Text('Source link copied')));
                          }
                        },
                      ),
                    ]),
              ),
          ],
        ),
      );

  Widget _relatedEntry(BuildContext context, Map<String, dynamic> related,
          String explanation) =>
      ListTile(
        key: ValueKey('related-entry-${related['id']}'),
        title: Text('${related['headword']}  ${related['reading']}'),
        subtitle: Text(explanation),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => Navigator.of(context).push(MaterialPageRoute<void>(
            builder: (_) =>
                UsageEntryScreen(dictionary: dictionary, entry: related))),
      );

  Widget _example(BuildContext context, Map<String, dynamic> use) {
    final chars = (use['sentence'] as String).runes.toList();
    final start = (use['start'] as int) - (use['sentence_start'] as int);
    final end = (use['end'] as int) - (use['sentence_start'] as int);
    return Text.rich(TextSpan(children: [
      TextSpan(text: String.fromCharCodes(chars.take(start))),
      TextSpan(
          text: String.fromCharCodes(chars.sublist(start, end)),
          style: TextStyle(
              fontWeight: FontWeight.bold,
              color: Theme.of(context).colorScheme.primary)),
      TextSpan(text: String.fromCharCodes(chars.skip(end))),
    ]));
  }
}

class UsageDictionaryLink extends StatelessWidget {
  final Language language;
  final int? startOffset;
  final String? surface;
  final String? reading;
  final String? gloss;
  final String source;
  final int segmentIndex;
  final String sourceText;
  const UsageDictionaryLink(
      {super.key,
      this.language = Language.chinese,
      this.startOffset,
      this.surface,
      this.reading,
      this.gloss,
      required this.source,
      required this.segmentIndex,
      required this.sourceText});

  @override
  Widget build(BuildContext context) => FutureBuilder<UsageDictionary>(
      future: UsageDictionary.load(language: language),
      builder: (context, snapshot) {
        if (!snapshot.hasData) return const SizedBox.shrink();
        final dictionary = snapshot.data!;
        final matchedUse = dictionary.occurrence(
            source, segmentIndex, sourceText,
            startOffset: startOffset,
            surface: surface,
            reading: reading,
            gloss: gloss);
        if (matchedUse == null) return const SizedBox.shrink();
        var use = matchedUse;
        var entry =
            dictionary.entries.singleWhere((e) => e['id'] == use['entry_id']);
        if (dictionary.isGrammarOnly(entry)) {
          // A merged productive pattern can still contain a lexical verb.
          // Keep that verb link alongside grammar; do not invent a phrase word.
          if (entry['kind'] != 'construction' ||
              startOffset == null ||
              surface == null ||
              surface == use['surface']) {
            return const SizedBox.shrink();
          }
          final start = sourceText.substring(0, startOffset!).runes.length;
          final end = start + surface!.runes.length;
          final lexical = dictionary.occurrences
              .where((o) =>
                  o['source'] == source &&
                  o['layer'] != 'expression' &&
                  (o['start'] as int) >= start &&
                  (o['end'] as int) <= end &&
                  !dictionary
                      .isGrammarOnly(dictionary.entry(o['entry_id'] as String)))
              .toList();
          if (lexical.length != 1) return const SizedBox.shrink();
          use = lexical.single;
          entry = dictionary.entry(use['entry_id'] as String);
        }
        final count = dictionary.occurrences
            .where((o) => o['entry_id'] == entry['id'])
            .length;
        return TextButton.icon(
            icon: const Icon(Icons.menu_book_outlined),
            label: Text(
                'Dictionary · ${surface != null && surface != entry['headword'] ? '${entry['headword']} · ' : ''}'
                '$count ${count == 1 ? 'usage' : 'usages'}'),
            onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(
                builder: (_) => UsageEntryScreen(
                    dictionary: dictionary,
                    entry: entry,
                    selectedSense: use['sense_id'] as String))));
      });
}
