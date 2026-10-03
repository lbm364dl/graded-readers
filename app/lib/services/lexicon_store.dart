import 'lexicon_store_base.dart';
import 'lexicon_store_unsupported.dart'
    if (dart.library.ffi) 'lexicon_store_native.dart'
    if (dart.library.js_interop) 'lexicon_store_web.dart' as platform;

export 'lexicon_store_base.dart';

final LexiconStore lexiconStore = platform.createLexiconStore();
