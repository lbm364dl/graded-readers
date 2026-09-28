import 'package:flutter/material.dart';
import '../models.dart';
import '../services/grammar_dictionary.dart';

/// Prefer the reviewed contextual function/pronunciation over coarse source
/// glosses such as "but; subject marker". Source identity remains unchanged.
class JapaneseSegmentText extends StatelessWidget {
  final AgentSegment segment;
  final String source, sourceText;
  final int segmentIndex, startOffset;
  final bool reading;
  final TextStyle? style;
  const JapaneseSegmentText(
      {super.key,
      required this.segment,
      required this.source,
      required this.sourceText,
      required this.segmentIndex,
      required this.startOffset,
      this.reading = false,
      this.style});
  @override
  Widget build(BuildContext context) => FutureBuilder<GrammarDictionary>(
      future: GrammarDictionary.load(),
      builder: (context, snapshot) {
        final lesson = segment.type == 'particle'
            ? snapshot.data?.particleLesson(
                source, sourceText, startOffset, segment.text, segmentIndex)
            : null;
        final value = reading
            ? ((lesson?['reading'] as String?) ?? segment.pinyin)
            : ((lesson?['summary_en'] as String?) ?? segment.meaningEn);
        return Text(value, style: style);
      });
}

/// Occurrence notes stay separate from independent lessons and form chains.
/// Unpublished legacy lexical overlays must not become fake grammar cards.
class JapaneseGrammarContext extends StatelessWidget {
  final String source, sourceText;
  final List<AgentGrammarOverlay> overlays;
  const JapaneseGrammarContext(
      {super.key,
      required this.source,
      required this.sourceText,
      required this.overlays});

  @override
  Widget build(BuildContext context) => FutureBuilder<GrammarDictionary>(
        future: GrammarDictionary.load(),
        builder: (context, snapshot) {
          if (!snapshot.hasData) return const SizedBox.shrink();
          final notes = <String, String>{};
          for (final overlay in overlays) {
            for (final use in snapshot.data!
                .uses(source, sourceText, overlay.start, overlay.text)) {
              if (use['layer'] == 'overlay' && use['surface'] == overlay.text) {
                notes.putIfAbsent(
                    overlay.text, () => use['context_en'] as String);
              }
            }
          }
          if (notes.isEmpty) return const SizedBox.shrink();
          return Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('In this phrase',
                      style: Theme.of(context).textTheme.labelMedium),
                  for (final note in notes.entries) ...[
                    const SizedBox(height: 4),
                    Text(note.key,
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    Text(note.value,
                        style: const TextStyle(fontSize: 13, height: 1.4)),
                  ],
                ],
              ));
        },
      );
}
