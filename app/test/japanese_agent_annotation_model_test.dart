import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/models.dart';

void main() {
  test('parses explicit dictionary links and ordered Japanese form steps', () {
    final segment = AgentSegment.fromJson({
      'text': '怖くなかった',
      'type': 'word',
      'reading': 'こわくなかった',
      'meaning_en': 'was not scary',
      'lemma': '怖い',
      'lemma_reading': 'こわい',
      'dictionary_key': '怖い',
      'dictionary_definition_en': 'scary, frightening',
      'form_steps': [
        {
          'form': '怖くない',
          'reading': 'こわくない',
          'label': 'plain negative',
          'meaning_en': 'not scary',
        },
        {
          'form': '怖くなかった',
          'reading': 'こわくなかった',
          'label': 'plain negative past',
          'meaning_en': 'was not scary',
        },
      ],
    });
    expect(segment.dictionaryKey, '怖い');
    expect(segment.dictionaryDefinitionEn, 'scary, frightening');
    expect(
      segment.formSteps.map((step) => step.form),
      ['怖くない', '怖くなかった'],
    );

    final lexical = AgentGrammarComponent.fromJson({
      'start': 0,
      'end': 2,
      'surface': '動き',
      'lemma': '動く',
      'lemma_kana': 'うごく',
      'function_en': 'movement verb',
      'lookup_kind': 'lexical',
      'dictionary_key': '動く',
      'dictionary_definition_en': 'to move',
    });
    expect(lexical.lookupKind, 'lexical');
    expect(lexical.dictionaryKey, '動く');
  });

  test('groups a learned construction made from learner-sized segments', () {
    final annotation = AgentChapterAnnotation.fromJson({
      'text': '歩くことにしました',
      'segments': [
        {
          'text': '歩く',
          'type': 'word',
          'reading': 'あるく',
          'lemma': '歩く',
          'lemma_reading': 'あるく',
          'part_of_speech': 'verb',
          'conjugation_form': 'plain nonpast',
          'meaning_en': 'walk',
          'story_role': 'none',
          'story_importance_en': '',
          'target_curriculum_level': 1,
          'curriculum_status': 'in_level',
          'matched_curriculum_level': 1,
          'learning_focus': 'target',
          'lookup_reason': '',
          'grammar_candidate_keys': ['decision.koto_ni_suru'],
        },
        {
          'text': 'ことにしました',
          'type': 'grammar',
          'reading': 'ことにしました',
          'lemma': 'ことにする',
          'lemma_reading': 'ことにする',
          'part_of_speech': 'grammar construction',
          'conjugation_form': 'polite past',
          'meaning_en': 'decided to',
          'story_role': 'none',
          'story_importance_en': '',
          'target_curriculum_level': 1,
          'curriculum_status': 'not_applicable',
          'matched_curriculum_level': null,
          'learning_focus': 'target',
          'lookup_reason': '',
          'grammar_candidate_keys': ['decision.koto_ni_suru'],
        },
      ],
      'grammar_overlays': [
        {
          'start': 0,
          'end': 9,
          'text': '歩くことにしました',
          'surface': '歩くことにしました',
          'grammar_candidate_key': 'decision.koto_ni_suru',
          'pattern': 'Vることにする',
          'meaning_en': 'decided to walk',
          'head_lemma': 'ことにする',
          'head_lemma_kana': 'ことにする',
          'form_label': 'decision construction, polite past',
          'explanation_en': 'ことにする marks a decision.',
          'components': [
            {
              'start': 0,
              'end': 2,
              'surface': '歩く',
              'lemma': '歩く',
              'lemma_kana': 'あるく',
              'function_en': 'action being decided',
            },
            {
              'start': 2,
              'end': 9,
              'surface': 'ことにしました',
              'lemma': 'ことにする',
              'lemma_kana': 'ことにする',
              'function_en': 'decision construction in polite past',
            },
          ],
        },
      ],
    });

    expect(annotation.segments.first.pinyin, 'あるく');
    expect(annotation.segments.first.lemma, '歩く');
    expect(annotation.segments.first.isJapaneseAnnotation, isTrue);
    expect(annotation.grammarOverlays.single.formLabel,
        'decision construction, polite past');
    expect(annotation.grammarOverlays.single.components.first.lemma, '歩く');
    final units = buildJapaneseDisplayUnits(annotation);
    expect(units.single.segment.text, '歩くことにしました');
    expect(units.single.segment.lemma, 'ことにする');
    expect(units.single.segment.meaningEn, 'decided to walk');
  });

  test('a genuine fixed collocation can include its noun and particle', () {
    Map<String, dynamic> segment(String text, String type, String lemma) => {
          'text': text,
          'type': type,
          'reading': text,
          'lemma': lemma,
          'lemma_reading': lemma,
          'part_of_speech': type,
          'conjugation_form': 'form',
          'meaning_en': lemma,
          'story_role': 'none',
          'story_importance_en': '',
          'grammar_candidate_keys': <String>[],
        };
    final stomach = segment('お腹', 'word', 'お腹')
      ..['dictionary_key'] = 'お腹'
      ..['dictionary_definition_en'] = 'stomach; belly';
    final annotation = AgentChapterAnnotation.fromJson({
      'text': 'お腹がすいて',
      'segments': [
        stomach,
        segment('が', 'particle', 'が'),
        segment('すいて', 'word', 'すく'),
      ],
      'grammar_overlays': [
        {
          'start': 0,
          'end': 6,
          'surface': 'お腹がすいて',
          'grammar_candidate_key': 'collocation.onaka_ga_suku',
          'pattern': 'お腹がすく',
          'meaning_en': 'was hungry, and…',
          'head_lemma': 'お腹がすく',
          'head_lemma_kana': 'おなかがすく',
          'form_label': 'fixed collocation; connective て-form',
          'explanation_en': 'The て-form connects this state to what follows.',
          'components': [
            {
              'start': 0,
              'end': 2,
              'surface': 'お腹',
              'lemma': 'お腹',
              'lemma_kana': 'おなか',
              'function_en': 'stomach',
            },
            {
              'start': 2,
              'end': 3,
              'surface': 'が',
              'lemma': 'が',
              'lemma_kana': 'が',
              'function_en': 'subject marker',
            },
            {
              'start': 3,
              'end': 6,
              'surface': 'すいて',
              'lemma': 'すく',
              'lemma_kana': 'すく',
              'function_en': 'become empty/hungry, connective て-form',
            },
          ],
        },
      ],
    });

    final unit = buildJapaneseDisplayUnits(annotation).single;
    expect(unit.segment.text, 'お腹がすいて');
    expect(unit.segment.lemma, 'お腹がすく');
    expect(unit.segment.dictionaryKey, isEmpty);
    expect(unit.grammar.components.first.dictionaryKey, isEmpty);
  });

  test('fixed-expression explanation makes full inflected collocation tappable', () {
    Map<String, dynamic> segment(String text, String type, String lemma) => {
          'text': text,
          'type': type,
          'reading': text,
          'lemma': lemma,
          'lemma_reading': lemma,
          'part_of_speech': type,
          'conjugation_form': 'form',
          'meaning_en': lemma,
          'story_role': 'none',
          'story_importance_en': '',
          'grammar_candidate_keys': <String>[],
        };
    final annotation = AgentChapterAnnotation.fromJson({
      'text': '目が回りました',
      'segments': [
        segment('目', 'word', '目'),
        segment('が', 'particle', 'が'),
        segment('回りました', 'word', '回る'),
      ],
      'grammar_overlays': [
        {
          'start': 0,
          'end': 7,
          'surface': '目が回りました',
          'grammar_candidate_key': 'me-ga-mawaru',
          'pattern': '目が回る',
          'meaning_en': 'felt dizzy',
          'head_lemma': '目が回る',
          'head_lemma_kana': 'めがまわる',
          'form_label': 'polite past',
          'explanation_en':
              'The fixed expression 目が回る means to become dizzy.',
          'components': [
            {
              'start': 0,
              'end': 1,
              'surface': '目',
              'lemma': '目',
              'lemma_kana': 'め',
              'function_en': 'eyes',
            },
            {
              'start': 1,
              'end': 2,
              'surface': 'が',
              'lemma': 'が',
              'lemma_kana': 'が',
              'function_en': 'subject marker',
            },
            {
              'start': 2,
              'end': 7,
              'surface': '回りました',
              'lemma': '回る',
              'lemma_kana': 'まわる',
              'function_en': 'polite past predicate',
            },
          ],
        },
      ],
    });

    final unit = buildJapaneseDisplayUnits(annotation).single;
    expect(unit.segment.text, '目が回りました');
    expect(unit.segment.lemma, '目が回る');
    expect(unit.segment.meaningEn, 'felt dizzy');
    expect(unit.segment.type, 'idiom');
    expect(unit.segment.partOfSpeech, 'fixed expression');
    expect(unit.grammar.components.map((item) => item.text),
        ['目', 'が', '回りました']);
  });

  test('conventional-expression explanation makes a collocation tappable', () {
    Map<String, dynamic> segment(String text, String type, String lemma) => {
          'text': text,
          'type': type,
          'reading': text,
          'lemma': lemma,
          'lemma_reading': lemma,
          'part_of_speech': type,
          'conjugation_form': 'form',
          'meaning_en': lemma,
          'story_role': 'none',
          'story_importance_en': '',
          'grammar_candidate_keys': <String>[],
        };
    final annotation = AgentChapterAnnotation.fromJson({
      'text': '腹が減り',
      'segments': [
        segment('腹', 'word', '腹'),
        segment('が', 'particle', 'が'),
        segment('減り', 'word', '減る'),
      ],
      'grammar_overlays': [
        {
          'start': 0,
          'end': 4,
          'surface': '腹が減り',
          'grammar_candidate_key': 'hara-ga-heru',
          'pattern': '腹が減る',
          'meaning_en': 'became hungry',
          'head_lemma': '腹が減る',
          'head_lemma_kana': 'はらがへる',
          'form_label': 'conjunctive form',
          'explanation_en':
              '腹が減る is a conventional expression meaning to become hungry.',
          'components': [
            {
              'start': 0,
              'end': 1,
              'surface': '腹',
              'lemma': '腹',
              'lemma_kana': 'はら',
              'function_en': 'stomach',
            },
            {
              'start': 1,
              'end': 2,
              'surface': 'が',
              'lemma': 'が',
              'lemma_kana': 'が',
              'function_en': 'subject marker',
            },
            {
              'start': 2,
              'end': 4,
              'surface': '減り',
              'lemma': '減る',
              'lemma_kana': 'へる',
              'function_en': 'decrease; become empty',
            },
          ],
        },
      ],
    });

    final unit = buildJapaneseDisplayUnits(annotation).single;
    expect(unit.segment.text, '腹が減り');
    expect(unit.segment.lemma, '腹が減る');
    expect(unit.segment.type, 'idiom');
  });

  test('does not merge an ordinary topic clause into one tap target', () {
    Map<String, dynamic> segment(String text, String type, String lemma) => {
          'text': text,
          'type': type,
          'reading': text,
          'lemma': lemma,
          'lemma_reading': lemma,
          'part_of_speech': type,
          'conjugation_form': 'form',
          'meaning_en': lemma,
          'story_role': 'none',
          'story_importance_en': '',
          'grammar_candidate_keys': <String>[],
        };
    final annotation = AgentChapterAnnotation.fromJson({
      'text': '吾輩は猫である',
      'segments': [
        segment('吾輩', 'word', '吾輩'),
        segment('は', 'particle', 'は'),
        segment('猫', 'word', '猫'),
        segment('である', 'grammar', 'だ'),
      ],
      'grammar_overlays': [
        {
          'start': 0,
          'end': 7,
          'surface': '吾輩は猫である',
          'grammar_candidate_key': 'topic_copula',
          'pattern': 'NはNである',
          'meaning_en': 'I am a cat',
          'head_lemma': 'だ',
          'head_lemma_kana': 'だ',
          'form_label': 'topic plus literary copula',
          'explanation_en': 'A complete ordinary topic sentence.',
          'components': [
            {
              'start': 0,
              'end': 2,
              'surface': '吾輩',
              'lemma': '吾輩',
              'lemma_kana': 'わがはい',
              'function_en': 'topic noun',
            },
            {
              'start': 2,
              'end': 3,
              'surface': 'は',
              'lemma': 'は',
              'lemma_kana': 'は',
              'function_en': 'topic marker',
            },
            {
              'start': 3,
              'end': 4,
              'surface': '猫',
              'lemma': '猫',
              'lemma_kana': 'ねこ',
              'function_en': 'predicate noun',
            },
            {
              'start': 4,
              'end': 7,
              'surface': 'である',
              'lemma': 'だ',
              'lemma_kana': 'だ',
              'function_en': 'literary copula',
            },
          ],
        },
      ],
    });

    expect(buildJapaneseDisplayUnits(annotation), isEmpty);
    expect(
      japaneseGrammarForSegment(
        segment: annotation.segments.first,
        start: 0,
        end: 2,
        overlays: annotation.grammarOverlays,
      ),
      isEmpty,
    );
    expect(
      japaneseGrammarForSegment(
        segment: annotation.segments.last,
        start: 4,
        end: 7,
        overlays: annotation.grammarOverlays,
      ).single.headLemma,
      'だ',
    );
  });

  test('benefactive chain includes its content verb as one display unit', () {
    Map<String, dynamic> segment(
      String text,
      String type,
      String lemma,
      String reading,
    ) =>
        {
          'text': text,
          'type': type,
          'reading': reading,
          'lemma': lemma,
          'lemma_reading': reading,
          'part_of_speech': type,
          'conjugation_form': 'form',
          'meaning_en': lemma,
          'story_role': 'none',
          'story_importance_en': '',
          'target_curriculum_level': 2,
          'curriculum_status': 'not_applicable',
          'matched_curriculum_level': null,
          'learning_focus': 'target',
          'lookup_reason': '',
          'grammar_candidate_keys': <String>[],
        };

    final annotation = AgentChapterAnnotation.fromJson({
      'text': '名前を付けてもらえなかった',
      'segments': [
        segment('名前', 'word', '名前', 'なまえ'),
        segment('を', 'particle', 'を', 'を'),
        segment('付け', 'word', '付ける', 'つけ'),
        segment('て', 'particle', 'て', 'て'),
        segment('もらえ', 'word', 'もらう', 'もらえ'),
        segment('なかっ', 'auxiliary', 'ない', 'なかっ'),
        segment('た', 'auxiliary', 'た', 'た'),
      ],
      'grammar_overlays': [
        {
          'start': 5,
          'end': 13,
          'surface': 'てもらえなかった',
          'grammar_candidate_key': 'te_moraenakatta',
          'pattern': 'て＋もらえる＋ない＋た',
          'meaning_en': 'could not get someone to attach it',
          'head_lemma': 'もらう',
          'head_lemma_kana': 'もらう',
          'form_label': 'negative past potential benefactive',
          'explanation_en': 'A received favor was not possible.',
          'components': [
            {
              'start': 0,
              'end': 1,
              'surface': 'て',
              'lemma': 'て',
              'lemma_kana': 'て',
              'function_en': 'link'
            },
            {
              'start': 1,
              'end': 4,
              'surface': 'もらえ',
              'lemma': 'もらう',
              'lemma_kana': 'もらう',
              'function_en': 'receive a favor'
            },
            {
              'start': 4,
              'end': 7,
              'surface': 'なかっ',
              'lemma': 'ない',
              'lemma_kana': 'ない',
              'function_en': 'negative'
            },
            {
              'start': 7,
              'end': 8,
              'surface': 'た',
              'lemma': 'た',
              'lemma_kana': 'た',
              'function_en': 'past'
            },
          ],
        },
      ],
    });

    final unit = buildJapaneseDisplayUnits(annotation).single;
    expect(unit.segment.text, '付けてもらえなかった');
    expect(unit.segment.lemma, '付ける');
    expect(unit.grammar.text, '付けてもらえなかった');
    expect(unit.grammar.components.first.text, '付け');
    expect(unit.firstSegment, 2);
    expect(unit.lastSegment, 6);
  });

  test('noun ga connective naku is exposed as one contextual display unit', () {
    Map<String, dynamic> segment(
      String text,
      String type,
      String lemma,
      String reading,
    ) =>
        {
          'text': text,
          'type': type,
          'reading': reading,
          'lemma': lemma,
          'lemma_reading': reading,
          'part_of_speech': type,
          'conjugation_form': text == 'なく'
              ? 'connective く-form'
              : 'non-inflecting',
          'meaning_en': lemma,
          'story_role': 'none',
          'story_importance_en': '',
          'grammar_candidate_keys': <String>[],
        };

    final annotation = AgentChapterAnnotation.fromJson({
      'text': '考えがなく',
      'segments': [
        segment('考え', 'word', '考え', 'かんがえ'),
        segment('が', 'particle', 'が', 'が'),
        segment('なく', 'word', 'ない', 'なく'),
      ],
      'grammar_overlays': [
        {
          'start': 0,
          'end': 5,
          'surface': '考えがなく',
          'grammar_candidate_key': 'n-ga-naku',
          'pattern': 'Nがなく',
          'meaning_en': 'with no thought or idea',
          'head_lemma': '考えがない',
          'head_lemma_kana': 'かんがえがない',
          'form_label': 'negative connective',
          'explanation_en':
              'なく is the connective く-form of ない and links onward.',
          'components': [
            {
              'start': 0,
              'end': 2,
              'surface': '考え',
              'lemma': '考え',
              'lemma_kana': 'かんがえ',
              'function_en': 'thought or idea',
            },
            {
              'start': 2,
              'end': 3,
              'surface': 'が',
              'lemma': 'が',
              'lemma_kana': 'が',
              'function_en': 'subject marker',
            },
            {
              'start': 3,
              'end': 5,
              'surface': 'なく',
              'lemma': 'ない',
              'lemma_kana': 'ない',
              'function_en': 'connective く-form',
            },
          ],
        },
      ],
    });

    final unit = buildJapaneseDisplayUnits(annotation).single;
    expect(unit.segment.text, '考えがなく');
    expect(unit.segment.meaningEn, 'with no thought or idea');
    expect(unit.grammar.headLemma, '考えがない');
    expect(unit.firstSegment, 0);
    expect(unit.lastSegment, 2);
  });

  test('same-surface overlay stays accessible on an already grouped predicate', () {
    final segment = AgentSegment.fromJson({
      'text': '置いてやれ',
      'type': 'word',
      'reading': 'おいてやれ',
      'lemma': '置く',
      'lemma_reading': 'おく',
      'part_of_speech': 'verb',
      'conjugation_form': 'benefactive imperative',
      'meaning_en': 'put it there as a favor',
      'story_role': 'none',
      'story_importance_en': '',
      'grammar_candidate_keys': ['benefactive-yaru'],
    });
    final overlay = AgentGrammarOverlay.fromJson({
      'start': 0,
      'end': 5,
      'surface': '置いてやれ',
      'grammar_candidate_key': 'benefactive-yaru',
      'pattern': 'Vてやる',
      'meaning_en': 'do V as a favor',
      'head_lemma': '置いてやる',
      'head_lemma_kana': 'おいてやる',
      'form_label': 'benefactive imperative',
      'explanation_en': 'やれ is the imperative of the benefactive helper.',
      'components': [
        {
          'start': 0,
          'end': 3,
          'surface': '置いて',
          'lemma': '置く',
          'lemma_kana': 'おく',
          'function_en': 'content action',
        },
        {
          'start': 3,
          'end': 5,
          'surface': 'やれ',
          'lemma': 'やる',
          'lemma_kana': 'やる',
          'function_en': 'benefactive helper in the imperative',
        },
      ],
    });

    expect(
      japaneseGrammarForSegment(
        segment: segment,
        start: 0,
        end: 5,
        overlays: [overlay],
      ).single.headLemma,
      '置いてやる',
    );
  });
}
