# One-tap AmneziaWG import from a web page: AmneziaVPN and AmneziaWG apps (2026-10-08)

Source read: `amnezia-vpn/amnezia-client` branch `dev` @ `3e9b70a5` (2026-10-05) plus tag `5.0.3.0` (latest release, 2026-09-18); `amneziawg-android` @ `ff15093b` (2026-09-21); `amneziawg-apple` @ HEAD (2026-08-23). **verified** = read in source. **reported** = issue/PR/web text. **unverified** = needs a device test.

## Bottom line

- **Only Android has a working one-tap URL path**: `vpn://<key>`. Every other platform of AmneziaVPN has none today.
- A PR that adds `vpn://` on iOS, macOS, Windows and Linux (#2597) has been open since 2026-05-13 and is **not merged**. It is not in release 5.0.3.0.
- **iOS has no one-tap path.** The best available is a file download opened via the share sheet, or the user pasting the key into the app.
- **Neither standalone AmneziaWG app (Android or iOS) registers any URL scheme.** The iOS app registers `.conf` document types only. The Android app registers nothing.
- There is no `https://…?url=` import form in AmneziaVPN. Only `vpn://<key>`, files and `--import <data>` on the desktop CLI exist.

## Platform matrix

| Platform / app | Method | Status | Evidence |
|---|---|---|---|
| **AmneziaVPN Android** | `vpn://<key>` tapped in a browser. A `BROWSABLE` VIEW filter `scheme=vpn host=*` sits on `ImportConfigActivity`, which forwards the full URI string to `AmneziaActivity`. It then calls `goToPageHome` → `extractConfigFromData` → `goToPageViewConfig`. | **verified** in source. Not tested on a device. | [AndroidManifest.xml L116-124](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/android/AndroidManifest.xml#L116-L124); [ImportConfigActivity.kt L62-77](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/android/src/org/amnezia/vpn/ImportConfigActivity.kt#L62-L77); [AmneziaActivity.kt L253-262](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/android/src/org/amnezia/vpn/AmneziaActivity.kt#L253-L262); [coreSignalHandlers.cpp L388-394](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/core/controllers/coreSignalHandlers.cpp#L388-L394) |
| AmneziaVPN Android, file | VIEW on `file:`/`content:` with `*.conf`, `*.vpn`, `*.cfg`. SEND of `text/plain` or `application/octet-stream` also works. | **verified** in source. | AndroidManifest.xml L103-150 (same file); ImportConfigActivity.kt L38-60 |
| AmneziaVPN Android, https URL to a config | Not supported. The file filter matches `file`/`content` schemes only, not `http(s)`. | **verified** (absence). | AndroidManifest.xml L126-132 |
| **AmneziaVPN iOS** | `vpn://` is **not registered**. `Info.plist.in` has no `CFBundleURLTypes`. The `openURL` handlers return early unless `url.isFileURL`. | **verified** (absence). | [ios/app/Info.plist.in](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/ios/app/Info.plist.in) (only `CFBundleDocumentTypes` at L207); [AmneziaSceneDelegateHooks.mm L15-40](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/platforms/ios/AmneziaSceneDelegateHooks.mm#L15-L40); QtAppDelegate.mm L35-57 |
| AmneziaVPN iOS, file | Document types `.vpn`, `.conf`, `.cfg`, `.ovpn`, `.backup` with `LSHandlerRank=Alternate`. The file text goes through `importConfigFromOutside` → `extractConfigFromData` → `goToPageViewConfig`. So it shows an import view, not a silent import. | **verified** in source. Share-sheet flow unverified on a device. | Info.plist.in L111-232; coreSignalHandlers.cpp L400-405 |
| AmneziaVPN macOS | No URL scheme. Same `CFBundleDocumentTypes` as iOS. | **verified** (absence). | [macos/app/Info.plist.in L157](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/macos/app/Info.plist.in#L157) |
| AmneziaVPN Windows / Linux | No scheme registration. The only inbound hook is the CLI option `--import <data>`. The `.desktop` file has no `MimeType=x-scheme-handler/…` and no `%u`. | **verified** (absence). | [amneziaApplication.cpp L46, L169-175](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/amneziaApplication.cpp#L169-L175); [deploy/data/linux/AmneziaVPN.desktop](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/deploy/data/linux/AmneziaVPN.desktop) |
| iOS / macOS / Windows / Linux `vpn://` | PR #2597 "Feat: Add deep link" adds it: `CFBundleURLTypes` `vpn` on iOS and macOS, a Windows HKCU `Software\Classes\vpn` key, and `x-scheme-handler/vpn` on Linux. It also adds a `vpn-deeplink-demo.html` test page. **Open, unmerged**, last updated 2026-09-30 (merge of `dev`). | **reported**. | [PR #2597](https://github.com/amnezia-vpn/amnezia-client/pull/2597); older requests [#978](https://github.com/amnezia-vpn/amnezia-client/issues/978), [#1067](https://github.com/amnezia-vpn/amnezia-client/issues/1067) |
| **AmneziaWG Android** (`org.amnezia.awg`) | Nothing registered. The manifest has no `<data>` element and no VIEW filter. Import is via the in-app SAF picker only. | **verified**; also matches [#2499](https://github.com/amnezia-vpn/amnezia-client/issues/2499). | [ui/src/main/AndroidManifest.xml](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/ui/src/main/AndroidManifest.xml) (no `<data`); [gradle.properties L3](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/gradle.properties#L3) |
| **AmneziaWG iOS** (`org.amnezia.awg`) | No URL scheme. Document types are `.conf` (`LSHandlerRank=Owner`), zip and text. `application(_:open:)` passes every URL to `importFromDisposableFile(url:)`, so it expects file URLs. Requested as [#2498](https://github.com/amnezia-vpn/amnezia-client/issues/2498) (open). | **verified**. | [UI/iOS/Info.plist L10-60](https://github.com/amnezia-vpn/amneziawg-apple/blob/master/Sources/WireGuardApp/UI/iOS/Info.plist); [AppDelegate.swift L35-38](https://github.com/amnezia-vpn/amneziawg-apple/blob/master/Sources/WireGuardApp/UI/iOS/AppDelegate.swift#L35-L38) |
| AmneziaWG macOS | No URL scheme. `.conf` document type only. | **verified** (absence). | `Sources/WireGuardApp/UI/macOS/Info.plist` |

### Version history for `vpn://` on Android

- **verified** by diffing manifests: `ImportConfigActivity` gained the `vpn` filter in commit `195bdb94` "Refactor import config" (2023-12-11).
- Tag `4.2.0.1` has it. Tag `4.1.0.1` does not.
- So it exists in every release since about 4.2 (Dec 2023 to Jan 2024). It is still present in 5.0.3.0.

### What the import code does with the payload

- `ImportController::extractConfigFromData` strips `vpn://`, base64url-decodes with `OmitTrailingEquals`, tries `qUncompress` and falls back to the raw bytes. It then detects the type, and AWG/WireGuard/OpenVPN/Xray/Amnezia JSON are all accepted. See [importController.cpp L176-215](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/core/controllers/selfhosted/importController.cpp#L176-L215). **verified**
- The file-open path hands over the raw file text, so a plain AWG `.conf` is accepted. A `vpn://` key inside a `.vpn` file would also work. **verified**
- **Never a silent import.** Every external path ends at `goToPageViewConfig`, where the user sees the config and confirms. **verified**
- Length limit: none in the code. Android passes the URI through `Intent` extras (Binder, about 1 MB). A key of 2 to 4 KB is far below any limit. **unverified** on a device, but low risk.
- The `#name` fragment is not stripped. On Android `intent.data.toString()` carries it into the base64 string. Qt may skip the invalid `#`, and zlib should ignore trailing bytes, but that is **unverified**. Do not rely on it. Drop the fragment in the redirect.

## Traps to design around

1. **iOS `backup` filename trap.** Both iOS handlers branch on `filePath.contains("backup")` and send the file to the backup-restore flow instead of the config import (AmneziaSceneDelegateHooks.mm L27, QtAppDelegate.mm L45). Never put "backup" anywhere in the path or filename, such as `/var/.../backup-user.conf`. **verified**
2. **iOS `.conf` is contested.** The AmneziaWG app claims `.conf` as `Owner`. AmneziaVPN claims it as `Alternate`. If both are installed, iOS may prefer AmneziaWG. The unique extension `.vpn` goes only to AmneziaVPN, so `.vpn` is the safer choice for AmneziaVPN users. The `.vpn` UTI is declared in Info.plist.in L113-135.
3. **Android user gesture.** Chrome blocks auto-redirects to custom schemes and `intent:` URLs without a user gesture. The PR #2597 demo page says as much: "click must be a user gesture". So `/go/amnezia` should end on a visible button, with at most an auto-attempt on top. **reported**
4. **Telegram's in-app browser.** `Telegram.WebApp.openLink` goes to the system browser by default, which is what we want. Whether Telegram's in-app browser would pass `vpn://` on is **unverified**. Keep the page usable if the scheme does nothing.
5. **Other apps may claim `vpn:`.** The `package=` pin in the `intent://` form avoids the chooser. A bare `vpn://` link may show a chooser or open another app. **unverified**

## Identifiers for fallback store links

| App | Android package | iOS App Store |
|---|---|---|
| AmneziaVPN | `org.amnezia.vpn` ([build.gradle.kts L40](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/android/build.gradle.kts#L40)); `https://play.google.com/store/apps/details?id=org.amnezia.vpn` | `https://apps.apple.com/app/id1600529900` (in [updateController.cpp L200](https://github.com/amnezia-vpn/amnezia-client/blob/3e9b70a5f1dd939b0df062881ac404536c678846/client/core/controllers/updateController.cpp#L200); that line is the macOS branch, and iOS uses a build-time fallback URL for the same app). **reported** for iOS. |
| AmneziaWG | `org.amnezia.awg` ([README](https://github.com/amnezia-vpn/amneziawg-android/blob/ff15093bf3856fea0947923474870b86efb682f0/README.md)) | `https://apps.apple.com/app/id6478942365` ([listing](https://apps.apple.com/us/app/amneziawg/id6478942365)), **reported** via web search |

Amnezia's own blog tells iOS 15 and 16 users to use the AmneziaWG app because AmneziaVPN needs a newer iOS ([blog](https://amnezia.org/blog/when-to-use-amneziawg-app-instead-of-amnezia-vpn)). **reported**

## Recommended implementation for `/go/amnezia`

The server needs the User-Agent, or the client needs `navigator.userAgent`, to pick the platform. The key and the file are signed so neither can be guessed.

**Android** (primary, one tap):
- Render a button, not an auto-redirect. Its `href` is:
  `intent://<KEY_WITHOUT_vpn://_AND_WITHOUT_#name>#Intent;scheme=vpn;package=org.amnezia.vpn;S.browser_fallback_url=https%3A%2F%2Fplay.google.com%2Fstore%2Fapps%2Fdetails%3Fid%3Dorg.amnezia.vpn;end`
- Base64url contains no `/` or `+`, so `intent://` plus the payload is safe.
- Optionally try the same link once on page load, behind the user's tap.
- Below the button, show "Copy key". The user can then paste it under Add server → "Insert key" (the `PageSetupWizardTextKey` screen, which accepts a `vpn://` line).
- Add a plain `vpn://<key>` link for non-Chrome browsers such as Firefox and Samsung.

**iOS** (no scheme exists, so be honest in the UI):
- Primary: "Download config". Serve the signed HTTPS URL with `Content-Type: application/octet-stream` and `Content-Disposition: attachment; filename="<name>.vpn"`.
  - The name must not contain "backup", and `.vpn` rather than `.conf` avoids the AmneziaWG `Owner` claim.
  - Instruct the user: tap the downloaded file, Share, then AmneziaVPN. That opens the config preview screen.
  - This depends on Safari's download UI plus the share sheet, so it is **unverified**. Test it on a real iPhone.
- Secondary: "Copy key" and then paste into AmneziaVPN via Add server → "Insert key".
- Add store links with the App Store id above.
- If PR #2597 is merged and shipped, add `vpn://<key>` as the first option on iOS. Gate it on an app-version check you do by hand, as the page cannot detect the app version.

**Desktop** (Windows, macOS, Linux): offer the `.vpn` download or "Copy key" only. Today there is no handler. After #2597 ships, `vpn://` would work.

**Standalone AmneziaWG users**: offer the `.conf` download only. Neither app has a deep link. On Android, long-press → Open with is not available because there is no VIEW filter, so the user must import through the app's picker. On iOS the `.conf` opens the app directly because it is the `Owner`.

**Other details**:
- Strip the `#name` fragment from the `vpn://` key before building the link.
- Keep `/go/<app>?u=<url>` for the other apps. For `amnezia` the `u` parameter should carry the key, or a signed token that resolves to it server-side. A signed token avoids putting the key in browser history and referrers.
- Re-check this report when #2597 merges. Watch the release notes for the first tag that contains `CFBundleURLTypes` in `ios/app/Info.plist.in`.

## Residual risks and open items

- No device was available, so none of the Android or iOS flows were run. The Android intent-filter and import path are **verified** by reading source. The Chrome gesture behaviour, the Telegram browser behaviour and the iOS share-sheet flow are **unverified**.
- The iOS App Store id for AmneziaVPN comes from the macOS branch of `openStorePage`. The iOS fallback URL is injected at build time (`CLIENT_IOS_STORE_URL_FALLBACK`) and was not seen. I assumed it is the same app id. Confirm before shipping.
- Behaviour of `#name` after the base64 string is not verified (see above).
