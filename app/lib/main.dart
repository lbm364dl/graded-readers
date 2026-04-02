import 'package:flutter/material.dart';
import 'data.dart';
import 'models.dart';
import 'theme.dart';
import 'screens/home_screen.dart';
import 'services/dictionary_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await DictionaryService.instance.initialize();
  runApp(const GradedReadersApp());
}

class LanguageNotifier extends ValueNotifier<Language> {
  LanguageNotifier() : super(Language.chinese);

  Future<void> switchTo(Language language) async {
    await DictionaryService.instance.switchLanguage(language);
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
  const GradedReadersApp({super.key});

  @override
  State<GradedReadersApp> createState() => _GradedReadersAppState();
}

class _GradedReadersAppState extends State<GradedReadersApp> {
  final _languageNotifier = LanguageNotifier();
  final _repo = ContentRepository();

  @override
  void dispose() {
    _languageNotifier.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LanguageScope(
      notifier: _languageNotifier,
      child: MaterialApp(
        title: 'Graded Readers',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.lightTheme,
        darkTheme: AppTheme.darkTheme,
        themeMode: ThemeMode.system,
        home: HomeScreen(repo: _repo),
      ),
    );
  }
}
