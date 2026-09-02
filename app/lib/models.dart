enum Language { chinese, japanese }

class AgentFormStep {
  final String form;
  final String reading;
  final String label;
  final String meaningEn;

  const AgentFormStep({
    required this.form,
    required this.reading,
    required this.label,
    required this.meaningEn,
  });

  factory AgentFormStep.fromJson(Map<String, dynamic> json) => AgentFormStep(
        form: json['form'] as String,
        reading: json['reading'] as String,
        label: json['label'] as String,
        meaningEn: json['meaning_en'] as String,
      );
}

class AgentSubsegment {
  final int start;
  final int end;
  final String text;
  final String meaningEn;

  const AgentSubsegment({
    required this.start,
    required this.end,
    required this.text,
    required this.meaningEn,
  });

  factory AgentSubsegment.fromJson(Map<String, dynamic> json) =>
      AgentSubsegment(
        start: json['start'] as int,
        end: json['end'] as int,
        text: json['text'] as String,
        meaningEn: json['meaning_en'] as String? ?? '',
      );
}

class AgentSegment {
  final String text;
  final String type;
  final String pinyin;
  final String meaningEn;
  final String storyTerm;
  final String storyTermMeaningEn;
  final String storyTermImportanceEn;
  final int? targetHskLevel;
  final String hskStatus;
  final int? matchedHskLevel;
  final int? characterHskLevel;
  final String hskEvidence;
  final String learningFocus;
  final String lookupReason;
  final String focusReviewNoteEn;
  final String explanationZh;
  final String exampleZh;
  final String compositionEn;
  final List<AgentSubsegment> subsegments;
  final List<String> grammarCandidateKeys;
  final String lemma;
  final String lemmaReading;
  final String partOfSpeech;
  final String conjugationForm;
  final String storyRole;
  final String dictionaryKey;
  final String dictionaryDefinitionEn;
  final List<AgentFormStep> formSteps;

  const AgentSegment({
    required this.text,
    required this.type,
    required this.pinyin,
    required this.meaningEn,
    this.storyTerm = '',
    this.storyTermMeaningEn = '',
    this.storyTermImportanceEn = '',
    this.targetHskLevel,
    this.hskStatus = '',
    this.matchedHskLevel,
    this.characterHskLevel,
    this.hskEvidence = '',
    this.learningFocus = 'target',
    this.lookupReason = '',
    this.focusReviewNoteEn = '',
    this.explanationZh = '',
    this.exampleZh = '',
    this.compositionEn = '',
    this.subsegments = const [],
    this.grammarCandidateKeys = const [],
    this.lemma = '',
    this.lemmaReading = '',
    this.partOfSpeech = '',
    this.conjugationForm = '',
    this.storyRole = '',
    this.dictionaryKey = '',
    this.dictionaryDefinitionEn = '',
    this.formSteps = const [],
  });

  factory AgentSegment.fromJson(Map<String, dynamic> json) => AgentSegment(
        text: json['text'] as String,
        type: json['type'] as String,
        pinyin: (json['pinyin'] ?? json['reading'] ?? '') as String,
        meaningEn: json['meaning_en'] as String,
        storyTerm: json['story_term'] as String? ??
            ((json['story_role'] as String? ?? '') == 'story_term'
                ? json['text'] as String
                : ''),
        storyTermMeaningEn: json['story_term_meaning_en'] as String? ?? '',
        storyTermImportanceEn: json['story_term_importance_en'] as String? ??
            json['story_importance_en'] as String? ??
            '',
        targetHskLevel: json['target_hsk_level'] as int? ??
            json['target_curriculum_level'] as int?,
        hskStatus: json['hsk_status'] as String? ??
            json['curriculum_status'] as String? ??
            '',
        matchedHskLevel: json['matched_hsk_level'] as int? ??
            json['matched_curriculum_level'] as int?,
        characterHskLevel: json['character_hsk_level'] as int?,
        hskEvidence: json['hsk_evidence'] as String? ?? '',
        learningFocus: json['learning_focus'] as String? ?? 'target',
        lookupReason: json['lookup_reason'] as String? ?? '',
        focusReviewNoteEn: json['focus_review_note_en'] as String? ?? '',
        explanationZh: json['explanation_zh'] as String? ?? '',
        exampleZh: json['example_zh'] as String? ?? '',
        compositionEn: json['composition_en'] as String? ?? '',
        subsegments: (json['subsegments'] as List? ?? const [])
            .map((item) =>
                AgentSubsegment.fromJson(item as Map<String, dynamic>))
            .toList(),
        grammarCandidateKeys:
            (json['grammar_candidate_keys'] as List? ?? const [])
                .cast<String>(),
        lemma: json['lemma'] as String? ?? '',
        lemmaReading: json['lemma_reading'] as String? ?? '',
        partOfSpeech: json['part_of_speech'] as String? ?? '',
        conjugationForm: json['conjugation_form'] as String? ?? '',
        storyRole: json['story_role'] as String? ?? '',
        dictionaryKey: json['dictionary_key'] as String? ?? '',
        dictionaryDefinitionEn:
            json['dictionary_definition_en'] as String? ?? '',
        formSteps: (json['form_steps'] as List? ?? const [])
            .map((item) => AgentFormStep.fromJson(item as Map<String, dynamic>))
            .toList(),
      );

