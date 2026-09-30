import 'package:flutter/material.dart';

import '../services/korean_sentence_breakdowns.dart';

class KoreanSentenceBreakdownLink extends StatelessWidget {
  final String source;
  final String sourceText;
  final int startOffset;
  const KoreanSentenceBreakdownLink(
      {super.key,
      required this.source,
      required this.sourceText,
      required this.startOffset});

  @override
  Widget build(BuildContext context) => FutureBuilder<KoreanSentenceBreakdowns>(
        future: KoreanSentenceBreakdowns.load(),
        builder: (context, snapshot) {
          final item = snapshot.data?.at(source, sourceText, startOffset);
          if (item == null) return const SizedBox.shrink();
          return TextButton.icon(
            icon: const Icon(Icons.view_list_outlined),
            label: const Text('Break down this sentence'),
            onPressed: () => showModalBottomSheet<void>(
              context: context,
              isScrollControlled: true,
              builder: (context) => SafeArea(
                child: FractionallySizedBox(
                  heightFactor: 0.8,
                  child: ListView(padding: const EdgeInsets.all(20), children: [
                    Text('Sentence breakdown',
                        style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 12),
                    Text(item.sentence,
                        style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Text(item.translationEn),
                    const SizedBox(height: 12),
                    for (final part in item.parts)
                      ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(part.text),
                          subtitle: Text(part.explanationEn)),
                  ]),
                ),
              ),
            ),
          );
        },
      );
}
