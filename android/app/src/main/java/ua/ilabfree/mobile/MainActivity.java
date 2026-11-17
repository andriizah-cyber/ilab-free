package ua.ilabfree.mobile;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanRecord;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.ContentValues;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelUuid;
import android.provider.MediaStore;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * iLab Free Mobile: інтерфейс — та сама веб-сторінка (assets/index.html),
 * Bluetooth — нативний Android BLE (модуль логера Microchip BM77, «Transparent UART»).
 * Сторінка бачить звичний navigator.bluetooth завдяки assets/android_ble.js.
 */
@SuppressLint({"MissingPermission", "SetJavaScriptEnabled"})
public class MainActivity extends Activity {

    static final UUID SVC = UUID.fromString("49535343-fe7d-4ae5-8fa9-9fafd205e455");
    static final UUID TX = UUID.fromString("49535343-1e4d-4bd9-ba61-23c647249616");   // notify
    static final UUID RX = UUID.fromString("49535343-8841-43f4-a8d4-ecbe34729bb3");   // write
    static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");

    private WebView web;
    private BluetoothAdapter adapter;
    private BluetoothDevice device;
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic txChar, rxChar;
    private final Handler ui = new Handler(Looper.getMainLooper());

    private int connectId = -1;
    private Runnable afterPermission;
    private int permissionFailId = -1;

    // черга GATT-операцій: Android виконує лише одну за раз
    private static class Op { int id; Runnable run; Op(int i, Runnable r) { id = i; run = r; } }
    private final ArrayDeque<Op> ops = new ArrayDeque<>();
    private Op current;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        BluetoothManager bm = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
        adapter = bm != null ? bm.getAdapter() : null;

