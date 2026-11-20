package com.presenova.app;

import android.Manifest;
import android.app.Activity;
import android.app.DownloadManager;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.DownloadListener;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import androidx.webkit.WebMessageReplyProxy;
import androidx.credentials.Credential;
import androidx.credentials.CredentialManager;
import androidx.credentials.CustomCredential;
import androidx.credentials.GetCredentialRequest;
import androidx.credentials.GetCredentialResponse;
import androidx.credentials.CredentialManagerCallback;
import androidx.credentials.exceptions.GetCredentialCancellationException;
import androidx.credentials.exceptions.GetCredentialException;
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption;
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential;
import com.google.android.libraries.identity.googleid.GoogleIdTokenParsingException;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.util.Base64;
import android.graphics.Color;

import java.io.OutputStream;
import java.net.URLDecoder;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.List;
import java.util.concurrent.Executor;

public class MainActivity extends Activity {
    private static final String HOME = "https://presenova-frontend.onrender.com/";
    private static final int FILE_PICKER = 41;
    private static final int MEDIA_PERMISSION = 42;
    private WebView webView;
    private ProgressBar progress;
    private LinearLayout errorPanel;
    private ValueCallback<Uri[]> fileCallback;
    private PermissionRequest pendingWebPermission;
    private String[] pendingAndroidPermissions;
    private String[] pendingWebResources;
    private final ArrayDeque<PermissionRequest> webPermissionQueue = new ArrayDeque<>();
    private boolean pendingWebPermissionCanceled;
    private final Handler authHandler = new Handler(Looper.getMainLooper());
    private WebMessageReplyProxy pendingAuthReply;
    private CancellationSignal authCancellation;
    private boolean authInProgress;
    private final Runnable authTimeout = () -> finishGoogleSignIn(null, "timeout");

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(15, 15, 26));
        getWindow().setNavigationBarColor(Color.rgb(15, 15, 26));

        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        errorPanel = makeErrorPanel();
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(-1, dp(3), Gravity.TOP);
        root.addView(progress, progressParams);
        root.addView(errorPanel, new FrameLayout.LayoutParams(-1, -1));
        errorPanel.setVisibility(View.GONE);
        setContentView(root);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);

        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(webView, "PresenovaDownloads",
                new java.util.HashSet<>(java.util.Arrays.asList("https://presenova-frontend.onrender.com", "https://cerulean-pavlova-319933.netlify.app")),
                (view, message, sourceOrigin, isMainFrame, replyProxy) -> {
                    if (!isMainFrame || !isTrusted(sourceOrigin)) return;
                    try {
                        org.json.JSONObject payload = new org.json.JSONObject(message.getData());
                        saveBlob(payload.optString("name"), payload.optString("mime"), payload.getString("data"));
                    } catch (Exception e) { Toast.makeText(MainActivity.this, "Could not save download", Toast.LENGTH_LONG).show(); }
                });
            WebViewCompat.addWebMessageListener(webView, "PresenovaAuth",
                new java.util.HashSet<>(java.util.Arrays.asList("https://presenova-frontend.onrender.com", "https://cerulean-pavlova-319933.netlify.app")),
                (view, message, sourceOrigin, isMainFrame, replyProxy) -> {
                    if (!isMainFrame || !isTrusted(sourceOrigin) || !isTrusted(Uri.parse(view.getUrl() == null ? "" : view.getUrl()))) return;
                    if (!"google-sign-in".equals(message.getData())) return;
                    beginGoogleSignIn(replyProxy);
                });
        }
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri uri = req.getUrl();
                if (isTrusted(uri)) return false;
                if ("https".equalsIgnoreCase(uri.getScheme()) || "mailto".equalsIgnoreCase(uri.getScheme())) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, uri)); } catch (Exception ignored) { }
                }
                return true;
            }

            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                errorPanel.setVisibility(View.GONE);
                progress.setVisibility(View.VISIBLE);
            }

            @Override public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                installBlobDownloadHook();
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showError();
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                progress.setVisibility(value >= 100 ? View.GONE : View.VISIBLE);
            }

            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{
                    "application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/msword", "audio/*", "video/*", "text/*", "application/zip"
                });
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                startActivityForResult(pick, FILE_PICKER);
                return true;
            }

            @Override public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> requestWebPermission(request));
            }

            @Override public void onPermissionRequestCanceled(PermissionRequest request) {
                runOnUiThread(() -> {
                    if (pendingWebPermission == request) {
                        pendingWebPermissionCanceled = true;
                    } else {
                        webPermissionQueue.remove(request);
                    }
                });
            }
        });
        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, length) -> download(url, userAgent, contentDisposition, mimeType));

        if (state != null) webView.restoreState(state);
        else if (hasInternet()) webView.loadUrl(HOME);
        else showError();
    }

    private LinearLayout makeErrorPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(32), dp(24), dp(32), dp(24));
        panel.setBackgroundColor(Color.rgb(15, 15, 26));
        TextView title = new TextView(this);
        title.setText("You’re offline"); title.setTextColor(Color.WHITE); title.setTextSize(22); title.setGravity(Gravity.CENTER);
        TextView details = new TextView(this);
        details.setText("Check your internet connection and try again."); details.setTextColor(Color.LTGRAY); details.setTextSize(16); details.setGravity(Gravity.CENTER);
        TextView retry = new TextView(this);
        retry.setText("  Retry  "); retry.setTextColor(Color.WHITE); retry.setTextSize(16); retry.setGravity(Gravity.CENTER);
        retry.setBackgroundColor(Color.rgb(99, 102, 241));
        LinearLayout.LayoutParams button = new LinearLayout.LayoutParams(-2, dp(48)); button.topMargin = dp(20);
        panel.addView(title); panel.addView(details); panel.addView(retry, button);
        retry.setOnClickListener(v -> { if (hasInternet()) { panel.setVisibility(View.GONE); webView.loadUrl(HOME); } });
        return panel;
    }

    private void requestWebPermission(PermissionRequest request) {
        Uri origin = Uri.parse(request.getOrigin().toString());
        if (!isTrusted(origin)) { request.deny(); return; }
        if (pendingWebPermission != null) {
            webPermissionQueue.addLast(request);
            return;
        }
        processWebPermission(request);
    }

    private void processWebPermission(PermissionRequest request) {
        List<String> androidPerms = new ArrayList<>();
        List<String> grant = new ArrayList<>();
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)) {
                grant.add(resource); androidPerms.add(Manifest.permission.CAMERA);
            } else if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) {
                grant.add(resource); androidPerms.add(Manifest.permission.RECORD_AUDIO);
            }
        }
        if (grant.isEmpty()) {
            request.deny();
            processNextWebPermission();
            return;
        }
        String[] needed = androidPerms.stream().distinct().filter(p -> checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED).toArray(String[]::new);
        if (needed.length == 0) {
            request.grant(grant.toArray(new String[0]));
            processNextWebPermission();
            return;
        }
        pendingWebPermission = request;
        pendingWebPermissionCanceled = false;
        pendingAndroidPermissions = grant.toArray(new String[0]);
        pendingWebResources = grant.toArray(new String[0]);
        requestPermissions(needed, MEDIA_PERMISSION);
    }

    private void processNextWebPermission() {
        if (pendingWebPermission == null && !webPermissionQueue.isEmpty()) {
            processWebPermission(webPermissionQueue.removeFirst());
        }
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == MEDIA_PERMISSION && pendingWebPermission != null) {
            List<String> grantedResources = new ArrayList<>();
            if (pendingWebResources != null) {
                for (String resource : pendingWebResources) {
                    String permission = PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resource)
                        ? Manifest.permission.CAMERA
                        : PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)
                            ? Manifest.permission.RECORD_AUDIO
                            : null;
                    if (permission != null && checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED) {
                        grantedResources.add(resource);
                    }
                }
            }
            if (!pendingWebPermissionCanceled) {
                if (grantedResources.isEmpty()) pendingWebPermission.deny();
                else pendingWebPermission.grant(grantedResources.toArray(new String[0]));
            }
            pendingWebPermission = null; pendingAndroidPermissions = null;
            pendingWebResources = null;
            pendingWebPermissionCanceled = false;
            processNextWebPermission();
        }
    }

    @Override protected void onActivityResult(int code, int result, Intent data) {
        super.onActivityResult(code, result, data);
        if (code == FILE_PICKER && fileCallback != null) {
            Uri[] chosen = null;
            if (result == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int count = data.getClipData().getItemCount(); chosen = new Uri[count];
                    for (int i = 0; i < count; i++) chosen[i] = data.getClipData().getItemAt(i).getUri();
                } else if (data.getData() != null) chosen = new Uri[]{data.getData()};
            }
            fileCallback.onReceiveValue(chosen); fileCallback = null;
        }
    }

    private void download(String url, String userAgent, String disposition, String mime) {
        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            request.setMimeType(mime);
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) request.addRequestHeader("Cookie", cookie);
            request.addRequestHeader("User-Agent", userAgent);
            request.setTitle(safeName(disposition, mime));
            request.setDescription("Downloading presentation file");
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, safeName(disposition, mime));
            ((DownloadManager)getSystemService(DOWNLOAD_SERVICE)).enqueue(request);
            Toast.makeText(this, "Download started", Toast.LENGTH_SHORT).show();
        } catch (Exception e) { Toast.makeText(this, "Could not start download", Toast.LENGTH_LONG).show(); }
    }

    private void saveBlob(String name, String mime, String data) {
        try {
            String clean = name == null ? "presenova-download" : name.replaceAll("[^A-Za-z0-9._ -]", "_");
            byte[] bytes = Base64.decode(data, Base64.DEFAULT);
            ContentValues values = new ContentValues(); values.put(MediaStore.Downloads.DISPLAY_NAME, clean);
            values.put(MediaStore.Downloads.MIME_TYPE, mime == null ? "application/octet-stream" : mime);
            values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Presenova");
            Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (uri == null) throw new IllegalStateException("Download storage unavailable");
            try (OutputStream stream = getContentResolver().openOutputStream(uri)) { stream.write(bytes); }
            Toast.makeText(MainActivity.this, "Saved to Downloads/Presenova", Toast.LENGTH_LONG).show();
        } catch (Exception e) { Toast.makeText(MainActivity.this, "Could not save download", Toast.LENGTH_LONG).show(); }
    }

    private void beginGoogleSignIn(WebMessageReplyProxy replyProxy) {
        if (authInProgress) {
            replyAuthError(replyProxy, "in-progress");
            return;
        }
        if (BuildConfig.PRESENOVA_WEB_CLIENT_ID.trim().isEmpty()) {
            replyAuthError(replyProxy, "configuration");
            return;
        }
        authInProgress = true;
        pendingAuthReply = replyProxy;
        authCancellation = new CancellationSignal();
        authHandler.postDelayed(authTimeout, 60_000L);

        try {
            GetSignInWithGoogleOption option = new GetSignInWithGoogleOption.Builder(BuildConfig.PRESENOVA_WEB_CLIENT_ID).build();
            GetCredentialRequest request = new GetCredentialRequest.Builder().addCredentialOption(option).build();
            CredentialManager manager = CredentialManager.create(this);
            Executor callbackExecutor = getMainExecutor();
            manager.getCredentialAsync(this, request, authCancellation, callbackExecutor,
                new CredentialManagerCallback<GetCredentialResponse, GetCredentialException>() {
                    @Override public void onResult(GetCredentialResponse response) {
                        if (!authInProgress) return;
                        Credential credential = response.getCredential();
                        if (!(credential instanceof CustomCredential) ||
                            !GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL.equals(credential.getType())) {
                            finishGoogleSignIn(null, "credential");
                            return;
                        }
                        try {
                            String idToken = GoogleIdTokenCredential.createFrom(((CustomCredential)credential).getData()).getIdToken();
                            if (idToken == null || idToken.isEmpty() || idToken.length() > 16_384) {
                                finishGoogleSignIn(null, "credential");
                                return;
                            }
                            finishGoogleSignIn(idToken, null);
                        } catch (GoogleIdTokenParsingException e) {
                            finishGoogleSignIn(null, "credential");
                        }
                    }

                    @Override public void onError(GetCredentialException exception) {
                        if (exception instanceof GetCredentialCancellationException) finishGoogleSignIn(null, "cancelled");
                        else finishGoogleSignIn(null, "unavailable");
                    }
                });
        } catch (Exception e) {
            finishGoogleSignIn(null, "unavailable");
        }
    }

    private void finishGoogleSignIn(String idToken, String errorCode) {
        if (!authInProgress) return;
        authInProgress = false;
        authHandler.removeCallbacks(authTimeout);
        if ("timeout".equals(errorCode) && authCancellation != null) authCancellation.cancel();
        authCancellation = null;
        WebMessageReplyProxy reply = pendingAuthReply;
        pendingAuthReply = null;
        if (reply == null || webView.getUrl() == null || !isTrusted(Uri.parse(webView.getUrl()))) return;
        try {
            String payload = errorCode == null
                ? "{\"type\":\"success\",\"idToken\":" + org.json.JSONObject.quote(idToken) + "}"
                : "{\"type\":\"error\",\"code\":" + org.json.JSONObject.quote(errorCode) + "}";
            reply.postMessage(payload);
        } catch (RuntimeException e) {
            Toast.makeText(this, "Could not return Google sign-in result.", Toast.LENGTH_SHORT).show();
        }
    }

    private void replyAuthError(WebMessageReplyProxy reply, String code) {
        try {
            reply.postMessage("{\"type\":\"error\",\"code\":" + org.json.JSONObject.quote(code) + "}");
        } catch (RuntimeException e) {
            Toast.makeText(this, "Could not return Google sign-in result.", Toast.LENGTH_SHORT).show();
        }
    }

    private void installBlobDownloadHook() {
        String js = "(function(){if(window.__presenovaBlobHook||!window.PresenovaDownloads)return;window.__presenovaBlobHook=true;document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a');if(!a||!a.href||!a.href.startsWith('blob:'))return;e.preventDefault();fetch(a.href).then(function(r){return r.blob()}).then(function(b){var f=new FileReader();f.onload=function(){window.PresenovaDownloads.postMessage(JSON.stringify({name:a.download||'presenova-download',mime:b.type,data:f.result.split(',')[1]}))};f.readAsDataURL(b)}).catch(function(){})},true)})();";
        webView.evaluateJavascript(js, null);
    }

    private String safeName(String disposition, String mime) {
        String name = "presenova-download";
        if (disposition != null) {
            java.util.regex.Matcher matcher = java.util.regex.Pattern.compile("filename\\*?=(?:UTF-8''|\\\")?([^;\\\"]+)", java.util.regex.Pattern.CASE_INSENSITIVE).matcher(disposition);
            if (matcher.find()) try { name = URLDecoder.decode(matcher.group(1).replace("\\\"", ""), "UTF-8"); } catch (Exception ignored) { }
        }
        name = name.replaceAll("[^A-Za-z0-9._ -]", "_");
        if (!name.contains(".")) name += mime != null && mime.contains("pdf") ? ".pdf" : mime != null && mime.contains("presentation") ? ".pptx" : ".bin";
        return name;
    }

    private boolean isTrusted(Uri uri) {
        if (!"https".equalsIgnoreCase(uri.getScheme())) return false;
        String host = uri.getHost();
        return "presenova-frontend.onrender.com".equalsIgnoreCase(host)
            || "cerulean-pavlova-319933.netlify.app".equalsIgnoreCase(host);
    }
    private boolean hasInternet() {
        ConnectivityManager cm = (ConnectivityManager)getSystemService(Context.CONNECTIVITY_SERVICE);
        android.net.Network network = cm.getActiveNetwork();
        NetworkCapabilities caps = network == null ? null : cm.getNetworkCapabilities(network);
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }
    private void showError() { progress.setVisibility(View.GONE); errorPanel.setVisibility(View.VISIBLE); }
    private int dp(int value) { return (int)(value * getResources().getDisplayMetrics().density); }
    @Override protected void onSaveInstanceState(Bundle state) { webView.saveState(state); super.onSaveInstanceState(state); }
    @Override public void onBackPressed() { if (webView.canGoBack()) webView.goBack(); else super.onBackPressed(); }
    @Override protected void onDestroy() {
        authHandler.removeCallbacks(authTimeout);
        if (authCancellation != null) authCancellation.cancel();
        authInProgress = false; pendingAuthReply = null;
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        webView.destroy(); super.onDestroy();
    }
}