  bool get hasGrammar => grammarCandidateKeys.isNotEmpty;

  bool get isLookupOnly => learningFocus == 'lookup';
  bool get isStoryTerm => storyTerm.isNotEmpty || storyRole == 'story_term';
  bool get isJapaneseAnnotation => lemma.isNotEmpty;
  bool get hasChineseHelp => explanationZh.isNotEmpty || exampleZh.isNotEmpty;
  bool get hasSubsegments =>
      compositionEn.isNotEmpty && subsegments.length >= 2;
}

class AgentGrammarComponent {
  final int start;
  final int end;
  final String text;
  final String lemma;
  final String lemmaReading;
  final String functionEn;
  final String lookupKind;
  final String dictionaryKey;
  final String dictionaryDefinitionEn;

  const AgentGrammarComponent({
    required this.start,
    required this.end,
    required this.text,
    required this.lemma,
    required this.lemmaReading,
    required this.functionEn,
    this.lookupKind = 'none',
    this.dictionaryKey = '',
    this.dictionaryDefinitionEn = '',
  });

  factory AgentGrammarComponent.fromJson(Map<String, dynamic> json) =>
      AgentGrammarComponent(
        start: json['start'] as int,
        end: json['end'] as int,
        text: (json['text'] ?? json['surface']) as String,
        lemma: json['lemma'] as String,
        lemmaReading: json['lemma_kana'] as String? ?? '',
        functionEn: json['function_en'] as String? ?? '',
        lookupKind: json['lookup_kind'] as String? ?? 'none',
        dictionaryKey: json['dictionary_key'] as String? ?? '',
        dictionaryDefinitionEn:
            json['dictionary_definition_en'] as String? ?? '',
      );
}

class AgentGrammarOverlay {
  final int start;
  final int end;
  final String text;
  final String grammarCandidateKey;
  final String pattern;
  final String meaningEn;
  final String headLemma;
  final String headLemmaReading;
  final String formLabel;
  final String explanationEn;
  final List<AgentGrammarComponent> components;

  const AgentGrammarOverlay({
    required this.start,
    required this.end,
    required this.text,
    required this.grammarCandidateKey,
    required this.pattern,
    required this.meaningEn,
    this.headLemma = '',
    this.headLemmaReading = '',
    this.formLabel = '',
    this.explanationEn = '',
    this.components = const [],
  });

  factory AgentGrammarOverlay.fromJson(Map<String, dynamic> json) =>
      AgentGrammarOverlay(
        start: json['start'] as int,
        end: json['end'] as int,
        text: (json['text'] ?? json['surface']) as String,
        grammarCandidateKey: json['grammar_candidate_key'] as String,
        pattern: json['pattern'] as String,
        meaningEn: json['meaning_en'] as String,
        headLemma: json['head_lemma'] as String? ?? '',
        headLemmaReading: json['head_lemma_kana'] as String? ?? '',
        formLabel: json['form_label'] as String? ?? '',
        explanationEn: json['explanation_en'] as String? ?? '',
        components: (json['components'] as List? ?? const [])
            .map((item) =>
                AgentGrammarComponent.fromJson(item as Map<String, dynamic>))
            .toList(),
      );
}

class AgentChapterAnnotation {
  final String text;
  final List<AgentSegment> segments;
  final List<AgentGrammarOverlay> grammarOverlays;

  const AgentChapterAnnotation({
    required this.text,
    required this.segments,
    required this.grammarOverlays,
  });

  factory AgentChapterAnnotation.fromJson(Map<String, dynamic> json) =>
      AgentChapterAnnotation(
        text: json['text'] as String,
        segments: (json['segments'] as List)
            .map((item) => AgentSegment.fromJson(item as Map<String, dynamic>))
            .toList(),
        grammarOverlays: (json['grammar_overlays'] as List)
            .map((item) =>
                AgentGrammarOverlay.fromJson(item as Map<String, dynamic>))
            .toList(),
      );
}

/// A tightly bound Japanese inflection rendered as one reader tap target.
///
/// The source annotation keeps morphemes separate for dictionary access.  This
/// view layer joins only predicate-sized grammar overlays; the original pieces
/// remain available through [grammar.components] in the explanation sheet.
class JapaneseDisplayUnit {
  final int firstSegment;
  final int lastSegment;
  final int start;
  final int end;
  final AgentSegment segment;
  final AgentGrammarOverlay grammar;