        web = new WebView(this);
        WebSettings ws = web.getSettings();
        ws.setJavaScriptEnabled(true);
        ws.setDomStorageEnabled(true);
        ws.setAllowFileAccess(true);
        web.setWebChromeClient(new WebChromeClient());   // діалоги confirm()
        web.addJavascriptInterface(new Bridge(), "AndroidBLE");
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
    }

    @Override
    protected void onDestroy() {
        if (gatt != null) { gatt.close(); gatt = null; }
        super.onDestroy();
    }

    // ---------- виклики JS ----------
    private void js(final String code) {
        ui.post(() -> web.evaluateJavascript(code, null));
    }

    private void cb(int id, boolean ok, String arg) {
        if (id < 0) return;
        js("window.__bleCb&&window.__bleCb(" + id + "," + ok + "," + JSONObject.quote(arg) + ")");
    }

    private static String toHex(byte[] b) {
        StringBuilder sb = new StringBuilder(b.length * 2);
        for (byte x : b) sb.append(String.format("%02x", x & 0xff));
        return sb.toString();
    }

    private static byte[] fromHex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    // ---------- дозволи ----------
    private String[] neededPermissions() {
        List<String> p = new ArrayList<>();
        if (Build.VERSION.SDK_INT >= 31) {
            p.add(Manifest.permission.BLUETOOTH_SCAN);
            p.add(Manifest.permission.BLUETOOTH_CONNECT);
        } else {
            p.add(Manifest.permission.ACCESS_FINE_LOCATION);
        }
        return p.toArray(new String[0]);
    }

    private boolean hasPermissions(String[] perms) {
        for (String p : perms)
            if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) return false;
        return true;
    }

    private void withPermissions(String[] perms, int failId, Runnable r) {
        if (hasPermissions(perms)) { r.run(); return; }
        afterPermission = r;
        permissionFailId = failId;
        requestPermissions(perms, 1);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        boolean ok = true;
        for (int r : results) ok &= r == PackageManager.PERMISSION_GRANTED;
        Runnable r = afterPermission;
        afterPermission = null;
        if (ok && r != null) r.run();
        else cb(permissionFailId, false, "Немає дозволу на Bluetooth. Дозвольте його в налаштуваннях застосунку.");
    }

    // ---------- пошук логера ----------
    private void doScan(final int id) {
        if (adapter == null || !adapter.isEnabled()) {
            cb(id, false, "Увімкніть Bluetooth на телефоні");
            return;
        }
        final BluetoothLeScanner scanner = adapter.getBluetoothLeScanner();
        if (scanner == null) { cb(id, false, "Bluetooth LE недоступний"); return; }
        final LinkedHashMap<String, BluetoothDevice> found = new LinkedHashMap<>();
        final LinkedHashMap<String, String> names = new LinkedHashMap<>();
        final ScanCallback scb = new ScanCallback() {
            @Override
            public void onScanResult(int type, ScanResult r) {
                BluetoothDevice d = r.getDevice();
                ScanRecord rec = r.getScanRecord();
                String n = rec != null ? rec.getDeviceName() : null;
                if (n == null) n = d.getName();
                boolean svc = rec != null && rec.getServiceUuids() != null
                        && rec.getServiceUuids().contains(new ParcelUuid(SVC));
                if (svc || (n != null && n.startsWith("TY"))) {
                    found.put(d.getAddress(), d);
                    names.put(d.getAddress(), n != null ? n : d.getAddress());
                }
            }
        };
        ScanSettings st = new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build();
        js("document.getElementById('status').textContent='Пошук логера (4 с)…'");
        scanner.startScan(null, st, scb);
        ui.postDelayed(() -> {
            try { scanner.stopScan(scb); } catch (Exception ignored) { }
            choose(id, found, names);
        }, 4000);
    }

    private void choose(final int id, final LinkedHashMap<String, BluetoothDevice> found,
                        final LinkedHashMap<String, String> names) {
        if (found.isEmpty()) {
            cb(id, false, "Логер не знайдено. Увімкніть його (синій світлодіод) і спробуйте ще раз.");
            return;
        }
        final List<String> addrs = new ArrayList<>(found.keySet());
        if (addrs.size() == 1) { select(id, found.get(addrs.get(0)), names.get(addrs.get(0))); return; }
        String[] labels = new String[addrs.size()];
        for (int i = 0; i < labels.length; i++) labels[i] = names.get(addrs.get(i)) + "  (" + addrs.get(i) + ")";
        new AlertDialog.Builder(this)
                .setTitle("Виберіть логер")
                .setItems(labels, (dlg, which) -> select(id, found.get(addrs.get(which)), names.get(addrs.get(which))))
                .setOnCancelListener(dlg -> cb(id, false, "cancel"))
                .show();
    }

    private void select(int id, BluetoothDevice d, String name) {
        device = d;
        cb(id, true, name);
    }

    // ---------- з'єднання ----------
    private void doConnect(final int id) {
        if (device == null) { cb(id, false, "Спочатку знайдіть логер"); return; }
        if (gatt != null) { gatt.close(); gatt = null; }
        connectId = id;
        gatt = device.connectGatt(this, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
        ui.postDelayed(() -> {
            if (connectId == id) { connectId = -1; cb(id, false, "Логер не відповідає"); disconnectGatt(); }
        }, 15000);
    }

    private void disconnectGatt() {
        if (gatt != null) gatt.disconnect();
    }

    private final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(final BluetoothGatt g, final int status, final int newState) {
            ui.post(() -> {
                if (newState == BluetoothProfile.STATE_CONNECTED) {
                    g.discoverServices();
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                    g.close();
                    if (gatt == g) gatt = null;
                    ops.clear();
                    if (current != null) { cb(current.id, false, "Відключено"); current = null; }
                    if (connectId >= 0) { cb(connectId, false, "Не вдалося підключитися (" + status + ")"); connectId = -1; }
                    js("window.__bleDisconnected&&window.__bleDisconnected()");
                }
            });
        }

        @Override
        public void onServicesDiscovered(final BluetoothGatt g, int status) {
            ui.post(() -> {
                BluetoothGattService s = g.getService(SVC);
                if (s == null) {
                    cb(connectId, false, "На пристрої немає сервісу UART");
                    connectId = -1;
                    g.disconnect();
                    return;
                }
                txChar = s.getCharacteristic(TX);
                rxChar = s.getCharacteristic(RX);
                if (!g.requestMtu(185)) finishConnect();
                ui.postDelayed(MainActivity.this::finishConnect, 2000);   // якщо MTU не відповість
            });
        }

        @Override
        public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            ui.post(MainActivity.this::finishConnect);
        }

        @Override
        public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor d, final int status) {
            ui.post(() -> opDone(status == BluetoothGatt.GATT_SUCCESS));
        }

        @Override
        public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, final int status) {
            ui.post(() -> opDone(status == BluetoothGatt.GATT_SUCCESS));
        }

        // Android 13+
        @Override
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c, byte[] value) {
            if (TX.equals(c.getUuid())) js("window.__bleData&&window.__bleData('" + toHex(value) + "')");
        }

        // Android 12 і старші
        @Override
        @SuppressWarnings("deprecation")
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c) {
            byte[] v = c.getValue();
            if (v != null && TX.equals(c.getUuid())) js("window.__bleData&&window.__bleData('" + toHex(v) + "')");
        }
    };

    private void finishConnect() {
        if (connectId >= 0) { cb(connectId, true, "ok"); connectId = -1; }
    }

    // ---------- черга операцій ----------
    private void enqueue(int id, Runnable r) {
        ops.add(new Op(id, r));
        next();
    }

    private void next() {
        if (current != null || ops.isEmpty()) return;
        final Op op = ops.poll();
        current = op;
        op.run.run();
        ui.postDelayed(() -> { if (current == op) opDone(false); }, 3000);
    }

    private void opDone(boolean ok) {
        Op op = current;
        current = null;
        if (op != null) cb(op.id, ok, ok ? "ok" : "Помилка запису Bluetooth");
        next();
    }

    @SuppressWarnings("deprecation")
    private void doEnableNotify(final int id) {
        enqueue(id, () -> {
            if (gatt == null || txChar == null) { opDone(false); return; }
            gatt.setCharacteristicNotification(txChar, true);
            BluetoothGattDescriptor d = txChar.getDescriptor(CCCD);
            if (d == null) { opDone(true); return; }
            d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
            if (!gatt.writeDescriptor(d)) opDone(false);
        });
    }

    @SuppressWarnings("deprecation")
    private void doWrite(final int id, final String hex) {
        enqueue(id, () -> {
            if (gatt == null || rxChar == null) { opDone(false); return; }
            rxChar.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            rxChar.setValue(fromHex(hex));
            if (!gatt.writeCharacteristic(rxChar)) opDone(false);
        });
    }

    // ---------- збереження CSV у «Завантаження» ----------
    private void saveCsv(String name, String text) {
        try {
            byte[] data = text.getBytes(StandardCharsets.UTF_8);
            if (Build.VERSION.SDK_INT >= 29) {
                ContentValues v = new ContentValues();
                v.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
                v.put(MediaStore.MediaColumns.MIME_TYPE, "text/csv");
                v.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS);
                Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                if (uri == null) throw new Exception("MediaStore");
                try (OutputStream o = getContentResolver().openOutputStream(uri)) {
                    if (o == null) throw new Exception("stream");
                    o.write(data);
                }
            } else {
                File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                //noinspection ResultOfMethodCallIgnored
                dir.mkdirs();
                try (FileOutputStream o = new FileOutputStream(new File(dir, name))) { o.write(data); }
            }
            Toast.makeText(this, "Збережено: Завантаження/" + name, Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "Не вдалося зберегти: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }

    // ---------- об'єкт для JavaScript ----------
    private class Bridge {
        @JavascriptInterface
        public void scan(final int id) {
            ui.post(() -> withPermissions(neededPermissions(), id, () -> doScan(id)));
        }

        @JavascriptInterface
        public void connect(final int id) {
            ui.post(() -> doConnect(id));
        }

        @JavascriptInterface
        public void enableNotify(final int id) {
            ui.post(() -> doEnableNotify(id));
        }

        @JavascriptInterface
        public void write(final int id, final String hex) {
            ui.post(() -> doWrite(id, hex));
        }

        @JavascriptInterface
        public void disconnect() {
            ui.post(MainActivity.this::disconnectGatt);
        }

        @JavascriptInterface
        public void saveCsv(final String name, final String text) {
            ui.post(() -> {
                if (Build.VERSION.SDK_INT < 29) {
                    String[] p = {Manifest.permission.WRITE_EXTERNAL_STORAGE};
                    withPermissions(p, -1, () -> MainActivity.this.saveCsv(name, text));
                } else {
                    MainActivity.this.saveCsv(name, text);
                }
            });
        }

        @JavascriptInterface
        public void keepScreen(final boolean on) {
            ui.post(() -> {
                if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            });
        }
    }
}
