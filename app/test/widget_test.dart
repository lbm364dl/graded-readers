import 'package:flutter_test/flutter_test.dart';
import 'package:hsk_graded/main.dart';

void main() {
  testWidgets('App launches without errors', (WidgetTester tester) async {
    await tester.pumpWidget(const HskGradedApp());
    expect(find.text('HSK 分级阅读'), findsOneWidget);
  });
}
