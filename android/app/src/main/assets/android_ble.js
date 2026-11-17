// Міст: імітує той мінімум Web Bluetooth API, яким користується index.html,
// поверх нативного Android BLE (об'єкт window.AndroidBLE з MainActivity.java).
(function () {
  "use strict";
  if (!window.AndroidBLE) return;
  var pending = {}, seq = 0, listeners = [], onDisc = [];

  // відповіді з Java: __bleCb(id, ok, текст)
  window.__bleCb = function (id, ok, arg) {
    var p = pending[id];
    delete pending[id];
    if (!p) return;
    if (ok) p.res(arg);
    else { var e = new Error(arg); if (arg === "cancel") e.name = "NotFoundError"; p.rej(e); }
  };
  // дані від логера (hex)
  window.__bleData = function (hex) {
    var b = new Uint8Array(hex.length / 2);
    for (var i = 0; i < b.length; i++) b[i] = parseInt(hex.substr(2 * i, 2), 16);
    var ev = { target: { value: new DataView(b.buffer) } };
    listeners.forEach(function (f) { f(ev); });
  };
  window.__bleDisconnected = function () {
    dev.gatt.connected = false;
    onDisc.forEach(function (f) { try { f(); } catch (e) {} });
  };

  function call(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    return new Promise(function (res, rej) {
      var id = ++seq;
      pending[id] = { res: res, rej: rej };
      window.AndroidBLE[method].apply(window.AndroidBLE, [id].concat(args));
    });
  }
  function hex(data) {
    var s = "";
    for (var i = 0; i < data.length; i++) s += (data[i] < 16 ? "0" : "") + data[i].toString(16);
    return s;
  }

  var tx = {
    startNotifications: function () { return call("enableNotify"); },
    addEventListener: function (_, f) { listeners.push(f); }
  };
  var rx = { writeValue: function (data) { return call("write", hex(data)); } };
  var service = {
    getCharacteristic: function (uuid) {
      return Promise.resolve(uuid.toLowerCase().indexOf("49535343-8841") === 0 ? rx : tx);
    }
  };
  var dev = {
    name: "", id: "android",
    gatt: {
      connected: false,
      connect: function () {
        return call("connect").then(function () {
          dev.gatt.connected = true;
          return { getPrimaryService: function () { return Promise.resolve(service); } };
        });
      },
      disconnect: function () { window.AndroidBLE.disconnect(); }
    },
    addEventListener: function (ev, f) { if (ev === "gattserverdisconnected") onDisc.push(f); }
  };

  Object.defineProperty(navigator, "bluetooth", {
    value: {
      requestDevice: function () {
        return call("scan").then(function (name) { dev.name = name; return dev; });
      }
    }
  });
  window.__androidSaveCsv = function (name, text) { window.AndroidBLE.saveCsv(name, text); };
  window.__androidKeepScreen = function (on) { window.AndroidBLE.keepScreen(!!on); };
})();
