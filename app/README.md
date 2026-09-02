# Graded Readers app

The reader catalog is generated exclusively from the repository-level
`content/` directory. Legacy `output/` and `readers/` files are intentionally
not visible in the app. Chinese word taps load reviewed agent sidecars from
`assets/annotations/`; missing sidecars render as non-interactive prose and
never fall back to the legacy dictionary tokenizer.

Rebuild the catalog and launch the web preview from the repository root:

```bash
python3 scripts/generate_app_content_json.py
cd app
flutter run -d chrome
```

For an in-progress local preview, `--allow-partial-annotations` publishes only
the reviewed sidecars currently available. The default command requires every
chapter annotation and fails closed when any are missing.

The current pilot catalog includes one reviewed chapter at each of HSK 1, HSK
2, and HSK 3. Other chapters and all legacy readers are absent. Each pilot
reports `Ch. 1/1`; taps use its reviewed agent sidecar.

Chinese and Japanese Noto Sans variable fonts are bundled under
`assets/fonts/`. The app does not fetch fonts at runtime, so glyph rendering is
deterministic offline.

Dictionary definitions, hanzi etymology, and historical glyphs are stored in
`assets/lexicon.sqlite3`. Taps perform indexed row lookups instead of decoding
the former whole-file JSON maps. Rebuild it after changing lookup source data:

```bash
python3 scripts/build_lexicon_database.py
```

On Android and other native targets the versioned database is copied to app
support storage once. The browser build queries the same database through the
bundled official SQLite WebAssembly runtime.

## Android checks

Run the device-level reader journey on an attached Android phone or emulator:

```bash
flutter test integration_test/android_reader_smoke_test.dart -d DEVICE_ID
```

It opens the clean HSK 1 pilot, checks the annotation UI, uses Android's native
clipboard, changes text size, and scrolls the chapter. Collect profile-mode HSK
3 frame timings with:

```bash
flutter drive --no-dds \
  --driver=test_driver/integration_test.dart \
  --target=integration_test/reader_performance_test.dart \
  --profile -d DEVICE_ID
```

The timing report is written to `build/integration_response_data.json`.

## Google Play internal testing

The first Play upload permanently reserves the Android `applicationId`. Confirm
the production package name before building or uploading the first bundle; the
current `com.hskgraded.hsk_graded` value is provisional.

Create a private upload key with `keytool`, then copy
`android/key.properties.example` to the ignored `android/key.properties` file
and fill in the key path, alias, and passwords. Keep the keystore and its
passwords backed up outside the repository. Google Play App Signing should hold
the distribution key; this local key is only the replaceable upload key.

Build the release bundle from this directory:

```bash
flutter analyze
flutter test
flutter build appbundle --release
```

The upload artifact is
`build/app/outputs/bundle/release/app-release.aab`. Every later Play upload must
use a greater Android build number (`version: ...+N` in `pubspec.yaml`, or
`--build-number N`).

In Play Console, create the app, enable Play App Signing, then use **Test and
release > Testing > Internal testing > Create new release**. Upload the AAB,
add release notes, add tester email addresses or a Google Group, publish the
internal release, and share its opt-in link. Internal testing supports up to
100 invited testers and can be used before the full store listing and Data
safety form are complete. A later closed, open, or production track requires
the remaining app-content and policy declarations.

## Getting Started

This project is a starting point for a Flutter application.

A few resources to get you started if this is your first Flutter project:

- [Lab: Write your first Flutter app](https://docs.flutter.dev/get-started/codelab)
- [Cookbook: Useful Flutter samples](https://docs.flutter.dev/cookbook)

For help getting started with Flutter development, view the
[online documentation](https://docs.flutter.dev/), which offers tutorials,
samples, guidance on mobile development, and a full API reference.
