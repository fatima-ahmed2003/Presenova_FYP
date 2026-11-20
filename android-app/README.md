# Presenova Android WebView app

Native Android wrapper for the live Presenova site at `https://cerulean-pavlova-319933.netlify.app/`. The app loads the live origin so website releases appear without rebuilding the APK. Firebase authentication runs through the website's configured Firebase Web SDK; WebView cookies and DOM storage are enabled for persistent browser sessions. No backend credentials or API keys are included here.

Features include origin-restricted WebView navigation, Android camera and microphone runtime permission prompts for trusted-site requests, Android document picker uploads, DownloadManager downloads, blob download saving into `Downloads/Presenova`, an offline/retry screen, loading progress, and Android back navigation. WebSocket support is provided by Android WebView's browser engine.

## Google Sign-In setup

The web app currently uses Firebase project `presenova-50ab7`. In Firebase Console for that project:

1. Open **Project settings → General → Your apps → Add app → Android**. Register package `com.presenova.app` (version `1.0`). If it already exists, select it instead of creating a duplicate.
2. Add the debug and release signing certificate SHA-1 and SHA-256 fingerprints to the Android app registration. For a local debug key on Windows, run `keytool -list -v -keystore "$env:USERPROFILE\.android\debug.keystore" -alias androiddebugkey -storepass android -keypass android`. For a release key, run `keytool -list -v -keystore <release-keystore> -alias <release-alias>` and enter its password when prompted. If distributing through Google Play App Signing, also register the SHA-1 and SHA-256 values shown in **Play Console → App integrity → App signing**.
3. In **Authentication → Sign-in method**, enable **Google** and save. Firebase provisions an Android OAuth client tied to `com.presenova.app` and the registered certificate fingerprints.
4. In that Google provider's web configuration, copy the **Web client ID** (the OAuth 2.0 client of type **Web application** ending in `.apps.googleusercontent.com`). Credential Manager must receive this Web client ID; do not use the Android OAuth client ID here.
5. Supply that public Web client ID as a Gradle property when building: `gradle assembleDebug -PpresenovaWebClientId=YOUR_WEB_CLIENT_ID`. To avoid putting it in shell history, add `presenovaWebClientId=YOUR_WEB_CLIENT_ID` to the developer's user-level Gradle properties file (`%USERPROFILE%\.gradle\gradle.properties`). The app build embeds only this public OAuth client identifier; it does not include an OAuth secret or Firebase service credential.

The Android side uses Credential Manager to obtain the Google ID token. The token is delivered only to the trusted Presenova HTTPS main frame, consumed immediately by Firebase Web Auth through `GoogleAuthProvider.credential()` and `signInWithCredential()`, and is never logged or persisted by the native wrapper. The Firebase project and backend user exchange remain the existing web application's configuration and flow.

## Build

Requires JDK 17 or newer, Android SDK Platform 35 and Build Tools 35, plus Gradle 8.9 or an Android Studio installation able to sync this project. Set the Web client ID property above, then run `gradle assembleDebug`; output is `app/build/outputs/apk/debug/app-debug.apk`.

The current workspace was checked for Java, Gradle and Android SDK. Java 18 is present. Gradle and Android SDK/ADB were not detected, so an APK could not be built in this environment.
