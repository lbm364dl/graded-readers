import 'package:flutter/services.dart';
import 'package:sqlite3/wasm.dart';
import 'package:typed_data/typed_buffers.dart';

import 'lexicon_store_base.dart';
import 'lexicon_store_sql.dart';

LexiconStore createLexiconStore() => WebLexiconStore();

class WebLexiconStore extends SqlLexiconStore {
  Future<void>? _initializing;

  @override
  Future<void> initialize() {
    if (isReady) return Future.value();
    return _initializing ??= _open();
  }

  Future<void> _open() async {
    final wasmData = await rootBundle.load('assets/sqlite3.wasm');
    final wasmBytes = wasmData.buffer.asUint8List(
      wasmData.offsetInBytes,
      wasmData.lengthInBytes,
    );
    final sqlite = await WasmSqlite3.load(Uint8List.fromList(wasmBytes));

    final databaseData = await rootBundle.load('assets/lexicon.sqlite3');
    final databaseBytes = databaseData.buffer.asUint8List(
      databaseData.offsetInBytes,
      databaseData.lengthInBytes,
    );
    final fileSystem = InMemoryFileSystem();
    final file = Uint8Buffer()..addAll(databaseBytes);
    const path = '/lexicon.sqlite3';
    fileSystem.fileData[path] = file;
    sqlite.registerVirtualFileSystem(fileSystem, makeDefault: true);
    database = sqlite.open(path, mode: OpenMode.readOnly);
  }
}
