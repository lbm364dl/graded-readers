import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'data.dart';
import 'models.dart';
import 'theme.dart';
import 'screens/home_screen.dart';
import 'services/dictionary_service.dart';
import 'services/etymology_service.dart';
import 'services/glyph_service.dart';

const _languageKey = 'selected_language';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  final prefs = await SharedPreferences.getInstance();
  final savedLang = prefs.getString(_languageKey);
  final initialLang = savedLang == 'japanese' ? Language.japanese : Language.chinese;

  await Future.wait([
    DictionaryService.instance.initialize(language: initialLang),
    EtymologyService.instance.initialize(),
    GlyphService.instance.initialize(),
    GoogleFonts.pendingFonts([
      GoogleFonts.notoSansJp(),
      GoogleFonts.notoSansSc(),
      GoogleFonts.notoSerifJp(),
      GoogleFonts.notoSerifSc(),
    ]),
  ]);
  runApp(GradedReadersApp(initialLanguage: initialLang));
}

class LanguageNotifier extends ValueNotifier<Language> {
  LanguageNotifier(super.language);

  bool _switching = false;
  bool get isSwitching => _switching;

  Future<void> switchTo(Language language) async {
    _switching = true;
    notifyListeners();
    await DictionaryService.instance.switchLanguage(language);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
        _languageKey, language == Language.japanese ? 'japanese' : 'chinese');
    await Future.delayed(const Duration(milliseconds: 50));
    _switching = false;
    value = language;
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
            home: _languageNotifier.isSwitching
                ? const Scaffold(
                    body: Center(child: CircularProgressIndicator()))
                : HomeScreen(repo: _repo),
          );
        },
      ),
    );
  }
}