  const JapaneseDisplayUnit({
    required this.firstSegment,
    required this.lastSegment,
    required this.start,
    required this.end,
    required this.segment,
    required this.grammar,
  });
}

/// Grammar cards for an unmerged Japanese segment belong on their lexical
/// head, not on every word that merely overlaps a wider explanatory span.
/// This keeps a clause-level audit overlay from appearing when the reader taps
/// an ordinary subject or object while still exposing the explanation on the
/// inflected predicate or grammar unit it describes.
List<AgentGrammarOverlay> japaneseGrammarForSegment({
  required AgentSegment segment,
  required int start,
  required int end,
  required List<AgentGrammarOverlay> overlays,
}) =>
    overlays.where((item) {
      if (!(item.start < end && start < item.end)) return false;
      return (item.start == start && item.end == end) ||
          segment.lemma == item.headLemma ||
          segment.text == item.headLemma;
    }).toList();

List<JapaneseDisplayUnit> buildJapaneseDisplayUnits(
  AgentChapterAnnotation annotation,
) {
  final starts = <int>[];
  final ends = <int>[];
  var offset = 0;
  for (final segment in annotation.segments) {
    starts.add(offset);
    offset += segment.text.length;
    ends.add(offset);
  }

  final candidates = <JapaneseDisplayUnit>[];
  for (final original in annotation.grammarOverlays) {
    var covered = <int>[
      for (var i = 0; i < annotation.segments.length; i++)
        if (starts[i] >= original.start && ends[i] <= original.end) i,
    ];
    if (covered.length < 2 ||
        starts[covered.first] != original.start ||
        ends[covered.last] != original.end ||
        covered.any((i) => annotation.segments[i].type == 'punctuation')) {
      continue;
    }

    final description = [
      original.grammarCandidateKey,
      original.formLabel,
      original.pattern,
      original.headLemma,
    ].join(' ').toLowerCase();
    final isBenefactive = description.contains('benefactive') ||
        description.contains('morau') ||
        const {'もらう', 'くれる', 'あげる', 'やる'}.contains(original.headLemma);
    final explanation = original.explanationEn.toLowerCase();
    final isCollocation = description.contains('collocation') ||
        description.contains('fixed expression') ||
        explanation.contains('collocation') ||
        explanation.contains('fixed expression') ||
        explanation.contains('conventional expression') ||
        original.pattern.contains('お腹がすく');
    final isConnectiveNaku = description.contains('n-ga-naku') ||
        (original.text.endsWith('がなく') &&
            original.headLemma.endsWith('がない'));
    var grammar = original;
    if (isBenefactive &&
        covered.first > 0 &&
        const {'て', 'で'}.contains(annotation.segments[covered.first].text) &&
        ends[covered.first - 1] == original.start &&
        const {'word', 'idiom'}
            .contains(annotation.segments[covered.first - 1].type)) {
      final action = annotation.segments[covered.first - 1];
      final prefixLength = action.text.length;
      covered = [covered.first - 1, ...covered];
      grammar = AgentGrammarOverlay(
        start: starts[covered.first],
        end: original.end,
        text: action.text + original.text,
        grammarCandidateKey: original.grammarCandidateKey,
        pattern: 'V${original.pattern}',
        meaningEn: original.meaningEn,
        headLemma: action.lemma,
        headLemmaReading: action.lemmaReading,
        formLabel: original.formLabel,
        explanationEn: original.explanationEn,
        components: [
          AgentGrammarComponent(
            start: 0,
            end: prefixLength,
            text: action.text,
            lemma: action.lemma,
            lemmaReading: action.lemmaReading,
            functionEn: 'main action receiving the favor',
          ),
          ...original.components.map(
            (component) => AgentGrammarComponent(
              start: component.start + prefixLength,
              end: component.end + prefixLength,
              text: component.text,
              lemma: component.lemma,
              lemmaReading: component.lemmaReading,
              functionEn: component.functionEn,
            ),
          ),
        ],
      );
    }

    final contentCount = covered.where((i) {
      return const {'word', 'name', 'idiom'}
          .contains(annotation.segments[i].type);
    }).length;
    final firstType = annotation.segments[covered.first].type;
    // A grammar overlay may explain a wider clause, but that must not turn an
    // ordinary topic sentence (for example 吾輩は猫である) into one giant tap
    // target.  Normal grammar units contain one lexical head; only reviewed
    // benefactive chains and genuine collocations may join two content words.
    if ((!isBenefactive && !isCollocation && !isConnectiveNaku &&
            contentCount > 1) ||
        (isBenefactive && contentCount > 2) ||
        (isCollocation && contentCount > 2) ||
        (isConnectiveNaku && contentCount > 2) ||
        firstType == 'particle' ||
        grammar.text.length > 18) {
      continue;
    }

    final parts = [for (final i in covered) annotation.segments[i]];
    final head = parts.firstWhere(
      (segment) => segment.lemma == grammar.headLemma,
      orElse: () => parts.firstWhere(
        (segment) => segment.type != 'particle' && segment.type != 'auxiliary',
        orElse: () => parts.first,
      ),
    );
    final surface = parts.map((segment) => segment.text).join();
    final merged = AgentSegment(
      text: surface,
      type: isCollocation ? 'idiom' : head.type,
      pinyin: parts.map((segment) => segment.pinyin).join(),
      meaningEn: grammar.meaningEn,
      storyTerm: head.storyTerm,
      storyTermMeaningEn: head.storyTermMeaningEn,
      storyTermImportanceEn: head.storyTermImportanceEn,
      targetHskLevel: head.targetHskLevel,
      hskStatus: head.hskStatus,
      matchedHskLevel: head.matchedHskLevel,
      characterHskLevel: head.characterHskLevel,
      hskEvidence: head.hskEvidence,
      learningFocus: head.learningFocus,
      lookupReason: head.lookupReason,
      focusReviewNoteEn: head.focusReviewNoteEn,
      grammarCandidateKeys: [grammar.grammarCandidateKey],
      lemma: grammar.headLemma,
      lemmaReading: grammar.headLemmaReading,
      partOfSpeech: isCollocation ? 'fixed expression' : head.partOfSpeech,
      conjugationForm: grammar.formLabel,
      storyRole: head.storyRole,
      // A grammar overlay's head lemma can cover a larger expression than
      // its first lexical segment.  Reusing that segment's key would make a
      // label such as お腹がすく open the unrelated お腹 entry.  Overlay
      // components retain their independently verified dictionary links; the
      // whole overlay remains non-clickable until it has its own canonical
      // dictionary key.
      dictionaryKey: '',
      dictionaryDefinitionEn: '',
    );
    candidates.add(JapaneseDisplayUnit(
      firstSegment: covered.first,
      lastSegment: covered.last,
      start: starts[covered.first],
      end: ends[covered.last],
      segment: merged,
      grammar: grammar,
    ));
  }

  // Prefer the longest useful unit at a shared start, then discard overlaps.
  candidates.sort((a, b) {
    final byStart = a.firstSegment.compareTo(b.firstSegment);
    return byStart != 0
        ? byStart
        : (b.lastSegment - b.firstSegment)
            .compareTo(a.lastSegment - a.firstSegment);
  });
  final accepted = <JapaneseDisplayUnit>[];
  var nextFree = 0;
  for (final candidate in candidates) {
    if (candidate.firstSegment < nextFree) continue;
    accepted.add(candidate);
    nextFree = candidate.lastSegment + 1;
  }
  return accepted;
}

