#!/usr/bin/env python3
"""پل پرداخت مایکت برای شب‌بان.
بعد از «npx cap add android» (و قبل از ساخت APK) اجرا می‌شه:  python3 apply_billing.py
- مجوز و queries مایکت رو به AndroidManifest اضافه می‌کنه
- AIDL مایکت + کلاس ShabbanBilling + MainActivity جدید رو می‌سازه
"""
import os, re, sys

ROOT = 'android'
APP = os.path.join(ROOT, 'app')
MAN = os.path.join(APP, 'src', 'main', 'AndroidManifest.xml')
JAVA = os.path.join(APP, 'src', 'main', 'java')

def die(m):
    print('BILLING ERROR: ' + m); sys.exit(1)

if not os.path.isfile(MAN): die('AndroidManifest.xml پیدا نشد؛ این اسکریپت باید بعد از cap add android اجرا بشه')

# ---------- 1) Manifest ----------
m = open(MAN, encoding='utf-8').read()
if 'ir.mservices.market.BILLING' not in m:
    add = ('    <uses-permission android:name="ir.mservices.market.BILLING" />\n'
           '    <queries>\n        <package android:name="ir.mservices.market" />\n    </queries>\n\n')
    i = m.find('<application')
    if i < 0: die('<application در Manifest پیدا نشد')
    # اگه قبلاً queries داریم، دوباره اضافه نشه
    if '<queries>' in m:
        add = '    <uses-permission android:name="ir.mservices.market.BILLING" />\n'
        m = m.replace('<queries>', '<queries>\n        <package android:name="ir.mservices.market" />', 1)
    m = m[:i] + add + '    ' + m[i:]
    open(MAN, 'w', encoding='utf-8').write(m)
    print('manifest ok')

# ---------- 2) gradle: aidl ----------
gp = os.path.join(APP, 'build.gradle')
g = open(gp, encoding='utf-8').read()
if 'aidl true' not in g and 'aidl = true' not in g:
    if re.search(r'buildFeatures\s*\{', g):
        g = re.sub(r'buildFeatures\s*\{', 'buildFeatures {\n        aidl true', g, 1)
    else:
        g, n = re.subn(r'(\nandroid\s*\{)', r'\1\n    buildFeatures {\n        aidl true\n    }', g, 1)
        if n != 1: die('بلوک android در build.gradle پیدا نشد')
    open(gp, 'w', encoding='utf-8').write(g)
    print('gradle ok')

# ---------- 3) پکیج برنامه ----------
pkg = None
for dp, dn, fn in os.walk(JAVA):
    if 'MainActivity.java' in fn:
        mdir = dp
        t = open(os.path.join(dp, 'MainActivity.java'), encoding='utf-8').read()
        mm = re.search(r'^\s*package\s+([\w.]+)\s*;', t, re.M)
        if mm: pkg = mm.group(1)
        break
if not pkg: die('MainActivity.java پیدا نشد')

# ---------- 4) AIDL ----------
ad = os.path.join(APP, 'src', 'main', 'aidl', 'com', 'android', 'vending', 'billing')
os.makedirs(ad, exist_ok=True)
open(os.path.join(ad, 'IInAppBillingService.aidl'), 'w', encoding='utf-8').write('''package com.android.vending.billing;

import android.os.Bundle;

interface IInAppBillingService {
    int isBillingSupported(int apiVersion, String packageName, String type);
    Bundle getSkuDetails(int apiVersion, String packageName, String type, in Bundle skusBundle);
    Bundle getBuyIntent(int apiVersion, String packageName, String sku, String type, String developerPayload);
    Bundle getPurchases(int apiVersion, String packageName, String type, String continuationToken);
    int consumePurchase(int apiVersion, String packageName, String purchaseToken);
}
''')

