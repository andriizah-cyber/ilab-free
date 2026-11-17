"""
Робота з реєстратором у фоновому потоці:
  USBDevice      — USB (CH372B, pyusb + WinUSB/libusb)
  SerialDevice   — Bluetooth (модуль BM77, класичний SPP = COM-порт, pyserial)
  DemoDevice     — імітація без плати
Протокол однаковий; через Bluetooth до кожної команди додається 5 байтів 0x55
(так робить оригінальний BTSensor.dll).
Події в GUI через queue: ("status", str), ("sample", Sample), ("portinfo", PortInfo),
("error", str), ("connected", bool).
"""
import math
import queue
import random
import threading
import time

from . import protocol as P

DEVICE_IDS = [(0x4348, 0x5537), (0x1A86, 0x5537)]
EP_OUT, EP_IN = 0x02, 0x82
BT_PAD = b"\x55" * 5


def _backend():
    try:
        import libusb_package
        return libusb_package.get_libusb1_backend()
    except Exception:
        return None


def list_bt_ports():
    """[(COMx, опис)] Bluetooth COM-портів; вихідний порт до логера — першим."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    res = []
    for p in list_ports.comports():
        hw = (p.hwid or "").upper()
        if "BTHENUM" in hw or "bluetooth" in (p.description or "").lower():
            outgoing = "LOCALMFG" not in hw and "&000000000000_" not in hw
            res.append((p.device, p.description, outgoing))
    res.sort(key=lambda r: (not r[2], r[0]))
    return [(d, desc) for d, desc, _ in res]


class _Base:
    name = ""

    def __init__(self, events: "queue.Queue"):
        self.events = events
        self._cmd = queue.Queue()
        self._thread = None
        self._alive = False
        self.running = False
        self.interval = 1.0
        self._last_emit = 0.0

    def open(self):
        self._alive = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self):
        self._cmd.put(("close", None))
        self._alive = False
        if self._thread:
            self._thread.join(timeout=3)

    def start(self, interval=1.0):
        """interval, с: >=1 — потік плати (1/с) з проріджуванням; <1 — опитування A3."""
        self._cmd.put(("start", interval))

    def stop(self):
        self._cmd.put(("stop", None))

    def _emit(self, kind, data):
        self.events.put((kind, data))

    # ---------- спільна логіка сеансу (USB і Bluetooth) ----------
    def _session(self, send, read):
        """send(cmd, payload=b"", pause=0.05); read(timeout_ms) -> пакет | None."""
        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                p = read(100)
                if p:
                    self._handle(p)

        pump(0.5)
        send(P.CMD_CONNECT)
        pump(1.0)
        send(P.CMD_SENSOR_INFO)
        pump(2.0)
        self._emit("status", f"Підключено ({self.name})")
        self._emit("connected", True)

        last_extend = last_poll = 0.0
        last_info = time.time()
        got_extended = False
        while self._alive:
            try:
                cmd, arg = self._cmd.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd == "start":
                self.interval, self._last_emit = float(arg), 0.0
                send(P.CMD_SET_FREQ, b"\x03")       # на потік не впливає (завжди 1/с)
                pump(0.3)
                send(P.CMD_START)
                send(P.CMD_LAST_DATA)               # розширений формат з вбудованими датчиками
                self.running, got_extended, last_extend = True, False, time.time()
                self._emit("status", f"Вимірювання… ({self.name})")
            elif cmd == "stop":
                send(P.CMD_STOP)
                self.running = False
                self._emit("status", "Зупинено")
            elif cmd == "close":
                break
            fast = self.running and self.interval < 1.0
            if fast and time.time() - last_poll >= self.interval:
                last_poll = time.time()
                send(P.CMD_LAST_DATA, pause=0)
            elif self.running and not got_extended and time.time() - last_extend > 3:
                send(P.CMD_LAST_DATA)
                last_extend = time.time()
            elif not self.running and time.time() - last_info > 4:
                send(P.CMD_SENSOR_INFO)             # стежимо за під'єднанням датчиків
                last_info = time.time()
            p = read(max(5, int(self.interval * 500)) if fast else 200)
            if p:
                s = self._handle(p)
                if s is not None and s.builtin is not None:
                    got_extended = True
        if self.running:
            try:
                send(P.CMD_STOP)
            except Exception:
                pass

    def _handle(self, p):
        if p[0] in (P.PKT_DATA, P.PKT_LAST_DATA):
            s = P.parse_sample(p)
            if s is None or not self.running:
                return s
            fast = self.interval < 1.0
            # швидкий режим — відповіді B3 на опитування; звичайний — потік B2 з проріджуванням
            if (fast and p[0] == P.PKT_LAST_DATA) or (not fast and p[0] == P.PKT_DATA):
                now = time.time()
                if fast or now - self._last_emit >= self.interval - 0.2:
                    self._last_emit = now
                    self._emit("sample", s)
            return s
        if p[0] == P.PKT_SENSOR_INFO:
            info = P.parse_port_info(p)
            if info:
                self._emit("portinfo", info)
        return None


class USBDevice(_Base):
    name = "USB"

    def _run(self):
        try:
            import usb.core
            import usb.util
        except ImportError:
            self._emit("error", "Не встановлено pyusb: py -m pip install pyusb libusb-package")
            return
        be = _backend()
        dev = None
        for vid, pid in DEVICE_IDS:
            dev = usb.core.find(idVendor=vid, idProduct=pid, backend=be)
            if dev:
                break
        if dev is None:
            self._emit("error", "Реєстратор не знайдено по USB. Перевірте кабель і драйвер "
                                "WinUSB (Zadig) або виберіть підключення через Bluetooth.")
            return
        self._emit("status", "Знайдено реєстратор (USB), ініціалізація…")
        try:
            try:
                dev.reset()          # без reset плата іноді не відповідає
                time.sleep(1.5)
            except usb.core.USBError:
                pass
            try:
                dev.set_configuration()
            except usb.core.USBError:
                pass

            def send(cmd, payload=b"", pause=0.05):
                dev.write(EP_OUT, P.build(cmd, payload), timeout=1000)
                if pause:
                    time.sleep(pause)

            def read(timeout=200):
                try:
                    return P.extract(bytes(dev.read(EP_IN, 64, timeout=timeout)))
                except usb.core.USBTimeoutError:
                    return None
                except usb.core.USBError as e:
                    if getattr(e, "errno", None) in (110, 10060, None):
                        return None
                    raise

            self._session(send, read)
        except Exception as e:  # noqa
            self._emit("error", f"Помилка USB: {e}")
        finally:
            self.running = False
            try:
                usb.util.dispose_resources(dev)
            except Exception:
                pass
            self._emit("connected", False)


class SerialDevice(_Base):
    """Bluetooth SPP (або будь-який COM-порт) — потік байтів, пакети шукаємо за сумою."""

    def __init__(self, events, port):
        super().__init__(events)
        self.port = port
        self.name = f"Bluetooth {port}"

    def _run(self):
        try:
            import serial
        except ImportError:
            self._emit("error", "Не встановлено pyserial: py -m pip install pyserial")
            return
        ser, err = None, None
        self._emit("status", f"Підключення до {self.port}… (логер має бути увімкнений)")
        for _ in range(3):                 # перша спроба часто впирається в таймаут
            try:
                ser = serial.Serial(self.port, 115200, timeout=0.05, write_timeout=3)
                break
            except serial.SerialException as e:
                err = e
                time.sleep(1.0)
        if ser is None:
            self._emit("error", f"Не вдалося відкрити {self.port}: {err}\n"
                                "Перевірте, що логер увімкнений і спарений з ПК.")
            return
        buf = bytearray()
        pending = []
        try:
            time.sleep(1.0)
            ser.reset_input_buffer()

            def send(cmd, payload=b"", pause=0.05):
                ser.write(P.build(cmd, payload) + BT_PAD)
                if pause:
                    time.sleep(pause)

            def read(timeout=200):
                end = time.time() + timeout / 1000.0
                while True:
                    if pending:
                        return pending.pop(0)
                    chunk = ser.read(256)
                    if chunk:
                        buf.extend(chunk)
                        pk, used = P.scan_stream(bytes(buf))
                        del buf[:used]
                        pending.extend(pk)
                        continue
                    if time.time() >= end:
                        return None

            self._session(send, read)
        except Exception as e:  # noqa
            self._emit("error", f"Помилка Bluetooth: {e}")
        finally:
            self.running = False
            try:
                ser.close()
            except Exception:
                pass
            self._emit("connected", False)


class DemoDevice(_Base):
    """Імітація реєстратора: зручно для навчання без плати і для тестів."""
    name = "Демо"

    def _run(self):
        self._emit("status", "Демо-режим (без плати)")
        self._emit("connected", True)
        for info in (P.PortInfo(1, 64, "温度", "℃"), P.PortInfo(3, 64, "温度", "℃"),
                     P.PortInfo(14, 78, "心率", "BPM")):
            self._emit("portinfo", info)
        n, period = 0, 1.0
        next_t = time.time()
        while self._alive:
            try:
                cmd, arg = self._cmd.get(timeout=0.05)
                if cmd == "start":
                    self.running, n, period = True, 0, float(arg)
                    self._emit("status", "Вимірювання (демо)…")
                elif cmd == "stop":
                    self.running = False
                    self._emit("status", "Зупинено")
                elif cmd == "close":
                    break
            except queue.Empty:
                pass
            if self.running and time.time() >= next_t:
                next_t = time.time() + period
                n += 1
                warm = 2000 + 400 * (1 - math.exp(-n / 20))
                adc = [int(2300 + 60 * math.sin(n / 12)), 4095, 0, 0,
                       int(warm + random.gauss(0, 3)), 4095, 0, 0]
                bi = [int(300 + 200 * math.sin(n / 10) + random.gauss(0, 5)),
                      int(252 + random.gauss(0, 1)), int(400 + 50 * math.sin(n / 15)),
                      125, int(951 + random.gauss(0, 1)), int(72 + random.gauss(0, 2))]
                self._emit("sample", P.Sample(adc, bi, n))
        self._emit("connected", False)
