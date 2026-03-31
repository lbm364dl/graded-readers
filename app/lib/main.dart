import 'package:flutter/material.dart';
import 'data.dart';
import 'theme.dart';
import 'screens/home_screen.dart';
import 'services/dictionary_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await DictionaryService.instance.initialize();
  runApp(const HskGradedApp());
}

class HskGradedApp extends StatelessWidget {
  const HskGradedApp({super.key});

  @override
  Widget build(BuildContext context) {
    final repo = ContentRepository();

    return MaterialApp(
      title: 'HSK 分级阅读',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.lightTheme,
      darkTheme: AppTheme.darkTheme,
      themeMode: ThemeMode.system,
      home: HomeScreen(repo: repo),
    );
  }
}