# ---------- 5) ShabbanBilling ----------
BILLING = r'''package __PKG__;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Bundle;
import android.os.IBinder;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;

import com.android.vending.billing.IInAppBillingService;

import java.util.ArrayList;

import org.json.JSONObject;

/** پل پرداخت مایکت (فقط محصول یک‌بارمصرف نشدنی «inapp»؛ هیچ‌وقت consume نمی‌شه). */
public class ShabbanBilling {
    public static final int RC = 47311;
    private final Activity act;
    private final WebView wv;
    private IInAppBillingService svc;
    private ServiceConnection conn;
    private boolean busy = false;
    private String sku = "", payload = "";

    public ShabbanBilling(Activity a, WebView w) { act = a; wv = w; }

    @JavascriptInterface
    public void purchase(final String s, final String uid) {
        act.runOnUiThread(new Runnable() { public void run() {
            if (busy) return;
            busy = true; sku = s == null ? "" : s; payload = uid == null ? "" : uid;
            withService(new Runnable() { public void run() { startBuy(); } });
        }});
    }

    private void withService(final Runnable next) {
        if (svc != null) { next.run(); return; }
        conn = new ServiceConnection() {
            public void onServiceConnected(ComponentName n, IBinder b) {
                svc = IInAppBillingService.Stub.asInterface(b);
                next.run();
            }
            public void onServiceDisconnected(ComponentName n) { svc = null; }
        };
        Intent it = new Intent("ir.mservices.market.InAppBillingService.BIND");
        it.setPackage("ir.mservices.market");
        boolean ok = false;
        try {
            if (act.getPackageManager().queryIntentServices(it, 0).isEmpty()) { fail("DIAG_NOSERVICE"); return; }
            ok = act.getApplicationContext().bindService(it, conn, Context.BIND_AUTO_CREATE);
            if (!ok) { fail("DIAG_BINDFALSE"); return; }
        } catch (Throwable e) { fail("DIAG_" + e.getClass().getSimpleName()); return; }
    }

    private void startBuy() {
        try {
            Bundle b = svc.getBuyIntent(3, act.getPackageName(), sku, "inapp", payload);
            int rc = b.getInt("RESPONSE_CODE", 6);
            if (rc == 7) { restore(); return; }
            if (rc != 0) { fail("BUY_" + rc); return; }
            PendingIntent pi = (PendingIntent) b.getParcelable("BUY_INTENT");
            if (pi == null) { fail("BUY_NOINTENT"); return; }
            act.startIntentSenderForResult(pi.getIntentSender(), RC, new Intent(), 0, 0, 0);
        } catch (Throwable e) { fail("BUY_EXC"); }
    }

    /** نتیجه‌ی صفحه‌ی پرداخت مایکت؛ true یعنی مال ماست. */
    public boolean handle(int req, int res, Intent data) {
        if (req != RC) return false;
        if (data == null) { fail(res == Activity.RESULT_CANCELED ? "CANCEL" : "NODATA"); return true; }
        int rc = 6;
        try { Object o = data.getExtras() == null ? null : data.getExtras().get("RESPONSE_CODE"); if (o instanceof Integer) rc = (Integer) o; else if (o instanceof Long) rc = ((Long) o).intValue(); } catch (Throwable e) {}
        if (res == Activity.RESULT_OK && rc == 0) {
            String d = data.getStringExtra("INAPP_PURCHASE_DATA");
            String sg = data.getStringExtra("INAPP_DATA_SIGNATURE");
            if (d == null || sg == null) { fail("NODATA"); return true; }
            deliver(d, sg);
        } else if (rc == 7) {
            withService(new Runnable() { public void run() { restore(); } });
        } else if (res == Activity.RESULT_CANCELED || rc == 1) {
            fail("CANCEL");
        } else fail("RES_" + rc);
        return true;
    }

    /** اگه قبلاً خریده (حساب جدید/نصب دوباره)، خریدش رو از مایکت می‌گیریم. */
    private void restore() {
        try {
            Bundle b = svc.getPurchases(3, act.getPackageName(), "inapp", null);
            if (b.getInt("RESPONSE_CODE", 6) == 0) {
                ArrayList<String> ds = b.getStringArrayList("INAPP_PURCHASE_DATA_LIST");
                ArrayList<String> ss = b.getStringArrayList("INAPP_DATA_SIGNATURE_LIST");
                if (ds != null && ss != null) {
                    for (int i = 0; i < ds.size() && i < ss.size(); i++) {
                        JSONObject o = new JSONObject(ds.get(i));
                        if (sku.equals(o.optString("productId"))) { deliver(ds.get(i), ss.get(i)); return; }
                    }
                }
            }
            fail("NOT_FOUND");
        } catch (Throwable e) { fail("RESTORE_EXC"); }
    }

    private void deliver(final String d, final String sg) {
        busy = false;
        final String js = "window.onShabbanPurchase&&window.onShabbanPurchase(" + JSONObject.quote(d) + "," + JSONObject.quote(sg) + ");";
        act.runOnUiThread(new Runnable() { public void run() { wv.evaluateJavascript(js, null); } });
    }

    private void fail(String code) {
        busy = false;
        final String js = "window.onShabbanBillingError&&window.onShabbanBillingError(" + JSONObject.quote(code) + ");";
        act.runOnUiThread(new Runnable() { public void run() { wv.evaluateJavascript(js, null); } });
    }

    public void destroy() {
        try { if (conn != null) act.getApplicationContext().unbindService(conn); } catch (Throwable e) {}
        svc = null;
    }
}
'''

MAIN = r'''package __PKG__;

import android.content.Intent;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    private ShabbanBilling billing;

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        try {
            billing = new ShabbanBilling(this, getBridge().getWebView());
            getBridge().getWebView().addJavascriptInterface(billing, "ShabbanBilling");
        } catch (Throwable e) { billing = null; }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (billing != null && billing.handle(requestCode, resultCode, data)) return;
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onDestroy() {
        if (billing != null) billing.destroy();
        super.onDestroy();
    }
}
'''
open(os.path.join(mdir, 'ShabbanBilling.java'), 'w', encoding='utf-8').write(BILLING.replace('__PKG__', pkg))
open(os.path.join(mdir, 'MainActivity.java'), 'w', encoding='utf-8').write(MAIN.replace('__PKG__', pkg))
print('billing bridge installed for package', pkg)
