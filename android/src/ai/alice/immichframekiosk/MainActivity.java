package ai.alice.immichframekiosk;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.app.Activity;
import android.os.Bundle;
import android.os.PowerManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkInfo;
import android.view.MotionEvent;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.os.Handler;
import java.util.Calendar;

public class MainActivity extends Activity {
    private static final String URL = "http://frameo-bridge.local:8098/frameo";
    private static final int RETRY_MS = 15000;
    private static final int MODE_CHECK_MS = 60000;
    private static final int WAKE_HOUR = 9;
    private static final int SLEEP_HOUR = 22;

    private FrameLayout root;
    private WebView webView;
    private TextView status;
    private Handler handler = new Handler();
    private PowerManager.WakeLock wakeLock;
    private boolean sleeping = false;

    private final Runnable retryLoad = new Runnable() {
        @Override public void run() { loadFrame(); }
    };

    private final Runnable modeCheck = new Runnable() {
        @Override public void run() {
            applyScheduleMode();
            handler.postDelayed(this, MODE_CHECK_MS);
        }
    };

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_FULLSCREEN, WindowManager.LayoutParams.FLAG_FULLSCREEN);

        PowerManager pm = (PowerManager)getSystemService(Context.POWER_SERVICE);
        wakeLock = pm.newWakeLock(PowerManager.SCREEN_BRIGHT_WAKE_LOCK | PowerManager.ACQUIRE_CAUSES_WAKEUP, "AI:ImmichFrame");

        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        webView = new WebView(this);
        webView.setBackgroundColor(Color.BLACK);
        webView.setVerticalScrollBarEnabled(false);
        webView.setHorizontalScrollBarEnabled(false);
        // Android WebView uses percent here; 1 means 1%, because of course it does.
        webView.setInitialScale(100);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(false);
        s.setAppCacheEnabled(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setSupportZoom(false);
        s.setLoadWithOverviewMode(false);
        s.setUseWideViewPort(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        webView.setWebChromeClient(new WebChromeClient());
        webView.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                if (!sleeping) {
                    status.setVisibility(View.GONE);
                    handler.postDelayed(new Runnable() {
                        @Override public void run() { status.setVisibility(View.GONE); hideSystemUi(); }
                    }, 3000);
                    hideSystemUi();
                }
            }
            @Override public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (!sleeping) {
                    showStatus("Immich frame offline. Retrying...\n" + description);
                    handler.removeCallbacks(retryLoad);
                    handler.postDelayed(retryLoad, RETRY_MS);
                }
            }
        });

        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(20);
        status.setPadding(24, 24, 24, 24);
        status.setBackgroundColor(Color.BLACK);

        root.addView(webView, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        root.addView(status, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT));
        setContentView(root);

        root.setOnTouchListener(new View.OnTouchListener() {
            @Override public boolean onTouch(View v, MotionEvent event) {
                hideSystemUi();
                return false;
            }
        });

        scheduleNextTransition();
        applyScheduleMode();
        handler.postDelayed(modeCheck, MODE_CHECK_MS);
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        scheduleNextTransition();
        applyScheduleMode();
    }

    @Override protected void onResume() {
        super.onResume();
        scheduleNextTransition();
        applyScheduleMode();
    }

    @Override protected void onPause() {
        if (webView != null) webView.pauseTimers();
        super.onPause();
    }

    @Override protected void onDestroy() {
        releaseWakeLock();
        super.onDestroy();
    }

    private void applyScheduleMode() {
        if (isSleepTime()) enterSleepMode(); else enterAwakeMode();
    }

    private void enterAwakeMode() {
        sleeping = false;
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE;
        getWindow().setAttributes(lp);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        acquireWakeLock();
        if (webView != null) {
            webView.setVisibility(View.VISIBLE);
            webView.resumeTimers();
        }
        hideSystemUi();
        loadFrame();
    }

    private void enterSleepMode() {
        sleeping = true;
        handler.removeCallbacks(retryLoad);
        releaseWakeLock();
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = 0.0f;
        getWindow().setAttributes(lp);
        if (webView != null) {
            webView.stopLoading();
            webView.setVisibility(View.GONE);
            webView.pauseTimers();
        }
        showStatus("Sleeping until 09:00");
        hideSystemUi();
    }

    private boolean isSleepTime() {
        int h = Calendar.getInstance().get(Calendar.HOUR_OF_DAY);
        return h >= SLEEP_HOUR || h < WAKE_HOUR;
    }

    private void scheduleNextTransition() {
        try {
            Calendar now = Calendar.getInstance();
            Calendar next = (Calendar)now.clone();
            int targetHour = isSleepTime() ? WAKE_HOUR : SLEEP_HOUR;
            next.set(Calendar.HOUR_OF_DAY, targetHour);
            next.set(Calendar.MINUTE, 0);
            next.set(Calendar.SECOND, 0);
            next.set(Calendar.MILLISECOND, 0);
            if (!next.after(now)) next.add(Calendar.DAY_OF_YEAR, 1);

            Intent intent = new Intent(this, AlarmReceiver.class);
            PendingIntent pi = PendingIntent.getBroadcast(this, 1001, intent, PendingIntent.FLAG_UPDATE_CURRENT);
            AlarmManager am = (AlarmManager)getSystemService(Context.ALARM_SERVICE);
            am.cancel(pi);
            am.setExact(AlarmManager.RTC_WAKEUP, next.getTimeInMillis(), pi);
        } catch (Exception ignored) {}
    }

    private void loadFrame() {
        if (sleeping) return;
        if (!networkLikelyUp()) {
            showStatus("Waiting for Wi-Fi...\n" + URL);
            handler.removeCallbacks(retryLoad);
            handler.postDelayed(retryLoad, RETRY_MS);
            return;
        }
        status.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(URL);
    }

    private boolean networkLikelyUp() {
        try {
            ConnectivityManager cm = (ConnectivityManager)getSystemService(Context.CONNECTIVITY_SERVICE);
            NetworkInfo ni = cm.getActiveNetworkInfo();
            return ni != null && ni.isConnected();
        } catch (Exception e) { return true; }
    }

    private void acquireWakeLock() {
        try { if (wakeLock != null && !wakeLock.isHeld()) wakeLock.acquire(); } catch (Exception ignored) {}
    }

    private void releaseWakeLock() {
        try { if (wakeLock != null && wakeLock.isHeld()) wakeLock.release(); } catch (Exception ignored) {}
    }

    private void showStatus(String text) {
        status.setText(text);
        status.setVisibility(View.VISIBLE);
    }

    private void hideSystemUi() {
        View decor = getWindow().getDecorView();
        decor.setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_FULLSCREEN |
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION |
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY |
            View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN |
            View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION |
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        );
    }
}