class Chapter {
  final String title;
  final String content;
  final String annotationAsset;

  Chapter({
    required this.title,
    required this.content,
    required this.annotationAsset,
  });

  factory Chapter.fromJson(Map<String, dynamic> json) {
    return Chapter(
      title: json['title'] as String,
      content: json['content'] as String,
      annotationAsset: json['annotationAsset'] as String? ?? '',
    );
  }
}

class Reader {
  final String id;
  final String book;
  final String bookTitle;
  final String bookTitleEn;
  final int level;
  final Language language;
  final List<Chapter> chapters;

  Reader({
    required this.id,
    required this.book,
    required this.bookTitle,
    required this.bookTitleEn,
    required this.level,
    required this.language,
    required this.chapters,
  });

  factory Reader.fromJson(Map<String, dynamic> json, Language language) {
    return Reader(
      id: json['id'] as String,
      book: json['book'] as String,
      bookTitle: json['bookTitle'] as String,
      bookTitleEn: json['bookTitleEn'] as String,
      level: json['level'] as int,
      language: language,
      chapters: (json['chapters'] as List)
          .map((c) => Chapter.fromJson(c as Map<String, dynamic>))
          .toList(),
    );
  }

  String get levelLabel {
    switch (language) {
      case Language.chinese:
        return 'HSK $level';
      case Language.japanese:
        const jlptLabels = {1: 'N5', 2: 'N4', 3: 'N3', 4: 'N2', 5: 'N1'};
        return 'JLPT ${jlptLabels[level] ?? level}';
    }
  }

  int get maxLevel => language == Language.chinese ? 6 : 5;
}

class Book {
  final String key;
  final String title;
  final String titleEn;
  final Map<int, Reader> levels;

  Book({
    required this.key,
    required this.title,
    required this.titleEn,
    required this.levels,
  });
}
