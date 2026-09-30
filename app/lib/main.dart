import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'data.dart';
import 'models.dart';
import 'theme.dart';
import 'screens/home_screen.dart';
import 'services/dictionary_service.dart';

const _languageKey = 'selected_language';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final prefs = await SharedPreferences.getInstance();
  final savedLang = prefs.getString(_languageKey);
  final initialLang = Language.values.firstWhere(
      (language) => language.name == savedLang,
      orElse: () => Language.chinese);

  runApp(GradedReadersApp(initialLanguage: initialLang));

  // Let Flutter paint the shell before parsing lookup data. Agent-authored
  // annotations render without the dictionary; the lookup data is warm by the
  // time a reader normally reaches and taps a phrase.
  WidgetsBinding.instance.addPostFrameCallback((_) {
    unawaited(DictionaryService.instance.initialize(language: initialLang));
  });
}

class LanguageNotifier extends ValueNotifier<Language> {
  LanguageNotifier(super.language);

  Future<void> switchTo(Language language) async {
    if (value == language) return;

    // The shelf and reviewed annotations do not depend on dictionary loading.
    // Activate the language synchronously, then let the indexed lookup store
    // finish opening without replacing the whole app with a loading screen.
    unawaited(DictionaryService.instance.switchLanguage(language));
    value = language;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_languageKey, language.name);
  }
}

class LanguageScope extends InheritedNotifier<LanguageNotifier> {
  const LanguageScope({
    super.key,
    required LanguageNotifier notifier,
    required super.child,
  }) : super(notifier: notifier);

  static LanguageNotifier of(BuildContext context) {
    return context
        .dependOnInheritedWidgetOfExactType<LanguageScope>()!
        .notifier!;
  }
}

class GradedReadersApp extends StatefulWidget {
  final Language initialLanguage;
  const GradedReadersApp({super.key, required this.initialLanguage});

  @override
  State<GradedReadersApp> createState() => _GradedReadersAppState();
}

class _GradedReadersAppState extends State<GradedReadersApp> {
  late final _languageNotifier = LanguageNotifier(widget.initialLanguage);
  final _repo = ContentRepository();

  // Pre-built themes to avoid regeneration on switch
  static final _themes = {
    for (final lang in Language.values)
      lang: (
        light: AppTheme.lightThemeFor(lang),
        dark: AppTheme.darkThemeFor(lang),
      ),
  };

  @override
  void dispose() {
    _languageNotifier.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LanguageScope(
      notifier: _languageNotifier,
      child: ValueListenableBuilder<Language>(
        valueListenable: _languageNotifier,
        builder: (context, language, _) {
          final t = _themes[language]!;
          return MaterialApp(
            key: ValueKey(language),
            title: 'Graded Readers',
            debugShowCheckedModeBanner: false,
            theme: t.light,
            darkTheme: t.dark,
            themeMode: ThemeMode.system,
            home: HomeScreen(repo: _repo),
          );
        },
      ),
    );
  }
}
