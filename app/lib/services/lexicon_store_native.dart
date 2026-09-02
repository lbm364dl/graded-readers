import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:path_provider/path_provider.dart';
import 'package:sqlite3/sqlite3.dart';

import 'lexicon_store_base.dart';
import 'lexicon_store_sql.dart';

LexiconStore createLexiconStore() => NativeLexiconStore();

class NativeLexiconStore extends SqlLexiconStore {
  Future<void>? _initializing;

  @override
  Future<void> initialize() {
    if (isReady) return Future.value();
    return _initializing ??= _open();
  }

  Future<void> _open() async {
    final manifest = jsonDecode(
      await rootBundle.loadString('assets/lexicon_manifest.json'),
    ) as Map<String, dynamic>;
    final digest = (manifest['sha256'] as String).substring(0, 16);
    final expectedBytes = manifest['bytes'] as int;

    Directory supportDirectory;
    try {
      supportDirectory = await getApplicationSupportDirectory();
    } on MissingPluginException {
      supportDirectory = Directory(
        '${Directory.systemTemp.path}/hsk_graded_flutter_test',
      );
    }
    await supportDirectory.create(recursive: true);
    final target = File(
      '${supportDirectory.path}/graded_readers_lexicon_$digest.sqlite3',
    );

    if (!await target.exists() || await target.length() != expectedBytes) {
      final data = await rootBundle.load('assets/lexicon.sqlite3');
      final bytes = data.buffer.asUint8List(
        data.offsetInBytes,
        data.lengthInBytes,
      );
      final temporary = File('${target.path}.building');
      await temporary.writeAsBytes(bytes, flush: true);
      if (await target.exists()) await target.delete();
      await temporary.rename(target.path);
    }

    database = sqlite3.open(target.path, mode: OpenMode.readOnly);
  }
}
