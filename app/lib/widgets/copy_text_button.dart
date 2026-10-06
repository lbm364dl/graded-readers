import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

Future<void> copyTextWithFeedback(
  BuildContext context, {
  required String text,
  required String confirmation,
}) async {
  await Clipboard.setData(ClipboardData(text: text));
  if (!context.mounted) return;
  final messenger = ScaffoldMessenger.maybeOf(context);
  messenger
    ?..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(confirmation),
        duration: const Duration(seconds: 1),
        behavior: SnackBarBehavior.floating,
      ),
    );
}

class CopyTextButton extends StatelessWidget {
  final String text;
  final String confirmation;
  final String tooltip;
  final double iconSize;
  final VisualDensity? visualDensity;

  const CopyTextButton({
    super.key,
    required this.text,
    required this.confirmation,
    this.tooltip = 'Copy',
    this.iconSize = 20,
    this.visualDensity,
  });

  @override
  Widget build(BuildContext context) {
    return IconButton(
      onPressed: () => copyTextWithFeedback(
        context,
        text: text,
        confirmation: confirmation,
      ),
      icon: Icon(Icons.copy_rounded, size: iconSize),
      tooltip: tooltip,
      visualDensity: visualDensity,
    );
  }
}
